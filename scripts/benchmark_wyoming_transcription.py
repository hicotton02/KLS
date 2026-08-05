from __future__ import annotations

import argparse
import base64
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.wyoming_vote_explanations import find_bill_sections


SAMPLE_MEDIA_IDS = (333, 481, 258, 256, 471, 255, 254, 262, 266, 265)
SAMPLE_RATE = 16_000
MAX_COMPRESSION_RATIO = 4.0
MAX_WORDS_PER_MINUTE = 240
MAX_SHORT_CHUNK_WORDS_PER_MINUTE = 260
MAX_REJECTED_SEGMENT_RATIO = 0.65
SPARSE_REJECTED_SEGMENT_RATIO = 0.60
MIN_WORDS_PER_MINUTE_AFTER_HEAVY_REJECTION = 80


class ChunkQualityError(RuntimeError):
    def __init__(self, message: str, *, elapsed: float, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.elapsed = elapsed
        self.diagnostics = diagnostics


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _production_samples(namespace: str, deployment: str, media_ids: list[int]) -> list[dict[str, Any]]:
    script = f"""
import json
from app.db import get_legislative_media, list_roll_calls_for_session

out = []
for media_id in {media_ids!r}:
    media = get_legislative_media(media_id)
    if not media:
        continue
    segments = media.get("transcript_json") or []
    roll_calls = list_roll_calls_for_session(
        "wy",
        int(media["year"]),
        str(media["session_date"]),
        str(media["chamber"]),
        special_session_value=media.get("special_session_value"),
    )
    out.append({{
        "id": media_id,
        "year": media["year"],
        "session_date": media["session_date"],
        "chamber": media["chamber"],
        "time_of_day": media.get("time_of_day"),
        "source_url": media["source_url"],
        "duration_seconds": media.get("duration_seconds"),
        "bill_nums": sorted({{str(row["bill_num"]) for row in roll_calls}}),
        "baseline_segments": segments,
    }})
print(json.dumps(out, separators=(",", ":")))
"""
    encoded = base64.b64encode(script.encode()).decode()
    command = [
        "kubectl",
        "-n",
        namespace,
        "exec",
        f"deploy/{deployment}",
        "-c",
        "web",
        "--",
        "python",
        "-c",
        f"import base64;exec(base64.b64decode('{encoded}'))",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def _wait_for_service(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"port-forward stopped: {process.stderr.read().strip()}")
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
            response.raise_for_status()
            return
        except httpx.HTTPError:
            time.sleep(0.5)
    raise RuntimeError("transcription service port-forward did not become ready")


def _service_choice(port: int, choice_index: int = 0) -> str:
    response = httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=10)
    response.raise_for_status()
    choices = response.json().get("data") or []
    if not choices or choice_index < 0 or choice_index >= len(choices) or not choices[choice_index].get("id"):
        raise RuntimeError("transcription service advertised no available engine")
    return str(choices[choice_index]["id"])


def _download_audio(source_url: str, destination: Path) -> Path:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 90,
        "extractor_retries": 5,
        "fragment_retries": 5,
        "extractor_args": {"youtube": {"player_client": ["android_vr"]}},
        "format": "bestaudio/best",
        "outtmpl": str(destination.with_suffix(".%(ext)s")),
    }
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            with YoutubeDL(options) as downloader:
                downloader.extract_info(source_url, download=True)
            candidates = sorted(
                path for path in destination.parent.glob(f"{destination.stem}.*") if path.suffix != ".part"
            )
            if not candidates:
                raise RuntimeError("download completed without an audio file")
            return candidates[0]
        except (DownloadError, RuntimeError) as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(3 * attempt)
    raise RuntimeError(f"recording download failed after three attempts: {last_error}")


