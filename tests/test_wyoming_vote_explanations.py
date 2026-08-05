from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import wyoming_vote_explanations as explanations
from app.db import (
    claim_legislative_media_explanation_scan,
    claim_legislative_media_transcription,
    connect,
    get_bill_vote_explanation_scan,
    get_legislative_media,
    list_bill_vote_explanations,
    list_legislative_media,
    replace_bill_roll_calls,
    replace_media_vote_explanations,
    update_legislative_media_transcript,
    upsert_bill_vote_explanation_scan,
    upsert_legislative_media,
)
from app.main import app
from app.settings import get_settings
from app.wyoming_vote_explanations import (
    TranscriptResult,
    _merge_transcript_segments,
    _transcription_chunk_quality_issue,
    _transcription_models_url,
    find_bill_sections,
    parse_youtube_json3,
    seed_curated_wyoming_examples,
    transcribe_wyoming_media,
)


def _seed_bill_and_vote() -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO bills (
                state, year, special_session_key, bill_num, bill_type, catch_title,
                bill_title, outcome, source_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wy",
                2098,
                -1,
                "SF0101",
                "SF",
                "A clear test bill",
                "A clear test bill",
                "passed",
                "2098-03-05T18:00:00+00:00",
                "2098-03-05T18:00:00+00:00",
                "2098-03-05T18:00:00+00:00",
            ),
        )
        connection.commit()
    replace_bill_roll_calls(
        "wy",
        2098,
        "SF0101",
        payloads=[
            {
                "roll_call_key": "vote-101",
                "vote_id": "101",
                "chamber": "H",
                "vote_date": "2098-03-05T11:45:00",
                "vote_type": "Third Reading",
                "action": "Third reading passed",
                "amendment_number": None,
                "yes_count": 0,
                "no_count": 1,
                "absent_count": 0,
                "conflict_count": 0,
                "excused_count": 0,
                "source_synced_at": "2098-03-05T18:00:00+00:00",
                "created_at": "2098-03-05T18:00:00+00:00",
                "updated_at": "2098-03-05T18:00:00+00:00",
                "members": [
                    {
                        "member_key": "wy-101",
                        "source_legislator_id": "101",
                        "legislator_name": "Pat Example",
                        "vote_label": "N",
                        "party": "Republican",
                        "district": "House District 1",
                        "vote_position": "no",
                    }
                ],
            }
        ],
    )


def test_parse_youtube_json3_and_find_bill_sections() -> None:
    segments = parse_youtube_json3(
        {
            "events": [
                {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "Senate file"}]},
                {"tStartMs": 3000, "dDurationMs": 2000, "segs": [{"utf8": " 101 is before us."}]},
                {"tStartMs": 7000, "dDurationMs": 2000, "segs": [{"utf8": "I will vote no."}]},
            ]
        }
    )

    assert segments[0]["start"] == 1
    assert "Senate file 101" in segments[0]["text"]
    sections = find_bill_sections(segments, ["SF0101", "HB0002"])
    assert sections == [{"bill_num": "SF0101", "start": 0, "end": 1800}]


def test_find_bill_sections_matches_spoken_bill_numbers() -> None:
    cases = [
        ("The next file is Senate File four.", "SF0004"),
        ("The committee considered Senate File eleven.", "SF0011"),
        ("House Bill eleven received a do-pass recommendation.", "HB0011"),
    ]
    for text, bill_num in cases:
        sections = find_bill_sections([{"start": 10, "end": 15, "text": text}], [bill_num])
        assert [section["bill_num"] for section in sections] == [bill_num]

    assert not find_bill_sections(
        [{"start": 10, "end": 15, "text": "House Bill eleven received a do-pass recommendation."}],
        ["HB0111"],
    )


def test_find_bill_sections_merges_overlapping_windows_for_the_same_bill() -> None:
    segments = [
        {"start": 100, "end": 105, "text": "Senate File 101 is before us."},
        {"start": 150, "end": 155, "text": "House Bill 2 was also mentioned."},
        {"start": 200, "end": 205, "text": "Returning to Senate File 101."},
        {"start": 250, "end": 255, "text": "House Bill 2 is next."},
    ]

    sections = find_bill_sections(segments, ["SF0101", "HB0002"])
    senate_sections = [section for section in sections if section["bill_num"] == "SF0101"]

    assert senate_sections == [{"bill_num": "SF0101", "start": 55, "end": 320}]


