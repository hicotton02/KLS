import json
from types import SimpleNamespace

import httpx
import pytest

from app import wyoming_vote_explanations as explanations
from app.db import (
    claim_legislative_media_transcription, connect, get_legislative_media,
    get_vote_explanation_overview, list_legislative_media, update_legislative_media_transcript,
    upsert_legislative_media,
)
from app.wyoming_media_repair import plan_media_repair, repair_media
from app.wyoming_media_sources import normalize_wyoming_media_url


def recording(url, **extra):
    return {"state": "wy", "year": 2018, "session_date": "2018-02-12", "chamber": "H",
            "source_url": url, "source_kind": "official_media", **extra}


@pytest.mark.parametrize(("url", "expected"), [
    ("http://wyoleg.gov/2018/Audio/AudioMenu/house/h021218am1.mp3", "https://wyoleg.gov/2018/Audio/house/h021218am1.mp3"),
    ("https://www.wyoleg.gov/2018/Audio/s021318am1.mp3", "https://wyoleg.gov/2018/Audio/senate/s021318am1.mp3"),
    ("http://wyoleg.gov/2018/Audio/h021318am1.mp3", "https://wyoleg.gov/2018/Audio/house/h021318am1.mp3"),
    ("https://elsewhere.example/2018/Audio/AudioMenu/house/test.mp3", "https://elsewhere.example/2018/Audio/AudioMenu/house/test.mp3"),
    ("https://wyoleg.gov/2019/Audio/AudioMenu/house/test.mp3", "https://wyoleg.gov/2019/Audio/AudioMenu/house/test.mp3"),
])
def test_archive_corrections_are_narrow(url, expected):
    assert normalize_wyoming_media_url(url) == expected
    assert normalize_wyoming_media_url(expected) == expected


def test_discovery_reuses_legacy_http_record_and_preserves_transcript():
    good = "http://wyoleg.gov/2018/Audio/house/h021218am1.mp3"
    media_id = upsert_legislative_media(recording(good))
    update_legislative_media_transcript(media_id, status="available", segments=[{"start": 0, "end": 1, "text": "Test."}])
    with connect() as connection:
        connection.execute("UPDATE legislative_media SET source_url = ?, explanation_scan_status = 'complete' WHERE id = ?", (good, media_id))
        connection.commit()
    assert upsert_legislative_media(recording(good.replace("/Audio/", "/Audio/AudioMenu/"))) == media_id
    assert len(list_legislative_media("wy")) == 1
    assert get_legislative_media(media_id)["transcript_status"] == "available"
    assert get_legislative_media(media_id)["explanation_scan_status"] == "complete"


@pytest.mark.parametrize(("error", "status"), [
    ("Invalid data found when processing input", "processing_failed"),
    ("chunk rejected 100% of transcript segments", "quality_failed"),
    ("The transcription service returned no timestamped speech", "quality_failed"),
    ("Sign in to confirm your age", "source_restricted"),
    ("Private video", "source_unavailable"),
    ("This video is not available", "source_unavailable"),
    ("Connection timed out", "failed"),
    ("429 Too Many Requests", "failed"),
])
def test_specific_failure_statuses(error, status):
    result = explanations._with_terminal_transcript_status(explanations.TranscriptResult(status="failed", error=error))
    assert result.status == status


@pytest.mark.parametrize(("status", "content_type", "body", "expected"), [
    (200, "text/html", b"<!doctype html><html>Not a recording</html>", "source_invalid"),
    (200, "audio/mpeg", b"<!doctype html><html>Error</html>", "source_invalid"),
    (200, "audio/mpeg", b"", "source_invalid"),
    (404, "text/html", b"Not found", "source_unavailable"),
    (410, "text/html", b"Gone", "source_unavailable"),
    (503, "text/html", b"Try later", "failed"),
    (429, "text/html", b"Slow down", "failed"),
])
def test_bad_downloads_never_reach_transcription(monkeypatch, tmp_path, status, content_type, body, expected):
    client_type = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(status, headers={"content-type": content_type}, content=body))
    monkeypatch.setattr(explanations.httpx, "Client", lambda **kwargs: client_type(transport=transport, **kwargs))
    monkeypatch.setattr(explanations, "_write_transcription_audio_chunks", lambda *_args: pytest.fail("Bad input reached audio processing"))
    result = explanations.fetch_media_transcript(
        {"id": 1, **recording("https://wyoleg.gov/test.mp3")},
        SimpleNamespace(transcription_api_url="http://stt.example", local_transcription_model=""),
    )
    assert result.status == expected
    assert not list(tmp_path.glob("*.mp3"))