def _write_audio_chunks(audio_path: Path, workdir: Path, chunk_seconds: int) -> list[dict[str, Any]]:
    import av

    workdir.mkdir(parents=True, exist_ok=True)
    chunk_samples = SAMPLE_RATE * chunk_seconds
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
        writer.setframerate(SAMPLE_RATE)
        return writer, path

    def close_chunk() -> None:
        nonlocal chunk_writer, chunk_path, chunk_written
        if chunk_writer is None or chunk_path is None:
            return
        chunk_writer.close()
        if chunk_written:
            offset = (total_written - chunk_written) / SAMPLE_RATE
            chunks.append(
                {
                    "path": chunk_path,
                    "offset_seconds": offset,
                    "duration_seconds": chunk_written / SAMPLE_RATE,
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
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for source_frame in container.decode(audio=0):
            for frame in resampler.resample(source_frame):
                write_pcm(bytes(frame.planes[0])[: frame.samples * 2])
        for frame in resampler.resample(None):
            write_pcm(bytes(frame.planes[0])[: frame.samples * 2])
    finally:
        container.close()
        close_chunk()
    if not chunks:
        raise RuntimeError("downloaded media contained no decodable audio")
    return chunks


def _split_wav_chunk(chunk: dict[str, Any], workdir: Path, seconds: int = 300) -> list[dict[str, Any]]:
    workdir.mkdir(parents=True, exist_ok=True)
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


def _normalized_segment(value: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


def _chunk_quality_issue(
    *,
    duration_seconds: float,
    density: float,
    rejected_ratio: float,
    raw_segment_count: int,
) -> str | None:
    density_limit = (
        MAX_SHORT_CHUNK_WORDS_PER_MINUTE if duration_seconds <= 60 else MAX_WORDS_PER_MINUTE
    )
    if density > density_limit:
        return f"chunk speech density was {density:.1f} words per minute"
    too_many_rejected = raw_segment_count > 0 and (
        rejected_ratio > MAX_REJECTED_SEGMENT_RATIO
        or (
            rejected_ratio > SPARSE_REJECTED_SEGMENT_RATIO
            and density < MIN_WORDS_PER_MINUTE_AFTER_HEAVY_REJECTION
        )
    )
    if too_many_rejected:
        return f"chunk rejected {rejected_ratio:.0%} of transcript segments at {density:.1f} words per minute"
    return None


def _transcribe_chunk(
    port: int,
    service_choice: str,
    chunk: dict[str, Any],
    bill_nums: list[str],
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    started = time.monotonic()
    audio_path = Path(chunk["path"])
    request_data = {
        "model": service_choice,
        "language": "en",
        "response_format": "verbose_json",
        "vad_filter": "true",
    }
    if bill_nums:
        request_data["hotwords"] = " ".join(bill_nums)
    with audio_path.open("rb") as handle:
        response = httpx.post(
            f"http://127.0.0.1:{port}/v1/audio/transcriptions",
            files={"file": (audio_path.name, handle, "application/octet-stream")},
            data=request_data,
            timeout=3600,
        )
    elapsed = time.monotonic() - started
    response.raise_for_status()
    payload = response.json()
    raw_segments = [item for item in (payload.get("segments") or []) if isinstance(item, dict)]
    segments: list[dict[str, Any]] = []
    rejected = 0
    recent_text: dict[str, float] = {}
    offset = float(chunk["offset_seconds"])
    for item in raw_segments:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        compression_ratio = float(item.get("compression_ratio") or 0)
        no_speech_probability = float(item.get("no_speech_prob") or 0)
        average_log_probability = float(item.get("avg_logprob") or 0)
        start = max(0.0, float(item.get("start") or 0))
        normalized = _normalized_segment(text)
        previous_start = recent_text.get(normalized)
        repeated = (
            len(normalized.split()) >= 10
            and previous_start is not None
            and start - previous_start <= 120
        )
        if (
            compression_ratio > MAX_COMPRESSION_RATIO
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
    word_count = len(_words(segments))
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
    quality_issue = _chunk_quality_issue(
        duration_seconds=float(chunk["duration_seconds"]),
        density=density,
        rejected_ratio=rejected_ratio,
        raw_segment_count=len(raw_segments),
    )
    if quality_issue:
        raise ChunkQualityError(
            quality_issue,
            elapsed=elapsed,
            diagnostics=diagnostics,
        )
    return segments, elapsed, diagnostics


def _transcribe_chunked(
    port: int,
    service_choice: str,
    audio_path: Path,
    known_bill_nums: list[str],
    workdir: Path,
    chunk_seconds: int,
    *,
    use_bill_hints: bool = False,
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    initial_chunks = _write_audio_chunks(audio_path, workdir, chunk_seconds)
    segments: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    elapsed = 0.0

    def transcribe_minute_retries(
        chunk: dict[str, Any],
        chunk_index: int,
    ) -> list[dict[str, Any]]:
        nonlocal elapsed
        retry_segments: list[dict[str, Any]] = []
        retry_chunks = _split_wav_chunk(chunk, workdir, seconds=60)
        for retry_index, retry_chunk in enumerate(retry_chunks, start=1):
            try:
                piece_segments, retry_elapsed, retry_diagnostics = _transcribe_chunk(
                    port,
                    service_choice,
                    retry_chunk,
                    known_bill_nums if use_bill_hints else [],
                )
            except ChunkQualityError as exc:
                elapsed += exc.elapsed
                retry_diagnostics = dict(exc.diagnostics)
                retry_diagnostics["chunk"] = chunk_index
                retry_diagnostics["retry_piece"] = retry_index
                retry_diagnostics["split_retry"] = True
                retry_diagnostics["retry_reason"] = "quality"
                retry_diagnostics["rejected"] = True
                diagnostics.append(retry_diagnostics)
                raise RuntimeError(
                    f"one-minute quality retry failed for chunk {chunk_index}, piece {retry_index}: {exc}"
                ) from exc
            retry_diagnostics["chunk"] = chunk_index
            retry_diagnostics["retry_piece"] = retry_index
            retry_diagnostics["split_retry"] = True
            retry_diagnostics["retry_reason"] = "quality"
            retry_segments.extend(piece_segments)
            diagnostics.append(retry_diagnostics)
            elapsed += retry_elapsed
        return retry_segments

    for index, chunk in enumerate(initial_chunks, start=1):
        try:
            chunk_segments, chunk_elapsed, chunk_diagnostics = _transcribe_chunk(
                port,
                service_choice,
                chunk,
                known_bill_nums if use_bill_hints else [],
            )
            chunk_diagnostics["chunk"] = index
            chunk_diagnostics["split_retry"] = False
            elapsed += chunk_elapsed
            segments.extend(chunk_segments)
            diagnostics.append(chunk_diagnostics)
        except ChunkQualityError as exc:
            elapsed += exc.elapsed
            failed_diagnostics = dict(exc.diagnostics)
            failed_diagnostics["chunk"] = index
            failed_diagnostics["split_retry"] = False
            failed_diagnostics["replaced_by_quality_retry"] = True
            diagnostics.append(failed_diagnostics)
            print(f"  Chunk {index} failed quality ({exc}); retrying as one-minute pieces.", flush=True)
            segments.extend(transcribe_minute_retries(chunk, index))
    segments.sort(key=lambda item: (int(item["start"]), int(item["end"])))
    return segments, elapsed, diagnostics


def _words(segments: list[dict[str, Any]]) -> list[str]:
    text = " ".join(str(segment.get("text") or "") for segment in segments).casefold()
    return re.findall(r"[a-z0-9]+", text)


def _trigrams(words: list[str]) -> set[tuple[str, str, str]]:
    return set(zip(words, words[1:], words[2:]))


def _quality_metrics(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    bill_nums: list[str],
) -> dict[str, Any]:
    baseline_words = _words(baseline)
    candidate_words = _words(candidate)
    baseline_grams = _trigrams(baseline_words)
    candidate_grams = _trigrams(candidate_words)
    shared = len(baseline_grams & candidate_grams)
    baseline_sections = find_bill_sections(baseline, bill_nums)
    candidate_sections = find_bill_sections(candidate, bill_nums)
    baseline_bills = {item["bill_num"] for item in baseline_sections}
    candidate_bills = {item["bill_num"] for item in candidate_sections}
    metrics = {
        "baseline_words": len(baseline_words),
        "transcribed_words": len(candidate_words),
        "word_count_ratio": round(len(candidate_words) / max(1, len(baseline_words)), 3),
        "trigram_recall": round(shared / max(1, len(baseline_grams)), 3),
        "trigram_precision": round(shared / max(1, len(candidate_grams)), 3),
        "baseline_bills_found": sorted(baseline_bills),
        "transcribed_bills_found": sorted(candidate_bills),
        "bill_recall": round(len(baseline_bills & candidate_bills) / max(1, len(baseline_bills)), 3),
        "reference_snippets": [
            {"start": int(segment.get("start") or 0), "text": str(segment.get("text") or "")[:500]}
            for segment in candidate
            if re.search(r"\b(?:house bill|senate file)\b", str(segment.get("text") or ""), re.IGNORECASE)
        ][:100],
    }
    issues = []
    if not 0.4 <= metrics["word_count_ratio"] <= 1.8:
        issues.append("transcript word count is outside the accepted caption-baseline range")
    if baseline_bills and metrics["bill_recall"] < 0.9:
        issues.append("bill-reference recall is below 90 percent")
    metrics["quality_status"] = "passed" if not issues else "failed"
    metrics["quality_issues"] = issues
    return metrics


def run(args: argparse.Namespace) -> dict[str, Any]:
    media_ids = list(dict.fromkeys(args.media_id or SAMPLE_MEDIA_IDS))
    samples = _production_samples(args.namespace, args.deployment, media_ids)
    if len(samples) != len(media_ids):
        raise RuntimeError(f"expected {len(media_ids)} samples, found {len(samples)}")

    port = _available_port()
    port_forward = subprocess.Popen(
        ["kubectl", "-n", args.service_namespace, "port-forward", f"svc/{args.service}", f"{port}:80"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_service(port, port_forward)
        service_choice = _service_choice(port, args.service_choice_index)
        results: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="kls-wy-stt-benchmark-") as temp_dir:
            workdir = Path(temp_dir)
            for index, sample in enumerate(samples, start=1):
                label = f"{sample['year']} {sample['session_date']} {sample['chamber']} {sample.get('time_of_day') or ''}".strip()
                print(f"[{index}/{len(samples)}] Downloading {label}...", flush=True)
                result: dict[str, Any] = {
                    "media_id": sample["id"],
                    "label": label,
                    "source_url": sample["source_url"],
                    "duration_seconds": sample.get("duration_seconds"),
                }
                try:
                    audio_path = _download_audio(sample["source_url"], workdir / f"media-{sample['id']}")
                    print(f"[{index}/{len(samples)}] Transcribing {label}...", flush=True)
                    segments, elapsed, chunk_diagnostics = _transcribe_chunked(
                        port,
                        service_choice,
                        audio_path,
                        sample["bill_nums"],
                        workdir / f"chunks-{sample['id']}",
                        args.chunk_seconds,
                        use_bill_hints=args.bill_hints,
                    )
                    duration = int(sample.get("duration_seconds") or segments[-1]["end"])
                    result.update(
                        {
                            "status": "passed",
                            "processing_seconds": round(elapsed, 1),
                            "realtime_speed": round(duration / max(0.1, elapsed), 2),
                            "segments": len(segments),
                            "chunks": chunk_diagnostics,
                            **_quality_metrics(sample["baseline_segments"], segments, sample["bill_nums"]),
                        }
                    )
                except Exception as exc:  # Keep the batch moving and report each failure.
                    result.update({"status": "failed", "error": str(exc)[:1000]})
                results.append(result)
                print(
                    f"[{index}/{len(samples)}] {result['status']}"
                    + (f" at {result.get('realtime_speed')}x real time." if result["status"] == "passed" else "."),
                    flush=True,
                )
        passed = [item for item in results if item["status"] == "passed"]
        quality_passed = [item for item in passed if item.get("quality_status") == "passed"]
        summary = {
            "sample_count": len(results),
            "passed": len(passed),
            "failed": len(results) - len(passed),
            "quality_passed": len(quality_passed),
            "quality_failed": len(passed) - len(quality_passed),
            "audio_hours": round(sum(int(item.get("duration_seconds") or 0) for item in results) / 3600, 2),
            "average_realtime_speed": round(
                sum(float(item["realtime_speed"]) for item in passed) / max(1, len(passed)), 2
            ),
            "average_trigram_recall": round(
                sum(float(item["trigram_recall"]) for item in passed) / max(1, len(passed)), 3
            ),
            "average_bill_recall": round(
                sum(float(item["bill_recall"]) for item in passed if item["baseline_bills_found"])
                / max(1, sum(bool(item["baseline_bills_found"]) for item in passed)),
                3,
            ),
        }
        return {"summary": summary, "recordings": results}
    finally:
        port_forward.terminate()
        try:
            port_forward.wait(timeout=5)
        except subprocess.TimeoutExpired:
            port_forward.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Wyoming recordings against the shared transcription service.")
    parser.add_argument("--namespace", default="keeping-law-simple")
    parser.add_argument("--deployment", default="keeping-law-simple-web")
    parser.add_argument("--service-namespace", default="automation")
    parser.add_argument("--service", default="automation-stt-gpu1-api")
    parser.add_argument("--service-choice-index", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--bill-hints", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--media-id", action="append", type=int, help="Benchmark one or more media IDs")
    parser.add_argument("--chunk-seconds", type=int, default=120)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    report = run(args)
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2) if args.summary_only else rendered)


if __name__ == "__main__":
    main()
