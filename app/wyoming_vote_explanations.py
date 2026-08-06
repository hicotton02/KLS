from __future__ import annotations

import json
import os
import re
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL

from app.db import (
    claim_legislative_media_explanation_scan,
    claim_legislative_media_transcription,
    count_bill_vote_explanations,
    get_legislative_media,
    init_db,
    list_bill_roll_calls,
    list_bill_roll_call_targets,
    list_legislative_media,
    list_roll_calls_for_session,
    mark_legislative_media_explanation_scan,
    open_pipeline_circuit_breaker,
    pipeline_circuit_breaker_is_open,
    replace_media_vote_explanations,
    update_legislative_media_transcript,
    upsert_bill_vote_explanation_scans,
    upsert_legislative_media,
)
from app.http_retry import get_with_retries
from app.ollama import OllamaClient
from app.settings import Settings, get_settings
from app.text_utils import iso_now
from app.wyoming_api import WyomingApiClient


Logger = Callable[[str], None]
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
YOUTUBE_CAPTION_CIRCUIT = "wyoming-youtube-captions"
TRANSCRIPTION_SAMPLE_RATE = 16_000
TRANSCRIPTION_CHUNK_SECONDS = 120
TRANSCRIPTION_RETRY_SECONDS = 60
MAX_TRANSCRIPTION_COMPRESSION_RATIO = 4.0
MAX_TRANSCRIPTION_WORDS_PER_MINUTE = 240
MAX_SHORT_TRANSCRIPTION_WORDS_PER_MINUTE = 260
MAX_REJECTED_TRANSCRIPTION_SEGMENT_RATIO = 0.65
SPARSE_REJECTED_TRANSCRIPTION_SEGMENT_RATIO = 0.60
MIN_WORDS_PER_MINUTE_AFTER_HEAVY_REJECTION = 80


@dataclass
class VoteExplanationBackfillStats:
    years: list[int]
    media_discovered: int = 0
    transcripts_added: int = 0
    transcripts_waiting: int = 0
    transcript_failures: int = 0
    media_scanned: int = 0
    explanations_found: int = 0
    bills_updated: int = 0


@dataclass
class TranscriptResult:
    status: str
    source: str | None = None
    segments: list[dict[str, Any]] | None = None
    title: str | None = None
    duration_seconds: int | None = None
    error: str | None = None


class TranscriptionChunkQualityError(RuntimeError):
    def __init__(self, message: str, *, elapsed: float, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.elapsed = elapsed
        self.diagnostics = diagnostics


def _youtube_id(source_url: str) -> str | None:
    parsed = urlparse(source_url)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if host not in YOUTUBE_HOSTS:
        return None
    if host == "youtu.be":
        return parsed.path.strip("/").split("/", 1)[0] or None
    query_id = parse_qs(parsed.query).get("v", [None])[0]
    if query_id:
        return str(query_id)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"live", "embed", "shorts"}:
        return parts[1]
    return None


def _normalize_media_source_url(source_url: object) -> str:
    value = str(source_url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    host = parsed.netloc.casefold().split(":", 1)[0]
    if parsed.scheme.casefold() == "s" and host in YOUTUBE_HOSTS:
        return parsed._replace(scheme="https").geturl()
    if value.startswith("//"):
        return f"https:{value}"
    if value.casefold().startswith(("wyoleg.gov/", "www.wyoleg.gov/")):
        return f"https://{value}"
    return value


def _source_kind(source_url: str) -> tuple[str, str | None]:
    video_id = _youtube_id(source_url)
    return ("youtube", video_id) if video_id else ("official_media", None)


def discover_wyoming_media(
    years: Iterable[int],
    *,
    settings: Settings | None = None,
    logger: Logger | None = None,
) -> int:
    selected_years = sorted({int(year) for year in years}, reverse=True)
    config = settings or get_settings()
    api = WyomingApiClient(config)
    discovered = 0
    now = iso_now()
    try:
        for year in selected_years:
            sessions = api.fetch_chamber_audio(year)
            year_count = 0
            for session in sessions:
                if not isinstance(session, dict):
                    continue
                session_date = str(session.get("sessionDate") or "")[:10]
                if not session_date:
                    continue
                special_session = session.get("specialSessionValue")
                for raw_media in session.get("chamberAudioFiles") or []:
                    if not isinstance(raw_media, dict):
                        continue
                    source_url = _normalize_media_source_url(raw_media.get("filePath"))
                    chamber = str(raw_media.get("chamber") or "").strip().upper()
                    if not source_url or chamber not in {"H", "S"}:
                        continue
                    source_kind, external_id = _source_kind(source_url)
                    upsert_legislative_media(
                        {
                            "state": "wy",
                            "year": year,
                            "special_session_value": special_session,
                            "session_date": session_date,
                            "session_day_number": session.get("sessionDaynumber"),
                            "chamber": chamber,
                            "time_of_day": str(raw_media.get("timeofDay") or "").strip().upper() or None,
                            "display_order": raw_media.get("displayOrder"),
                            "source_url": source_url,
                            "source_kind": source_kind,
                            "external_id": external_id,
                            "mime_type": raw_media.get("mimeType"),
                            "title": _default_media_title(year, session_date, chamber, raw_media.get("timeofDay")),
                            "source_synced_at": now,
                        }
                    )
                    discovered += 1
                    year_count += 1
            if logger:
                logger(f"Cataloged {year_count} official Wyoming recordings for {year}.")
    finally:
        api.close()
    return discovered


def _default_media_title(year: int, session_date: str, chamber: str, time_of_day: object) -> str:
    chamber_name = "House" if chamber == "H" else "Senate"
    day_part = str(time_of_day or "").strip().upper()
    suffix = f" {day_part}" if day_part else ""
    return f"Wyoming {chamber_name} floor session, {session_date}{suffix}"


def _ydl_options(*, download: bool = False, output_template: str | None = None) -> dict[str, Any]:
    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 90,
        "extractor_retries": 5,
        "fragment_retries": 5,
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
    }
    if not download:
        options["skip_download"] = True
    else:
        options["format"] = "bestaudio/best"
        if output_template:
            options["outtmpl"] = output_template
    return options


