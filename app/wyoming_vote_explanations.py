from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from app.db import (
    count_bill_vote_explanations,
    init_db,
    list_bill_roll_calls,
    list_bill_roll_call_targets,
    list_legislative_media,
    list_roll_calls_for_session,
    mark_legislative_media_explanation_scan,
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
                    source_url = str(raw_media.get("filePath") or "").strip()
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
        except DownloadError as exc:
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
        if not merged:
            merged.append(dict(segment))
            continue
        current = merged[-1]
        can_merge = (
            int(segment["start"]) <= int(current["end"]) + 2
            and int(segment["start"]) - int(current["start"]) <= 18
            and len(str(current["text"])) + len(str(segment["text"])) <= 500
        )
        if can_merge:
            current["text"] = _clean_caption_text(f"{current['text']} {segment['text']}")
            current["end"] = max(int(current["end"]), int(segment["end"]))
        else:
            merged.append(dict(segment))
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
        candidates = sorted(destination.parent.glob(f"{destination.stem}.*"))
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


def _transcribe_with_api(media: dict[str, Any], settings: Settings) -> TranscriptResult:
    with tempfile.TemporaryDirectory(prefix="kls-wy-media-") as temp_dir:
        media_path = _download_media(media, Path(temp_dir) / f"media-{media['id']}")
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with media_path.open("rb") as handle, httpx.Client(
                    timeout=settings.transcription_timeout_seconds,
                    follow_redirects=True,
                ) as client:
                    response = client.post(
                        settings.transcription_api_url,
                        files={"file": (media_path.name, handle, "application/octet-stream")},
                        data={"language": "en", "response_format": "verbose_json"},
                    )
                if response.status_code == 429 and attempt < 3:
                    time.sleep(30 * attempt)
                    continue
                response.raise_for_status()
                payload = response.json()
                raw_segments = payload.get("segments") if isinstance(payload, dict) else []
                segments = [
                    {
                        "start": max(0, int(float(item.get("start") or 0))),
                        "end": max(1, int(float(item.get("end") or 0))),
                        "text": _clean_caption_text(item.get("text")),
                    }
                    for item in (raw_segments or [])
                    if isinstance(item, dict) and _clean_caption_text(item.get("text"))
                ]
                if not segments:
                    raise RuntimeError("The transcription service returned no timestamped segments")
                duration = payload.get("duration") if isinstance(payload, dict) else None
                return TranscriptResult(
                    status="available",
                    source="speech_to_text",
                    segments=_merge_transcript_segments(segments),
                    duration_seconds=int(float(duration)) if duration else None,
                )
            except (httpx.HTTPError, OSError, RuntimeError, ValueError) as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(5 * attempt)
        return TranscriptResult(status="failed", error=str(last_error or "Transcription failed")[:1000])


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


def fetch_media_transcript(media: dict[str, Any], settings: Settings) -> TranscriptResult:
    try:
        captions: TranscriptResult | None = None
        if media.get("source_kind") == "youtube":
            captions = _youtube_captions(str(media["source_url"]), settings)
            if captions.status == "available":
                return captions
        if settings.transcription_api_url:
            return _transcribe_with_api(media, settings)
        if settings.local_transcription_model:
            return _transcribe_locally(media, settings)
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
    logger: Logger | None = None,
) -> tuple[int, int, int]:
    selected_years = sorted({int(year) for year in years}, reverse=True)
    config = settings or get_settings()
    statuses = None if force else ["pending", "needs_transcription"]
    media_items = list_legislative_media(
        "wy",
        years=selected_years,
        transcript_statuses=statuses,
        limit=limit,
    )
    added = waiting = failed = 0
    for media in media_items:
        if (
            media.get("transcript_status") == "needs_transcription"
            and not config.transcription_api_url
            and not config.local_transcription_model
        ):
            waiting += 1
            continue
        result = fetch_media_transcript(media, config)
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
    return sections


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
    scan_statuses = None if force else ["pending"]
    media_items = list_legislative_media(
        "wy",
        years=selected_years,
        transcript_statuses=["available"],
        explanation_scan_statuses=scan_statuses,
        limit=limit,
    )
    scanned = explanations = 0
    ollama = OllamaClient(config)
    try:
        for media in media_items:
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
