from fastapi.testclient import TestClient

from app.db import (
    connect,
    get_bill_vote_explanation_scan,
    list_bill_vote_explanations,
    list_legislative_media,
    replace_bill_roll_calls,
    replace_media_vote_explanations,
    update_legislative_media_transcript,
    upsert_bill_vote_explanation_scan,
    upsert_legislative_media,
)
from app.main import app
from app.wyoming_vote_explanations import find_bill_sections, parse_youtube_json3


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