def _extract_youtube_info(source_url: str, *, download: bool = False, output_template: str | None = None) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with YoutubeDL(_ydl_options(download=download, output_template=output_template)) as ydl:
                info = ydl.extract_info(source_url, download=download)
                if not isinstance(info, dict):
                    raise RuntimeError("YouTube returned no media information")
                return info
        except Exception as exc:  # yt-dlp raises several networking exception types.
            last_error = exc
            if attempt == 3:
                break
            delay = 15 * attempt if "429" in str(exc) else 3 * attempt
            time.sleep(delay)
    raise RuntimeError(str(last_error or "YouTube media lookup failed"))


def _select_caption_track(info: dict[str, Any]) -> dict[str, Any] | None:
    for collection_name in ("subtitles", "automatic_captions"):
        collection = info.get(collection_name)
        if not isinstance(collection, dict):
            continue
        language_keys = [key for key in ("en-orig", "en") if key in collection]
        language_keys.extend(key for key in collection if key.startswith("en") and key not in language_keys)
        for language in language_keys:
            tracks = collection.get(language)
            if not isinstance(tracks, list):
                continue
            for extension in ("json3", "vtt"):
                for track in tracks:
                    if isinstance(track, dict) and track.get("ext") == extension and track.get("url"):
                        return track
    return None


def _clean_caption_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\n", " ")).strip()


def parse_youtube_json3(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_segments: list[dict[str, Any]] = []
    for event in payload.get("events") or []:
        if not isinstance(event, dict) or not isinstance(event.get("segs"), list):
            continue
        text = _clean_caption_text("".join(str(part.get("utf8") or "") for part in event["segs"] if isinstance(part, dict)))
        if not text:
            continue
        start = max(0, int(int(event.get("tStartMs") or 0) / 1000))
        duration = max(1, int(int(event.get("dDurationMs") or 1000) / 1000))
        if raw_segments and raw_segments[-1]["text"] == text and start <= raw_segments[-1]["end"] + 1:
            raw_segments[-1]["end"] = max(raw_segments[-1]["end"], start + duration)
            continue
        raw_segments.append({"start": start, "end": start + duration, "text": text})
    return _merge_transcript_segments(raw_segments)


def _merge_transcript_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for segment in segments:
        normalized = dict(segment)
        normalized["start"] = max(0, int(segment["start"]))
        normalized["end"] = max(normalized["start"] + 1, int(segment["end"]))
        if not merged:
            merged.append(normalized)
            continue
        current = merged[-1]
        can_merge = (
            normalized["start"] <= int(current["end"]) + 2
            and normalized["start"] - int(current["start"]) <= 18
            and len(str(current["text"])) + len(str(normalized["text"])) <= 500
        )
        if can_merge:
            current["text"] = _clean_caption_text(f"{current['text']} {normalized['text']}")
            current["end"] = max(int(current["end"]), normalized["end"])
        else:
            merged.append(normalized)
    return merged


def _youtube_captions(source_url: str, settings: Settings) -> TranscriptResult:
    info = _extract_youtube_info(source_url)
    title = str(info.get("title") or "").strip() or None
    duration = info.get("duration")
    duration_seconds = int(duration) if isinstance(duration, (int, float)) else None
    track = _select_caption_track(info)
    if track is None:
        return TranscriptResult(
            status="needs_transcription",
            title=title,
            duration_seconds=duration_seconds,
            error="No English captions were published for this recording.",
        )
    with httpx.Client(timeout=settings.request_timeout_seconds, follow_redirects=True) as client:
        response = get_with_retries(client, str(track["url"]), max_attempts=4)
        response.raise_for_status()
    if track.get("ext") != "json3":
        return TranscriptResult(
            status="needs_transcription",
            title=title,
            duration_seconds=duration_seconds,
            error="The available caption format could not be read.",
        )
    segments = parse_youtube_json3(response.json())
    if not segments:
        return TranscriptResult(
            status="needs_transcription",
            title=title,
            duration_seconds=duration_seconds,
            error="The published caption track was empty.",
        )
    return TranscriptResult(
        status="available",
        source="youtube_captions",
        segments=segments,
        title=title,
        duration_seconds=duration_seconds,
    )


def _download_media(media: dict[str, Any], destination: Path) -> Path:
    if media.get("source_kind") == "youtube":
        output_template = str(destination.with_suffix(".%(ext)s"))
        _extract_youtube_info(str(media["source_url"]), download=True, output_template=output_template)
        candidates = sorted(
            path for path in destination.parent.glob(f"{destination.stem}.*") if path.suffix != ".part"
        )
        if not candidates:
            raise RuntimeError("Downloaded YouTube audio could not be located")
        return candidates[0]
    output_path = destination.with_suffix(Path(urlparse(str(media["source_url"])).path).suffix or ".mp4")
    with httpx.Client(timeout=None, follow_redirects=True) as client:
        with client.stream("GET", str(media["source_url"])) as response:
            response.raise_for_status()
            with output_path.open("wb") as handle:
                for chunk in response.iter_bytes(1024 * 1024):
                    handle.write(chunk)
    return output_path


def _transcription_models_url(api_url: str) -> str:
    parsed = urlparse(api_url)
    marker = "/v1/audio/transcriptions"
    prefix = parsed.path.split(marker, 1)[0] if marker in parsed.path else ""
    return parsed._replace(path=f"{prefix}/v1/models", query="", fragment="").geturl()


def _transcription_service_choice(client: httpx.Client, api_url: str) -> str:
    response = client.get(_transcription_models_url(api_url))
    response.raise_for_status()
    payload = response.json()
    choices = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not choices[0].get("id"):
        raise RuntimeError("The transcription service advertised no available engine")
    return str(choices[0]["id"])


def _write_transcription_audio_chunks(
    audio_path: Path,
    workdir: Path,
    *,
    chunk_seconds: int = TRANSCRIPTION_CHUNK_SECONDS,
) -> list[dict[str, Any]]:
    import av

    workdir.mkdir(parents=True, exist_ok=True)
    chunk_samples = TRANSCRIPTION_SAMPLE_RATE * chunk_seconds
    chunks: list[dict[str, Any]] = []
    chunk_index = 0
    chunk_writer: wave.Wave_write | None = None
    chunk_path: Path | None = None
    chunk_written = 0
    total_written = 0

    def open_chunk() -> tuple[wave.Wave_write, Path]:
        nonlocal chunk_index
        chunk_index += 1
        path = workdir / f"chunk-{chunk_index:03d}.wav"
        writer = wave.open(str(path), "wb")
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(TRANSCRIPTION_SAMPLE_RATE)
        return writer, path

    def close_chunk() -> None:
        nonlocal chunk_writer, chunk_path, chunk_written
        if chunk_writer is None or chunk_path is None:
            return
        chunk_writer.close()
        if chunk_written:
            offset = (total_written - chunk_written) / TRANSCRIPTION_SAMPLE_RATE
            chunks.append(
                {
                    "path": chunk_path,
                    "offset_seconds": offset,
                    "duration_seconds": chunk_written / TRANSCRIPTION_SAMPLE_RATE,
                }
            )
        chunk_writer = None
        chunk_path = None
        chunk_written = 0

    def write_pcm(payload: bytes) -> None:
        nonlocal chunk_writer, chunk_path, chunk_written, total_written
        remaining = payload
        while remaining:
            if chunk_writer is None:
                chunk_writer, chunk_path = open_chunk()
            available_samples = chunk_samples - chunk_written
            take_bytes = min(len(remaining), available_samples * 2)
            take_bytes -= take_bytes % 2
            if take_bytes <= 0:
                close_chunk()
                continue
            chunk_writer.writeframesraw(remaining[:take_bytes])
            written_samples = take_bytes // 2
            chunk_written += written_samples
            total_written += written_samples
            remaining = remaining[take_bytes:]
            if chunk_written >= chunk_samples:
                close_chunk()

    container = av.open(str(audio_path))
    try:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TRANSCRIPTION_SAMPLE_RATE)
        for source_frame in container.decode(audio=0):
            for frame in resampler.resample(source_frame):
                write_pcm(bytes(frame.planes[0])[: frame.samples * 2])
        for frame in resampler.resample(None):
            write_pcm(bytes(frame.planes[0])[: frame.samples * 2])
    finally:
        container.close()
        close_chunk()
    if not chunks:
        raise RuntimeError("Downloaded media contained no decodable audio")
    return chunks