def test_transcription_quality_filter_and_models_endpoint() -> None:
    assert _transcription_models_url("http://stt.example/v1/audio/transcriptions") == (
        "http://stt.example/v1/models"
    )


def test_merge_transcript_segments_clamps_subsecond_timestamp_ranges() -> None:
    segments = _merge_transcript_segments(
        [
            {"start": 10, "end": 10, "text": "Short response."},
            {"start": 30, "end": 29, "text": "Another response."},
        ]
    )

    assert [(segment["start"], segment["end"]) for segment in segments] == [(10, 11), (30, 31)]
    assert _transcription_chunk_quality_issue(
        duration_seconds=120,
        density=52,
        rejected_ratio=0.616,
        raw_segment_count=268,
    ) == "chunk rejected 62% of transcript segments at 52.0 words per minute"
    assert (
        _transcription_chunk_quality_issue(
            duration_seconds=60,
            density=250,
            rejected_ratio=0,
            raw_segment_count=20,
        )
        is None
    )


def test_transcribe_exact_media_id_does_not_process_other_pending_media(monkeypatch) -> None:
    target_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "session_date": "2098-03-05",
            "chamber": "H",
            "source_url": "https://www.youtube.com/watch?v=target",
            "source_kind": "youtube",
            "external_id": "target",
            "title": "Target recording",
        }
    )
    other_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "session_date": "2098-03-06",
            "chamber": "S",
            "source_url": "https://www.youtube.com/watch?v=other",
            "source_kind": "youtube",
            "external_id": "other",
            "title": "Other recording",
        }
    )
    update_legislative_media_transcript(target_id, status="failed", error="Prior attempt failed")
    processed: list[int] = []

    def fake_fetch(media, _settings, *, logger=None):
        processed.append(int(media["id"]))
        return TranscriptResult(
            status="available",
            source="speech_to_text",
            segments=[{"start": 0, "end": 2, "text": "Test speech."}],
            duration_seconds=2,
        )

    monkeypatch.setattr(explanations, "fetch_media_transcript", fake_fetch)

    assert transcribe_wyoming_media(
        [2098],
        settings=get_settings(),
        media_ids=[target_id],
        force=True,
    ) == (1, 0, 0)
    assert processed == [target_id]
    assert get_legislative_media(target_id)["transcript_status"] == "available"
    assert get_legislative_media(other_id)["transcript_status"] == "pending"

    with pytest.raises(ValueError, match="not in the selected Wyoming years"):
        transcribe_wyoming_media(
            [2097],
            settings=get_settings(),
            media_ids=[target_id],
            force=True,
        )


def test_caption_rate_limit_falls_back_to_transcription(monkeypatch) -> None:
    request = httpx.Request("GET", "https://www.youtube.com/api/timedtext")
    response = httpx.Response(429, request=request)
    expected = TranscriptResult(
        status="available",
        source="speech_to_text",
        segments=[{"start": 0, "end": 2, "text": "Test speech."}],
    )
    logs: list[str] = []

    def rate_limited_captions(_source_url, _settings):
        raise httpx.HTTPStatusError("rate limited", request=request, response=response)

    monkeypatch.setattr(explanations, "_youtube_captions", rate_limited_captions)
    monkeypatch.setattr(
        explanations,
        "_transcribe_with_api",
        lambda media, settings, *, logger=None: expected,
    )

    result = explanations.fetch_media_transcript(
        {"id": 123, "source_kind": "youtube", "source_url": "https://youtu.be/test"},
        SimpleNamespace(transcription_api_url="http://stt.example", local_transcription_model=""),
        logger=logs.append,
    )

    assert result == expected
    assert "falling back to transcription" in logs[0]


@pytest.mark.parametrize(
    ("source_url", "expected"),
    [
        ("s://youtu.be/wFUJMqXGsBE", "https://youtu.be/wFUJMqXGsBE"),
        ("wyoleg.gov/2018/Audio/session.mp3", "https://wyoleg.gov/2018/Audio/session.mp3"),
        ("//www.wyoleg.gov/2020/Audio/session.mp3", "https://www.wyoleg.gov/2020/Audio/session.mp3"),
    ],
)
def test_normalize_official_media_source_urls(source_url: str, expected: str) -> None:
    assert explanations._normalize_media_source_url(source_url) == expected