def test_audio_download_is_preserved(monkeypatch, tmp_path):
    client_type = httpx.Client
    audio = b"ID3" + b"x" * 150_000
    transport = httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=audio))
    monkeypatch.setattr(explanations.httpx, "Client", lambda **kwargs: client_type(transport=transport, **kwargs))
    path = explanations._download_media(recording("https://wyoleg.gov/test.mp3"), tmp_path / "audio")
    assert path.read_bytes() == audio


def legacy_failure(url, error):
    media_id = upsert_legislative_media(recording(f"https://wyoleg.gov/temporary/{abs(hash(url))}.mp3"))
    with connect() as connection:
        connection.execute("UPDATE legislative_media SET source_url = ?, transcript_status = 'source_unavailable', transcript_error = ? WHERE id = ?", (url, error, media_id))
        connection.commit()
    return media_id


def test_repair_is_backed_up_idempotent_and_keeps_evidence(tmp_path):
    good = "http://wyoleg.gov/2018/Audio/house/h021218am1.mp3"
    keeper = upsert_legislative_media(recording(good))
    update_legislative_media_transcript(keeper, status="available", segments=[{"start": 0, "end": 1, "text": "Saved speech."}])
    duplicate = legacy_failure(good.replace("/Audio/", "/Audio/AudioMenu/"), "Invalid data found when processing input")
    recovery = legacy_failure("http://wyoleg.gov/2018/Audio/AudioMenu/house/h021218pm1.mp3", "Invalid data found when processing input")
    quality = legacy_failure("http://wyoleg.gov/2006/Audio/house/test.mp3", "chunk rejected 100%")
    before = get_legislative_media(keeper)
    assert repair_media()["counts"] == {"duplicate": 1, "pending": 2}
    assert get_legislative_media(duplicate)["duplicate_of_id"] is None
    backup = tmp_path / "backup.json"
    repair_media(apply=True, backup_path=backup)
    assert len(json.loads(backup.read_text())["rows"]) == 3
    assert get_legislative_media(keeper) == before
    assert get_legislative_media(duplicate)["duplicate_of_id"] == keeper
    assert get_legislative_media(recovery)["source_url"] == "https://wyoleg.gov/2018/Audio/house/h021218pm1.mp3"
    assert get_legislative_media(quality)["transcript_status"] == "pending"
    assert len(list_legislative_media("wy")) == 3
    overview = get_vote_explanation_overview("wy")
    assert overview["media_total"] == 3
    assert overview["transcription_backlog"] == 2
    assert repair_media()["changes"] == []
    with connect() as connection:
        connection.execute("UPDATE legislative_media SET transcript_status = 'pending' WHERE id = ?", (duplicate,))
        connection.commit()
    claimed = [claim_legislative_media_transcription("wy") for _ in range(3)]
    assert {m["id"] for m in claimed if m} == {recovery, quality}
    with pytest.raises(ValueError, match="duplicate"):
        explanations.transcribe_wyoming_media([2018], media_ids=[duplicate], force=True)


def test_repair_refuses_active_workers_and_identity_conflicts():
    base = {"id": 1, "year": 2018, "special_session_key": -1, "session_date": "2018-02-12", "chamber": "H",
            "source_url": "http://wyoleg.gov/test.mp3", "transcript_status": "pending", "explanation_scan_status": "pending"}
    with pytest.raises(ValueError, match="Pause"):
        plan_media_repair([{**base, "transcript_status": "transcribing"}])
    with pytest.raises(ValueError, match="identity"):
        plan_media_repair([base, {**base, "id": 2, "chamber": "S"}])


def test_quality_failures_are_held_and_counted_separately():
    media_id = upsert_legislative_media(recording("https://wyoleg.gov/test.mp3"))
    update_legislative_media_transcript(media_id, status="quality_failed", error="Speech failed checks")
    assert claim_legislative_media_transcription("wy", retry_after_seconds=1) is None
    overview = get_vote_explanation_overview("wy")
    assert overview["transcription_quality_failed"] == 1
    assert overview["unavailable_recordings"] == 0
    assert overview["transcription_backlog"] == 0
    assert explanations._bill_explanation_scan_status([get_legislative_media(media_id)]) == "needs_review"
    from app.analytics import metrics_response
    metrics = metrics_response().body.decode()
    assert 'kls_legislative_media_issues{reason="quality_failed",state="wy"} 1.0' in metrics