def _split_transcription_wav_chunk(
    chunk: dict[str, Any],
    workdir: Path,
    *,
    seconds: int = TRANSCRIPTION_RETRY_SECONDS,
) -> list[dict[str, Any]]:
    source_path = Path(chunk["path"])
    results: list[dict[str, Any]] = []
    with wave.open(str(source_path), "rb") as source:
        frames_per_chunk = source.getframerate() * seconds
        index = 0
        consumed = 0
        while payload := source.readframes(frames_per_chunk):
            index += 1
            output_path = workdir / f"{source_path.stem}-retry-{index:02d}.wav"
            with wave.open(str(output_path), "wb") as output:
                output.setnchannels(source.getnchannels())
                output.setsampwidth(source.getsampwidth())
                output.setframerate(source.getframerate())
                output.writeframes(payload)
            frame_count = len(payload) // max(1, source.getnchannels() * source.getsampwidth())
            results.append(
                {
                    "path": output_path,
                    "offset_seconds": float(chunk["offset_seconds"]) + consumed / source.getframerate(),
                    "duration_seconds": frame_count / source.getframerate(),
                }
            )
            consumed += frame_count
    return results


def _normalized_transcription_segment(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _transcription_chunk_quality_issue(
    *,
    duration_seconds: float,
    density: float,
    rejected_ratio: float,
    raw_segment_count: int,
) -> str | None:
    density_limit = (
        MAX_SHORT_TRANSCRIPTION_WORDS_PER_MINUTE
        if duration_seconds <= TRANSCRIPTION_RETRY_SECONDS
        else MAX_TRANSCRIPTION_WORDS_PER_MINUTE
    )
    if density > density_limit:
        return f"chunk speech density was {density:.1f} words per minute"
    too_many_rejected = raw_segment_count > 0 and (
        rejected_ratio > MAX_REJECTED_TRANSCRIPTION_SEGMENT_RATIO
        or (
            rejected_ratio > SPARSE_REJECTED_TRANSCRIPTION_SEGMENT_RATIO
            and density < MIN_WORDS_PER_MINUTE_AFTER_HEAVY_REJECTION
        )
    )
    if too_many_rejected:
        return f"chunk rejected {rejected_ratio:.0%} of transcript segments at {density:.1f} words per minute"
    return None


def _transcription_retry_delay(response: httpx.Response, attempt: int) -> float:
    if response.status_code == 429:
        try:
            return min(120.0, max(1.0, float(response.headers.get("Retry-After") or 30 * attempt)))
        except ValueError:
            return float(30 * attempt)
    return float(5 * attempt)


def _transcribe_api_chunk(
    client: httpx.Client,
    api_url: str,
    service_choice: str,
    chunk: dict[str, Any],
    *,
    logger: Logger | None = None,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    started = time.monotonic()
    payload: dict[str, Any] | None = None
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            audio_path = Path(chunk["path"])
            with audio_path.open("rb") as handle:
                response = client.post(
                    api_url,
                    files={"file": (audio_path.name, handle, "application/octet-stream")},
                    data={
                        "model": service_choice,
                        "language": "en",
                        "response_format": "verbose_json",
                        "vad_filter": "true",
                    },
                )
            if response.status_code in {429, 502, 503, 504} and attempt < 3:
                delay = _transcription_retry_delay(response, attempt)
                if logger:
                    logger(
                        f"Transcription service returned {response.status_code} for chunk at "
                        f"{int(float(chunk['offset_seconds']))} seconds; retrying in {delay:.0f} seconds."
                    )
                time.sleep(delay)
                continue
            response.raise_for_status()
            parsed = response.json()
            if not isinstance(parsed, dict):
                raise RuntimeError("The transcription service returned an invalid response")
            payload = parsed
            break
        except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(5 * attempt)
    if payload is None:
        raise RuntimeError(str(last_error or "Transcription request failed"))

    elapsed = time.monotonic() - started
    raw_segments = [item for item in (payload.get("segments") or []) if isinstance(item, dict)]
    segments: list[dict[str, Any]] = []
    rejected = 0
    recent_text: dict[str, float] = {}
    offset = float(chunk["offset_seconds"])
    for item in raw_segments:
        text = _clean_caption_text(item.get("text"))
        if not text:
            continue
        compression_ratio = float(item.get("compression_ratio") or 0)
        no_speech_probability = float(item.get("no_speech_prob") or 0)
        average_log_probability = float(item.get("avg_logprob") or 0)
        start = max(0.0, float(item.get("start") or 0))
        normalized = _normalized_transcription_segment(text)
        previous_start = recent_text.get(normalized)
        repeated = len(normalized.split()) >= 10 and previous_start is not None and start - previous_start <= 120
        if (
            compression_ratio > MAX_TRANSCRIPTION_COMPRESSION_RATIO
            or (no_speech_probability > 0.6 and average_log_probability < -1.0)
            or repeated
        ):
            rejected += 1
            continue
        recent_text[normalized] = start
        segments.append(
            {
                "start": max(0, int(offset + start)),
                "end": max(1, int(offset + float(item.get("end") or start + 1))),
                "text": text,
            }
        )

    duration_minutes = max(1 / 60, float(chunk["duration_seconds"]) / 60)
    word_count = len(re.findall(r"[a-z0-9]+", " ".join(item["text"] for item in segments).casefold()))
    density = word_count / duration_minutes
    rejected_ratio = rejected / max(1, len(raw_segments))
    diagnostics = {
        "duration_seconds": round(float(chunk["duration_seconds"]), 1),
        "raw_segments": len(raw_segments),
        "accepted_segments": len(segments),
        "rejected_segments": rejected,
        "rejected_ratio": round(rejected_ratio, 3),
        "words_per_minute": round(density, 1),
    }
    quality_issue = _transcription_chunk_quality_issue(
        duration_seconds=float(chunk["duration_seconds"]),
        density=density,
        rejected_ratio=rejected_ratio,
        raw_segment_count=len(raw_segments),
    )
    if quality_issue:
        raise TranscriptionChunkQualityError(quality_issue, elapsed=elapsed, diagnostics=diagnostics)
    return segments, elapsed, diagnostics


def _transcribe_api_chunk_with_quality_retry(
    client: httpx.Client,
    api_url: str,
    service_choice: str,
    chunk: dict[str, Any],
    retry_workdir: Path,
    *,
    chunk_index: int,
    logger: Logger | None = None,
) -> tuple[list[dict[str, Any]], float]:
    processing_seconds = 0.0
    try:
        segments, elapsed, _diagnostics = _transcribe_api_chunk(
            client,
            api_url,
            service_choice,
            chunk,
            logger=logger,
        )
        return segments, elapsed
    except TranscriptionChunkQualityError as exc:
        processing_seconds += exc.elapsed
        if logger:
            logger(f"Chunk {chunk_index} failed quality ({exc}); retrying as one-minute pieces.")

    segments = []
    for retry_chunk in _split_transcription_wav_chunk(chunk, retry_workdir):
        retry_segments, elapsed, _diagnostics = _transcribe_api_chunk(
            client,
            api_url,
            service_choice,
            retry_chunk,
            logger=logger,
        )
        processing_seconds += elapsed
        segments.extend(retry_segments)
    return segments, processing_seconds


def _transcribe_with_api(
    media: dict[str, Any],
    settings: Settings,
    *,
    logger: Logger | None = None,
) -> TranscriptResult:
    with tempfile.TemporaryDirectory(prefix="kls-wy-media-") as temp_dir:
        workdir = Path(temp_dir)
        media_path = _download_media(media, workdir / f"media-{media['id']}")
        chunks = _write_transcription_audio_chunks(media_path, workdir / "chunks")
        segments: list[dict[str, Any]] = []
        processing_seconds = 0.0
        with httpx.Client(
            timeout=settings.transcription_timeout_seconds,
            follow_redirects=True,
        ) as client:
            service_choice = _transcription_service_choice(client, settings.transcription_api_url)
            chunk_concurrency = max(
                1,
                min(len(chunks), int(getattr(settings, "transcription_chunk_concurrency", 4))),
            )
            with ThreadPoolExecutor(max_workers=chunk_concurrency) as executor:
                futures = [
                    executor.submit(
                        _transcribe_api_chunk_with_quality_retry,
                        client,
                        settings.transcription_api_url,
                        service_choice,
                        chunk,
                        workdir / "chunks",
                        chunk_index=index,
                        logger=logger,
                    )
                    for index, chunk in enumerate(chunks, start=1)
                ]
                try:
                    for future in as_completed(futures):
                        chunk_segments, elapsed = future.result()
                        processing_seconds += elapsed
                        segments.extend(chunk_segments)
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise

        if not segments:
            return TranscriptResult(status="failed", error="The transcription service returned no timestamped speech")
        segments.sort(key=lambda item: (int(item["start"]), int(item["end"])))
        duration_seconds = int(round(sum(float(chunk["duration_seconds"]) for chunk in chunks)))
        if logger:
            speed = duration_seconds / max(0.1, processing_seconds)
            logger(
                f"Transcribed media {media['id']} in {len(chunks)} chunks at {speed:.2f} times real time."
            )
        return TranscriptResult(
            status="available",
            source="speech_to_text",
            segments=_merge_transcript_segments(segments),
            duration_seconds=duration_seconds,
        )


@lru_cache(maxsize=2)
def _local_transcription_model(
    model_name: str,
    device: str,
    compute_type: str,
    cpu_threads: int,
) -> Any:
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        download_root=os.getenv("HF_HOME", "/models"),
    )


def _transcribe_locally(media: dict[str, Any], settings: Settings) -> TranscriptResult:
    with tempfile.TemporaryDirectory(prefix="kls-wy-media-") as temp_dir:
        media_path = _download_media(media, Path(temp_dir) / f"media-{media['id']}")
        model = _local_transcription_model(
            settings.local_transcription_model,
            settings.local_transcription_device,
            settings.local_transcription_compute_type,
            settings.local_transcription_threads,
        )
        raw_segments, info = model.transcribe(
            str(media_path),
            language="en",
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=True,
        )
        segments = [
            {
                "start": max(0, int(float(segment.start))),
                "end": max(1, int(float(segment.end))),
                "text": _clean_caption_text(segment.text),
            }
            for segment in raw_segments
            if _clean_caption_text(segment.text)
        ]
        if not segments:
            return TranscriptResult(status="failed", error="Local transcription returned no timestamped speech.")
        duration = getattr(info, "duration", None)
        return TranscriptResult(
            status="available",
            source="speech_to_text",
            segments=_merge_transcript_segments(segments),
            duration_seconds=int(float(duration)) if duration else int(segments[-1]["end"]),
        )


def fetch_media_transcript(
    media: dict[str, Any],
    settings: Settings,
    *,
    logger: Logger | None = None,
) -> TranscriptResult:
    try:
        source_url = _normalize_media_source_url(media.get("source_url"))
        parsed_source = urlparse(source_url)
        if parsed_source.scheme.casefold() not in {"http", "https"} or not parsed_source.netloc:
            return TranscriptResult(status="failed", error="The official recording source URL is invalid.")
        normalized_media = dict(media)
        normalized_media["source_url"] = source_url
        captions: TranscriptResult | None = None
        if normalized_media.get("source_kind") == "youtube":
            if pipeline_circuit_breaker_is_open(YOUTUBE_CAPTION_CIRCUIT):
                captions = TranscriptResult(
                    status="needs_transcription",
                    error="Published caption lookup is cooling down after YouTube rate limiting.",
                )
                if logger:
                    logger(f"Skipping published captions for media {media['id']} during the YouTube cooldown.")
            else:
                try:
                    captions = _youtube_captions(source_url, settings)
                except httpx.HTTPError as exc:
                    response = getattr(exc, "response", None)
                    if response is not None and response.status_code == 429:
                        open_pipeline_circuit_breaker(
                            YOUTUBE_CAPTION_CIRCUIT,
                            cooldown_seconds=int(getattr(settings, "youtube_caption_cooldown_seconds", 3600)),
                            reason=str(exc),
                        )
                    if logger:
                        logger(
                            f"Published captions were unavailable for media {media['id']} ({exc}); "
                            "falling back to transcription."
                        )
                    captions = TranscriptResult(
                        status="needs_transcription",
                        error=f"Published captions were temporarily unavailable: {exc}",
                    )
            if captions.status == "available":
                return captions
        if settings.transcription_api_url:
            return _transcribe_with_api(normalized_media, settings, logger=logger)
        if settings.local_transcription_model:
            return _transcribe_locally(normalized_media, settings)
        return captions or TranscriptResult(
            status="needs_transcription",
            error="No transcription service is configured.",
        )
    except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return TranscriptResult(status="failed", error=str(exc)[:1000])


def transcribe_wyoming_media(
    years: Iterable[int],
    *,
    settings: Settings | None = None,
    limit: int | None = None,
    force: bool = False,
    media_ids: Iterable[int] | None = None,
    logger: Logger | None = None,
) -> tuple[int, int, int]:
    selected_years = sorted({int(year) for year in years}, reverse=True)
    config = settings or get_settings()
    selected_year_set = set(selected_years)
    selected_media_ids = list(dict.fromkeys(int(media_id) for media_id in (media_ids or [])))
    if selected_media_ids:
        media_items = []
        for media_id in selected_media_ids:
            media = get_legislative_media(media_id)
            if media is None:
                raise ValueError(f"Wyoming legislative media {media_id} was not found")
            if str(media.get("state") or "").casefold() != "wy" or int(media.get("year") or 0) not in selected_year_set:
                raise ValueError(f"Legislative media {media_id} is not in the selected Wyoming years")
            if not force and media.get("transcript_status") not in {"pending", "needs_transcription"}:
                raise ValueError(
                    f"Legislative media {media_id} has status {media.get('transcript_status')}; use --force to retry it"
                )
            media_items.append(media)
        if limit is not None:
            media_items = media_items[: max(0, limit)]
    elif force:
        media_items = list_legislative_media(
            "wy",
            years=selected_years,
            transcript_statuses=None,
            limit=limit,
        )
    else:
        media_items = []

    def selected_media() -> Iterable[dict[str, Any]]:
        if selected_media_ids or force:
            yield from media_items
            return
        claimed = 0
        max_items = None if limit is None else max(0, int(limit))
        while max_items is None or claimed < max_items:
            media = claim_legislative_media_transcription("wy", years=selected_years)
            if media is None:
                break
            claimed += 1
            yield media

    added = waiting = failed = 0
    for media in selected_media():
        if logger and not selected_media_ids and not force:
            logger(f"Claimed media {media['id']} for transcription.")
        result = fetch_media_transcript(media, config, logger=logger)
        update_legislative_media_transcript(
            int(media["id"]),
            status=result.status,
            transcript_source=result.source,
            segments=result.segments,
            title=result.title,
            duration_seconds=result.duration_seconds,
            error=result.error,
        )
        if result.status == "available":
            added += 1
        elif result.status == "needs_transcription":
            waiting += 1
        else:
            failed += 1
        if logger:
            logger(
                f"{media['year']} {media['session_date']} {media['chamber']} {media.get('time_of_day') or ''}: "
                f"{result.status}."
            )
    return added, waiting, failed


def _normalized_words(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _number_words(value: int) -> str:
    ones = (
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    )
    tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
    if value < 20:
        return ones[value]
    if value < 100:
        return " ".join(part for part in (tens[value // 10], ones[value % 10] if value % 10 else "") if part)
    if value < 1000:
        return " ".join(
            part
            for part in (f"{ones[value // 100]} hundred", _number_words(value % 100) if value % 100 else "")
            if part
        )
    return ""


def _bill_alias_patterns(bill_num: str) -> list[re.Pattern[str]]:
    match = re.fullmatch(r"([A-Za-z]+)0*([0-9]+)", str(bill_num).replace(" ", ""))
    if not match:
        return []
    prefix, number = match.group(1).upper(), str(int(match.group(2)))
    spoken = {
        "HB": "house bill",
        "SF": "senate file",
        "HJ": "house joint resolution",
        "HJR": "house joint resolution",
        "SJ": "senate joint resolution",
        "SJR": "senate joint resolution",
    }.get(prefix)
    aliases = [rf"\b{re.escape(prefix.casefold())}\s*0*{number}\b"]
    if spoken:
        aliases.append(rf"\b{spoken}(?:\s+number)?\s+0*{number}\b")
        number_words = _number_words(int(number))
        if number_words:
            aliases.append(rf"\b{spoken}(?:\s+number)?\s+{re.escape(number_words)}\b")
    return [re.compile(alias, re.IGNORECASE) for alias in aliases]


def find_bill_sections(
    segments: list[dict[str, Any]],
    bill_nums: Iterable[str],
    *,
    max_seconds: int = 1800,
) -> list[dict[str, Any]]:
    occurrences: list[tuple[int, str]] = []
    candidates = {str(bill_num): _bill_alias_patterns(str(bill_num)) for bill_num in bill_nums}
    for index, segment in enumerate(segments):
        window = " ".join(str(item.get("text") or "") for item in segments[index : index + 3])
        normalized = _normalized_words(window)
        for bill_num, patterns in candidates.items():
            if any(pattern.search(normalized) for pattern in patterns):
                start = int(segment.get("start") or 0)
                if not occurrences or occurrences[-1] != (start, bill_num):
                    occurrences.append((start, bill_num))
    occurrences.sort()
    deduped: list[tuple[int, str]] = []
    for occurrence in occurrences:
        if deduped and occurrence[1] == deduped[-1][1] and occurrence[0] - deduped[-1][0] < 20:
            continue
        deduped.append(occurrence)

    sections: list[dict[str, Any]] = []
    for index, (start, bill_num) in enumerate(deduped):
        if sections and sections[-1]["bill_num"] == bill_num and start <= sections[-1]["end"]:
            continue
        next_other = next((timecode for timecode, other in deduped[index + 1 :] if other != bill_num), None)
        section_start = max(0, start - 45)
        section_end = section_start + max_seconds
        if next_other is not None:
            section_end = min(section_end, max(start + 120, next_other - 10))
        sections.append({"bill_num": bill_num, "start": section_start, "end": section_end})

    merged_by_bill: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        bill_sections = merged_by_bill.setdefault(str(section["bill_num"]), [])
        if bill_sections:
            previous = bill_sections[-1]
            merged_end = max(int(previous["end"]), int(section["end"]))
            if int(section["start"]) <= int(previous["end"]) and merged_end - int(previous["start"]) <= max_seconds:
                previous["end"] = merged_end
                continue
        bill_sections.append(dict(section))
    return sorted(
        (section for bill_sections in merged_by_bill.values() for section in bill_sections),
        key=lambda section: (int(section["start"]), str(section["bill_num"])),
    )


def _transcript_text(segments: list[dict[str, Any]], start: int, end: int) -> str:
    lines = []
    for segment in segments:
        segment_start = int(segment.get("start") or 0)
        if segment_start < start or segment_start > end:
            continue
        lines.append(f"[{segment_start}] {segment.get('text') or ''}")
    return "\n".join(lines)


def _locate_evidence(
    segments: list[dict[str, Any]],
    evidence_text: str,
    *,
    hint: int,
    start: int,
    end: int,
) -> tuple[int, int] | None:
    evidence_words = _normalized_words(evidence_text).split()
    if len(evidence_words) < 5:
        return None
    needle = " ".join(evidence_words[: min(8, len(evidence_words))])
    matches: list[tuple[int, int]] = []
    for index, segment in enumerate(segments):
        segment_start = int(segment.get("start") or 0)
        if segment_start < start or segment_start > end:
            continue
        window = segments[index : index + 8]
        haystack = _normalized_words(" ".join(str(item.get("text") or "") for item in window))
        if needle not in haystack:
            continue
        evidence_set = set(evidence_words)
        overlap = len(evidence_set.intersection(haystack.split())) / max(1, len(evidence_set))
        if overlap >= 0.7:
            matches.append((segment_start, int(window[-1].get("end") or segment_start + 1)))
    if not matches:
        return None
    return min(matches, key=lambda match: abs(match[0] - hint))


def _roll_call_score(roll_call: dict[str, Any]) -> tuple[int, str]:
    action = str(roll_call.get("action") or "").casefold()
    final_markers = ("third reading", "3rd reading", "passed", "failed", "concur", "final")
    return (sum(marker in action for marker in final_markers), str(roll_call.get("vote_date") or ""))


def _member_roll_call(
    roll_calls: list[dict[str, Any]],
    lawmaker_name: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    normalized_name = lawmaker_name.casefold().strip()
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for roll_call in roll_calls:
        for member in roll_call.get("members") or []:
            if str(member.get("legislator_name") or "").casefold().strip() != normalized_name:
                continue
            if str(member.get("vote_position") or "") not in {"yes", "no"}:
                continue
            candidates.append((roll_call, member))
    if not candidates:
        return None
    return max(candidates, key=lambda pair: _roll_call_score(pair[0]))


def seed_curated_wyoming_examples() -> int:
    media = next(
        (
            item
            for item in list_legislative_media("wy", years=[2026])
            if item.get("external_id") == "X45rOkJsR2g"
        ),
        None,
    )
    if media is None:
        return 0
    bill_roll_calls = list_bill_roll_calls(
        "wy",
        2026,
        "SF0101",
        special_session_value=media.get("special_session_value"),
    )
    examples = [
        {
            "name": "Art Washut",
            "start": 8464,
            "end": 8720,
            "summary": (
                "He said the bill was too vague for officers making fast decisions. He also opposed exposing "
                "agencies to a $50,000 civil penalty when people disagreed about what the bill meant."
            ),
            "evidence": (
                "Senate File 101 is ambiguous. And we're going to ask our peace officers to go out there and deal "
                "with an ambiguous piece of legislation in the real world when you got to make decisions quickly."
            ),
        },
        {
            "name": "Pam Thayer",
            "start": 8891,
            "end": 8944,
            "summary": (
                "She said she supports Second Amendment rights, but the bill was too vague and left too much open "
                "to interpretation. She chose to support local law enforcement."
            ),
            "evidence": (
                "I firmly support mine in your Second Amendment rights and the right to bear arms. But at this time, "
                "I have to pause. Also, as other speakers have said, I need to support the local law enforcement."
            ),
        },
        {
            "name": "Elissa Campbell",
            "start": 9146,
            "end": 9231,
            "summary": (
                "She said she supports the Second Amendment, but local officers warned that the bill could keep "
                "them from doing their jobs during domestic violence calls. She voted no to back local law enforcement."
            ),
            "evidence": (
                "They shared their concerns with me, especially in those situations of domestic violence, and the "
                "challenges we're going to put them in and prevent them from being able to do their job."
            ),
        },
    ]
    rows: list[dict[str, Any]] = []
    for example in examples:
        member_match = _member_roll_call(bill_roll_calls, str(example["name"]))
        if member_match is None:
            continue
        roll_call, member = member_match
        rows.append(
            {
                "state": "wy",
                "year": 2026,
                "special_session_value": media.get("special_session_value"),
                "bill_num": "SF0101",
                "roll_call_key": str(roll_call["roll_call_key"]),
                "member_key": str(member["member_key"]),
                "lawmaker_name": str(member["legislator_name"]),
                "vote_position": str(member["vote_position"]),
                "reason_summary": example["summary"],
                "evidence_text": example["evidence"],
                "source_url": str(media["source_url"]),
                "source_title": media.get("title"),
                "source_start_seconds": int(example["start"]),
                "source_end_seconds": int(example["end"]),
                "statement_date": "2026-03-05",
                "source_kind": "public_floor_statement",
                "review_status": "curated",
                "source_synced_at": iso_now(),
            }
        )
    replace_media_vote_explanations(int(media["id"]), rows, replace_non_curated=False)
    return len(rows)


def _media_roll_calls(media: dict[str, Any]) -> list[dict[str, Any]]:
    roll_calls = list_roll_calls_for_session(
        "wy",
        int(media["year"]),
        str(media["session_date"]),
        str(media["chamber"]),
        special_session_value=media.get("special_session_value"),
    )
    bucket = str(media.get("time_of_day") or "").upper()
    if bucket not in {"AM", "PM"}:
        return roll_calls
    selected: list[dict[str, Any]] = []
    for roll_call in roll_calls:
        raw_date = str(roll_call.get("vote_date") or "")
        try:
            hour = datetime.fromisoformat(raw_date).hour
        except ValueError:
            selected.append(roll_call)
            continue
        if (bucket == "AM" and hour < 12) or (bucket == "PM" and hour >= 12):
            selected.append(roll_call)
    return selected or roll_calls


def extract_media_vote_explanations(
    media: dict[str, Any],
    *,
    ollama: OllamaClient,
) -> list[dict[str, Any]]:
    segments = media.get("transcript_json")
    if not isinstance(segments, list) or not segments:
        return []
    roll_calls = _media_roll_calls(media)
    by_bill: dict[str, list[dict[str, Any]]] = {}
    for roll_call in roll_calls:
        by_bill.setdefault(str(roll_call["bill_num"]), []).append(roll_call)
    sections = find_bill_sections(segments, by_bill)
    results: dict[tuple[str, str], dict[str, Any]] = {}
    for section in sections:
        bill_num = str(section["bill_num"])
        bill_roll_calls = by_bill.get(bill_num) or []
        roster = sorted(
            {
                str(member.get("legislator_name") or "").strip()
                for roll_call in bill_roll_calls
                for member in (roll_call.get("members") or [])
                if str(member.get("legislator_name") or "").strip()
            },
            key=str.casefold,
        )
        if not roster:
            continue
        title = str((bill_roll_calls[0].get("catch_title") or bill_roll_calls[0].get("bill_title") or ""))
        transcript = _transcript_text(segments, int(section["start"]), int(section["end"]))
        if not transcript:
            continue
        extracted = ollama.extract_vote_explanations(
            bill_num=bill_num,
            bill_title=title,
            lawmakers=roster,
            transcript=transcript,
        )
        for statement in extracted:
            member_match = _member_roll_call(bill_roll_calls, str(statement["lawmaker_name"]))
            if member_match is None:
                continue
            roll_call, member = member_match
            evidence_location = _locate_evidence(
                segments,
                str(statement["evidence_text"]),
                hint=int(statement.get("start_seconds") or 0),
                start=int(section["start"]),
                end=int(section["end"]),
            )
            if evidence_location is None:
                continue
            start_seconds, end_seconds = evidence_location
            key = (bill_num, str(member["member_key"]))
            results[key] = {
                "state": "wy",
                "year": int(media["year"]),
                "special_session_value": media.get("special_session_value"),
                "bill_num": bill_num,
                "roll_call_key": str(roll_call["roll_call_key"]),
                "member_key": str(member["member_key"]),
                "lawmaker_name": str(member["legislator_name"]),
                "vote_position": str(member["vote_position"]),
                "reason_summary": str(statement["reason_summary"]),
                "evidence_text": str(statement["evidence_text"]),
                "source_url": str(media["source_url"]),
                "source_title": media.get("title"),
                "source_start_seconds": start_seconds,
                "source_end_seconds": end_seconds,
                "statement_date": media.get("session_date"),
                "source_kind": "public_floor_statement",
                "review_status": "publishable",
                "source_synced_at": iso_now(),
            }
    return list(results.values())


def scan_wyoming_media(
    years: Iterable[int],
    *,
    settings: Settings | None = None,
    limit: int | None = None,
    force: bool = False,
    logger: Logger | None = None,
) -> tuple[int, int]:
    selected_years = sorted({int(year) for year in years}, reverse=True)
    config = settings or get_settings()
    media_items = (
        list_legislative_media(
            "wy",
            years=selected_years,
            transcript_statuses=["available"],
            explanation_scan_statuses=None,
            limit=limit,
        )
        if force
        else []
    )

    def selected_media() -> Iterable[dict[str, Any]]:
        if force:
            yield from media_items
            return
        claimed = 0
        max_items = None if limit is None else max(0, int(limit))
        while max_items is None or claimed < max_items:
            media = claim_legislative_media_explanation_scan("wy", years=selected_years)
            if media is None:
                break
            claimed += 1
            yield media

    scanned = explanations = 0
    ollama = OllamaClient(config)
    try:
        for media in selected_media():
            if logger and not force:
                logger(f"Claimed media {media['id']} for explanation scanning.")
            try:
                rows = extract_media_vote_explanations(media, ollama=ollama)
                replace_media_vote_explanations(int(media["id"]), rows)
                mark_legislative_media_explanation_scan(int(media["id"]), status="complete")
                scanned += 1
                explanations += len(rows)
                if logger:
                    logger(
                        f"Scanned {media['year']} {media['session_date']} {media['chamber']} "
                        f"{media.get('time_of_day') or ''}: found {len(rows)} published reasons."
                    )
            except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
                mark_legislative_media_explanation_scan(int(media["id"]), status="failed", error=str(exc)[:1000])
                if logger:
                    logger(f"Explanation scan failed for media {media['id']}: {exc}")
    finally:
        ollama.close()
    return scanned, explanations


def refresh_bill_explanation_scans(years: Iterable[int]) -> int:
    selected_years = sorted({int(year) for year in years}, reverse=True)
    targets = list_bill_roll_call_targets("wy", selected_years)
    media_items = list_legislative_media("wy", years=selected_years)
    media_by_session: dict[tuple[int, int, str, str], list[dict[str, Any]]] = {}
    for media in media_items:
        key = (
            int(media["year"]),
            int(media["special_session_key"]),
            str(media["session_date"]),
            str(media["chamber"]),
        )
        media_by_session.setdefault(key, []).append(media)

    targets_by_bill: dict[tuple[int, int, str], dict[str, Any]] = {}
    for target in targets:
        bill_key = (int(target["year"]), int(target["special_session_key"]), str(target["bill_num"]))
        record = targets_by_bill.setdefault(
            bill_key,
            {
                "special_session_value": target.get("special_session_value"),
                "media": {},
            },
        )
        session_key = (
            int(target["year"]),
            int(target["special_session_key"]),
            str(target["session_date"]),
            str(target["chamber"]),
        )
        for media in media_by_session.get(session_key, []):
            record["media"][int(media["id"])] = media

    scan_rows: list[dict[str, Any]] = []
    for (year, _special_key, bill_num), record in targets_by_bill.items():
        bill_media = list(record["media"].values())
        media_total = len(bill_media)
        transcribed = sum(item.get("transcript_status") == "available" for item in bill_media)
        scanned = sum(item.get("explanation_scan_status") == "complete" for item in bill_media)
        if media_total == 0:
            status = "source_unavailable"
        elif scanned == media_total:
            status = "complete"
        elif scanned:
            status = "partial"
        elif transcribed:
            status = "pending"
        elif any(item.get("transcript_status") == "needs_transcription" for item in bill_media):
            status = "needs_transcription"
        else:
            status = "pending"
        scanned_at_values = [str(item.get("explanation_scanned_at")) for item in bill_media if item.get("explanation_scanned_at")]
        explanation_count = count_bill_vote_explanations(
            "wy",
            year,
            bill_num,
            special_session_value=record.get("special_session_value"),
        )
        scan_rows.append(
            {
                "state": "wy",
                "year": year,
                "special_session_value": record.get("special_session_value"),
                "bill_num": bill_num,
                "scan_status": status,
                "media_total": media_total,
                "media_transcribed": transcribed,
                "media_scanned": scanned,
                "explanation_count": explanation_count,
                "last_scanned_at": max(scanned_at_values) if scanned_at_values else None,
                "details": {"published_sources_only": True},
            }
        )
    upsert_bill_vote_explanation_scans(scan_rows)
    return len(scan_rows)


def backfill_wyoming_vote_explanations(
    *,
    years: Iterable[int] | None = None,
    stage: str = "all",
    limit_media: int | None = None,
    force: bool = False,
    media_ids: Iterable[int] | None = None,
    settings: Settings | None = None,
    logger: Logger | None = None,
) -> VoteExplanationBackfillStats:
    config = settings or get_settings()
    selected_years = sorted(
        {int(year) for year in (years or config.wyoming_explanation_years)},
        reverse=True,
    )
    if stage not in {"all", "discover", "transcribe", "extract", "status", "worker"}:
        raise ValueError(f"Unknown vote-explanation stage: {stage}")
    selected_media_ids = list(dict.fromkeys(int(media_id) for media_id in (media_ids or [])))
    if selected_media_ids and stage != "transcribe":
        raise ValueError("--media-id can only be used with --stage transcribe")
    init_db()
    stats = VoteExplanationBackfillStats(years=selected_years)
    if stage in {"all", "discover", "worker"}:
        stats.media_discovered = discover_wyoming_media(selected_years, settings=config, logger=logger)
        stats.explanations_found += seed_curated_wyoming_examples()
    if stage == "worker":
        iterations = max(1, int(limit_media or 1))
        for _ in range(iterations):
            added, waiting, failed = transcribe_wyoming_media(
                selected_years,
                settings=config,
                limit=1,
                force=force,
                logger=logger,
            )
            scanned, explanations = scan_wyoming_media(
                selected_years,
                settings=config,
                limit=1,
                force=force,
                logger=logger,
            )
            stats.transcripts_added += added
            stats.transcripts_waiting += waiting
            stats.transcript_failures += failed
            stats.media_scanned += scanned
            stats.explanations_found += explanations
            if added + waiting + failed + scanned == 0:
                break
    if stage in {"all", "transcribe"}:
        added, waiting, failed = transcribe_wyoming_media(
            selected_years,
            settings=config,
            limit=limit_media,
            force=force,
            media_ids=selected_media_ids,
            logger=logger,
        )
        stats.transcripts_added = added
        stats.transcripts_waiting = waiting
        stats.transcript_failures = failed
    if stage in {"all", "extract"}:
        scanned, explanations = scan_wyoming_media(
            selected_years,
            settings=config,
            limit=limit_media,
            force=force,
            logger=logger,
        )
        stats.media_scanned = scanned
        stats.explanations_found = explanations
    stats.bills_updated = refresh_bill_explanation_scans(selected_years)
    return stats