def test_invalid_media_source_url_fails_without_calling_transcription(monkeypatch) -> None:
    monkeypatch.setattr(
        explanations,
        "_transcribe_with_api",
        lambda *_args, **_kwargs: pytest.fail("Invalid media must not reach transcription"),
    )

    result = explanations.fetch_media_transcript(
        {"id": 123, "source_kind": "youtube", "source_url": "not-a-url"},
        SimpleNamespace(transcription_api_url="http://stt.example", local_transcription_model=""),
    )

    assert result.status == "failed"
    assert result.error == "The official recording source URL is invalid."


def test_transcription_claims_are_distinct_and_stale_claims_recover() -> None:
    first_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "session_date": "2098-03-06",
            "chamber": "H",
            "source_url": "https://www.youtube.com/watch?v=claim-first",
            "source_kind": "youtube",
        }
    )
    second_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "session_date": "2098-03-05",
            "chamber": "S",
            "source_url": "https://www.youtube.com/watch?v=claim-second",
            "source_kind": "youtube",
        }
    )

    first_claim = claim_legislative_media_transcription("wy", years=[2098])
    second_claim = claim_legislative_media_transcription("wy", years=[2098])

    assert first_claim is not None and int(first_claim["id"]) == first_id
    assert second_claim is not None and int(second_claim["id"]) == second_id
    assert first_claim["transcript_status"] == "transcribing"
    assert second_claim["transcript_status"] == "transcribing"
    assert claim_legislative_media_transcription("wy", years=[2098]) is None

    with connect() as connection:
        connection.execute(
            "UPDATE legislative_media SET transcript_updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", first_id),
        )
        connection.commit()

    recovered = claim_legislative_media_transcription("wy", years=[2098], stale_after_seconds=1)
    assert recovered is not None and int(recovered["id"]) == first_id


def test_explanation_claims_are_distinct_and_stale_claims_recover() -> None:
    media_ids = []
    for day, chamber in ((6, "H"), (5, "S")):
        media_id = upsert_legislative_media(
            {
                "state": "wy",
                "year": 2098,
                "session_date": f"2098-03-{day:02d}",
                "chamber": chamber,
                "source_url": f"https://www.youtube.com/watch?v=scan-claim-{day}",
                "source_kind": "youtube",
            }
        )
        update_legislative_media_transcript(
            media_id,
            status="available",
            transcript_source="youtube_captions",
            segments=[{"start": 0, "end": 2, "text": "Test speech."}],
        )
        media_ids.append(media_id)

    first_claim = claim_legislative_media_explanation_scan("wy", years=[2098])
    second_claim = claim_legislative_media_explanation_scan("wy", years=[2098])

    assert first_claim is not None and int(first_claim["id"]) == media_ids[0]
    assert second_claim is not None and int(second_claim["id"]) == media_ids[1]
    assert first_claim["explanation_scan_status"] == "scanning"
    assert second_claim["explanation_scan_status"] == "scanning"
    assert claim_legislative_media_explanation_scan("wy", years=[2098]) is None

    with connect() as connection:
        connection.execute(
            "UPDATE legislative_media SET explanation_scanned_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", media_ids[0]),
        )
        connection.commit()

    recovered = claim_legislative_media_explanation_scan("wy", years=[2098], stale_after_seconds=1)
    assert recovered is not None and int(recovered["id"]) == media_ids[0]


