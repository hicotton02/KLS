from pathlib import Path

from scripts import benchmark_wyoming_transcription as benchmark


def test_chunk_quality_retries_sparse_heavily_rejected_audio() -> None:
    issue = benchmark._chunk_quality_issue(
        duration_seconds=120,
        density=52,
        rejected_ratio=0.616,
        raw_segment_count=268,
    )

    assert issue == "chunk rejected 62% of transcript segments at 52.0 words per minute"
    assert (
        benchmark._chunk_quality_issue(
            duration_seconds=120,
            density=106,
            rejected_ratio=0.641,
            raw_segment_count=100,
        )
        is None
    )


def test_chunk_quality_allows_short_dense_speech_but_rejects_runaway_text() -> None:
    assert (
        benchmark._chunk_quality_issue(
            duration_seconds=60,
            density=250,
            rejected_ratio=0,
            raw_segment_count=20,
        )
        is None
    )
    assert benchmark._chunk_quality_issue(
        duration_seconds=60,
        density=270,
        rejected_ratio=0,
        raw_segment_count=20,
    ) == "chunk speech density was 270.0 words per minute"
    assert benchmark._chunk_quality_issue(
        duration_seconds=120,
        density=110,
        rejected_ratio=0.66,
        raw_segment_count=100,
    ) == "chunk rejected 66% of transcript segments at 110.0 words per minute"


def test_download_audio_retries_transient_failures(monkeypatch, tmp_path: Path) -> None:
    destination = tmp_path / "recording"
    attempts = 0

    class FakeDownloader:
        def __init__(self, _options: dict[str, object]) -> None:
            pass

        def __enter__(self) -> "FakeDownloader":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def extract_info(self, _source_url: str, *, download: bool) -> None:
            nonlocal attempts
            assert download is True
            attempts += 1
            if attempts < 3:
                raise RuntimeError("transient source failure")
            destination.with_suffix(".webm").write_bytes(b"audio")

    monkeypatch.setattr(benchmark, "YoutubeDL", FakeDownloader)
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)

    assert benchmark._download_audio("https://example.test/recording", destination) == destination.with_suffix(
        ".webm"
    )
    assert attempts == 3