def test_failed_media_waits_for_retry_cooldown() -> None:
    transcript_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2097,
            "session_date": "2097-03-06",
            "chamber": "H",
            "source_url": "https://www.youtube.com/watch?v=retry-transcript",
            "source_kind": "youtube",
        }
    )
    update_legislative_media_transcript(transcript_id, status="failed", error="temporary failure")

    assert claim_legislative_media_transcription("wy", years=[2097], retry_after_seconds=3600) is None
    with connect() as connection:
        connection.execute(
            "UPDATE legislative_media SET transcript_updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", transcript_id),
        )
        connection.commit()
    transcript_retry = claim_legislative_media_transcription("wy", years=[2097], retry_after_seconds=1)
    assert transcript_retry is not None and int(transcript_retry["id"]) == transcript_id

    scan_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2096,
            "session_date": "2096-03-06",
            "chamber": "S",
            "source_url": "https://www.youtube.com/watch?v=retry-scan",
            "source_kind": "youtube",
        }
    )
    update_legislative_media_transcript(
        scan_id,
        status="available",
        transcript_source="youtube_captions",
        segments=[{"start": 0, "end": 2, "text": "Test speech."}],
    )
    with connect() as connection:
        connection.execute(
            """
            UPDATE legislative_media
            SET explanation_scan_status = 'failed',
                explanation_scan_error = 'temporary failure',
                explanation_scanned_at = '2099-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (scan_id,),
        )
        connection.commit()

    assert claim_legislative_media_explanation_scan("wy", years=[2096], retry_after_seconds=3600) is None
    with connect() as connection:
        connection.execute(
            "UPDATE legislative_media SET explanation_scanned_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", scan_id),
        )
        connection.commit()
    scan_retry = claim_legislative_media_explanation_scan("wy", years=[2096], retry_after_seconds=1)
    assert scan_retry is not None and int(scan_retry["id"]) == scan_id


def test_queue_workers_mark_claims_before_processing(monkeypatch) -> None:
    transcript_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "session_date": "2098-03-06",
            "chamber": "H",
            "source_url": "https://www.youtube.com/watch?v=queue-transcript",
            "source_kind": "youtube",
        }
    )
    observed_transcript_statuses: list[str] = []

    def fake_fetch(media, _settings, *, logger=None):
        observed_transcript_statuses.append(str(get_legislative_media(int(media["id"]))["transcript_status"]))
        return TranscriptResult(
            status="available",
            source="speech_to_text",
            segments=[{"start": 0, "end": 2, "text": "Test speech."}],
        )

    monkeypatch.setattr(explanations, "fetch_media_transcript", fake_fetch)
    assert transcribe_wyoming_media([2098], settings=get_settings(), limit=1) == (1, 0, 0)
    assert observed_transcript_statuses == ["transcribing"]
    assert get_legislative_media(transcript_id)["transcript_status"] == "available"

    observed_scan_statuses: list[str] = []

    def fake_extract(media, *, ollama):
        observed_scan_statuses.append(str(get_legislative_media(int(media["id"]))["explanation_scan_status"]))
        return []

    monkeypatch.setattr(explanations, "extract_media_vote_explanations", fake_extract)
    assert explanations.scan_wyoming_media([2098], settings=get_settings(), limit=1) == (1, 0)
    assert observed_scan_statuses == ["scanning"]
    assert get_legislative_media(transcript_id)["explanation_scan_status"] == "complete"


def test_curated_example_uses_final_bill_vote_after_statement_date() -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO bills (
                state, year, special_session_key, bill_num, bill_type, catch_title,
                bill_title, outcome, source_synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "wy",
                2026,
                -1,
                "SF0101",
                "SF",
                "Firearms preemption",
                "Firearms preemption",
                "passed",
                "2026-03-09T18:00:00+00:00",
                "2026-03-09T18:00:00+00:00",
                "2026-03-09T18:00:00+00:00",
            ),
        )
        connection.commit()
    replace_bill_roll_calls(
        "wy",
        2026,
        "SF0101",
        payloads=[
            {
                "roll_call_key": "h-5520",
                "vote_id": "5520",
                "chamber": "H",
                "vote_date": "2026-03-09T14:00:00",
                "vote_type": "Third Reading",
                "action": "H 3rd Reading:Passed 40-21-1-0-0",
                "amendment_number": None,
                "yes_count": 40,
                "no_count": 21,
                "absent_count": 1,
                "conflict_count": 0,
                "excused_count": 0,
                "source_synced_at": "2026-03-09T18:00:00+00:00",
                "created_at": "2026-03-09T18:00:00+00:00",
                "updated_at": "2026-03-09T18:00:00+00:00",
                "members": [
                    {
                        "member_key": f"wy-{index}",
                        "source_legislator_id": str(index),
                        "legislator_name": name,
                        "vote_label": "N",
                        "party": "Republican",
                        "district": f"House District {index}",
                        "vote_position": "no",
                    }
                    for index, name in enumerate(
                        ["Art Washut", "Pam Thayer", "Elissa Campbell"],
                        start=1,
                    )
                ],
            }
        ],
    )
    upsert_legislative_media(
        {
            "state": "wy",
            "year": 2026,
            "special_session_value": None,
            "session_date": "2026-03-05",
            "session_day_number": "20th",
            "chamber": "H",
            "time_of_day": "AM",
            "display_order": 10,
            "source_url": "https://youtube.com/live/X45rOkJsR2g?feature=share",
            "source_kind": "youtube",
            "external_id": "X45rOkJsR2g",
            "mime_type": "application/octet-stream",
            "title": "Wyoming House floor session, 2026-03-05 AM",
        }
    )

    assert seed_curated_wyoming_examples() == 3
    explanations = list_bill_vote_explanations("wy", 2026, "SF0101")
    assert {item["lawmaker_name"] for item in explanations} == {
        "Art Washut",
        "Pam Thayer",
        "Elissa Campbell",
    }
    assert {item["roll_call_key"] for item in explanations} == {"h-5520"}


def test_explanations_are_stored_and_exposed_without_model_metadata() -> None:
    _seed_bill_and_vote()
    media_id = upsert_legislative_media(
        {
            "state": "wy",
            "year": 2098,
            "special_session_value": None,
            "session_date": "2098-03-05",
            "session_day_number": "20th",
            "chamber": "H",
            "time_of_day": "AM",
            "display_order": 10,
            "source_url": "https://www.youtube.com/watch?v=test-video",
            "source_kind": "youtube",
            "external_id": "test-video",
            "mime_type": "application/octet-stream",
            "title": "Wyoming House floor session",
        }
    )
    update_legislative_media_transcript(
        media_id,
        status="available",
        transcript_source="youtube_captions",
        segments=[{"start": 100, "end": 110, "text": "I will vote no because the wording is unclear."}],
        title="Wyoming House floor session",
        duration_seconds=300,
    )
    replace_media_vote_explanations(
        media_id,
        [
            {
                "state": "wy",
                "year": 2098,
                "special_session_value": None,
                "bill_num": "SF0101",
                "roll_call_key": "vote-101",
                "member_key": "wy-101",
                "lawmaker_name": "Pat Example",
                "vote_position": "no",
                "reason_summary": "The lawmaker said the wording was unclear.",
                "evidence_text": "I will vote no because the wording is unclear.",
                "source_url": "https://www.youtube.com/watch?v=test-video",
                "source_title": "Wyoming House floor session",
                "source_start_seconds": 100,
                "source_end_seconds": 110,
                "statement_date": "2098-03-05",
                "source_kind": "public_floor_statement",
                "review_status": "publishable",
            }
        ],
    )
    upsert_bill_vote_explanation_scan(
        {
            "state": "wy",
            "year": 2098,
            "special_session_value": None,
            "bill_num": "SF0101",
            "scan_status": "complete",
            "media_total": 1,
            "media_transcribed": 1,
            "media_scanned": 1,
            "explanation_count": 1,
            "last_scanned_at": "2098-03-06T00:00:00+00:00",
        }
    )

    stored_media = list_legislative_media("wy", years=[2098])
    stored_explanations = list_bill_vote_explanations("wy", 2098, "SF0101")
    stored_scan = get_bill_vote_explanation_scan("wy", 2098, "SF0101")
    response = TestClient(app).get("/api/v1/areas/wyoming/bills/2098/SF0101")
    index_response = TestClient(app).get("/api/v1/areas/wyoming/vote-explanations", params={"year": 2098})
    profile_response = TestClient(app).get(
        "/api/v1/areas/wyoming/legislators/wy-101",
        params={"year": 2098},
    )

    assert stored_media[0]["transcript_json"][0]["start"] == 100
    assert stored_explanations[0]["lawmaker_name"] == "Pat Example"
    assert stored_scan is not None and stored_scan["scan_status"] == "complete"
    assert response.status_code == 200
    payload = response.json()
    assert payload["vote_explanations"][0]["reason"] == "The lawmaker said the wording was unclear."
    assert payload["vote_explanations"][0]["source"]["url"].endswith("&t=100s")
    assert payload["vote_explanation_scan"] == {
        "status": "complete",
        "last_scanned_at": "2098-03-06T00:00:00+00:00",
    }
    assert "model" not in str(payload).casefold()
    assert index_response.status_code == 200
    assert index_response.json()["bills"][0]["bill"]["bill_num"] == "SF0101"
    assert profile_response.status_code == 200
    published_reason = profile_response.json()["published_reasons"][0]
    assert published_reason["bill"]["bill_num"] == "SF0101"
    assert published_reason["explanation"]["member_key"] == "wy-101"
    assert published_reason["explanation"]["reason"] == "The lawmaker said the wording was unclear."
    assert "model" not in str(profile_response.json()).casefold()
