from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from app.content_corrections import WY_BUDGET_SOURCE_HASH, apply_content_corrections
from app.content_quality import assess_bill_content
from app.db import connect, list_recent_bills, replace_bill_roll_calls, upsert_bill
from app.main import app


def bill(number="HB0001", **changes):
    result = {
        "state": "wy", "year": 2026, "bill_num": number, "special_session_value": None,
        "catch_title": "School funding", "bill_title": "School funding", "outcome": "active",
        "last_action_date": "2026-04-01", "source_hash": "source", "bill_actions_json": [],
        "created_at": "2026-04-01", "updated_at": "2026-04-01",
        "interpretation_json": {
            "fact_check_status": "validated", "one_sentence_summary": "This bill funds school repairs.",
            "what_it_does": ["Sets aside $5 million to repair school roofs."],
            "who_it_affects": ["Public schools"],
        },
    }
    for key in ("bill_type", "sponsor", "bill_status", "status_label", "status_explainer",
                "last_action", "signed_date", "effective_date", "chapter_no", "enrolled_no",
                "sponsor_string_house", "sponsor_string_senate", "introduced_path", "digest_path",
                "summary_path", "current_version_path", "official_digest_text", "official_summary_text",
                "current_bill_text", "source_synced_at"):
        result.setdefault(key, None)
    result.update(changes)
    return result


def test_ready_summary_needs_original_explanation_and_source():
    assert assess_bill_content(bill())["summary_ready"] is True
    assert assess_bill_content(bill(state="tx"))["summary_ready"] is False
    assert assess_bill_content(bill(state="tx", summary_path="https://capitol.texas.gov/bill"))["summary_ready"] is True


@pytest.mark.parametrize("status", ["fallback", "stale", "", None])
def test_unchecked_explanations_fail_closed(status):
    item = bill()
    item["interpretation_json"]["fact_check_status"] = status
    quality = assess_bill_content(item)
    assert not quality["summary_ready"]
    assert not quality["ads_eligible"]
    assert not quality["featured"]
    assert not quality["indexable"]


@pytest.mark.parametrize("summary,points", [("School funding.", ["School funding"]),
                                          ("This bill funds school repairs.", ["This bill funds school repairs."]),
                                          ("", []), ("Something", "not an array")])
def test_repetition_and_malformed_explanations_are_not_ready(summary, points):
    item = bill()
    item["interpretation_json"].update(one_sentence_summary=summary, what_it_does=points)
    assert not assess_bill_content(item)["summary_ready"]


def test_useful_archives_can_be_indexed_without_becoming_ad_eligible():
    quality = assess_bill_content(bill(interpretation_json=None), has_vote_record=True)
    assert quality["indexable"] and quality["state"] == "vote_record"
    assert not quality["ads_eligible"] and not quality["featured"]


def test_source_version_correction_is_repeatable_and_does_not_mutate_input():
    original = bill("SF0001", source_hash=WY_BUDGET_SOURCE_HASH)
    before = deepcopy(original)
    corrected = apply_content_corrections(original)
    assert original == before
    assert "$10,099,046,462" in corrected["interpretation_json"]["what_it_does"][0]
    assert apply_content_corrections(corrected) == corrected
    changed = {**original, "source_hash": "changed-source"}
    assert apply_content_corrections(changed) == changed
    other_session = {**original, "year": 2025}
    assert apply_content_corrections(other_session) == other_session


def test_api_sitemap_and_featured_list_use_same_quality_rules():
    upsert_bill(bill())
    upsert_bill(bill("HB0002", interpretation_json=None))
    upsert_bill(bill("HB0003", interpretation_json=None, year=2019))
    replace_bill_roll_calls("wy", 2019, "HB0003", payloads=[{
        "roll_call_key": "floor", "chamber": "H", "action": "Third reading",
        "yes_count": 40, "no_count": 20, "members": [],
        "vote_id": None, "vote_date": "2019-02-01", "vote_type": None,
        "amendment_number": None, "absent_count": 0, "conflict_count": 0,
        "excused_count": 0, "source_synced_at": "2026-09-21",
        "created_at": "2026-09-21", "updated_at": "2026-09-21",
    }])
    client = TestClient(app)
    for number, year, indexed, ready in [("HB0001", 2026, True, True), ("HB0002", 2026, False, False),
                                        ("HB0003", 2019, True, False)]:
        data = client.get(f"/api/v1/areas/wyoming/bills/{year}/{number}").json()
        quality = data["bill"]["content_quality"]
        assert quality["indexable"] is indexed
        assert quality["summary_ready"] is ready
        assert bool(data["bill"]["summary"]) is ready
        sitemap = client.get("/sitemaps/wyoming.xml").text
        assert (f"/bill/{year}/{number}" in sitemap) is indexed
    assert [item["bill_num"] for item in list_recent_bills()] == ["HB0001"]


def test_text_heading_cannot_outrank_real_dates():
    upsert_bill(bill("HB0001", last_action_date="Final Actions", updated_at="2026-09-21"))
    upsert_bill(bill("HB0002", last_action_date="2026-09-20"))
    assert [item["bill_num"] for item in list_recent_bills(2)] == ["HB0002", "HB0001"]


def test_correction_is_persisted_on_ingestion():
    upsert_bill(bill("SF0001", source_hash=WY_BUDGET_SOURCE_HASH))
    with connect() as c:
        raw = c.execute("SELECT interpretation_json FROM bills WHERE bill_num = ?", ("SF0001",)).fetchone()
    assert "10,099,046,462" in raw["interpretation_json"]


def test_recent_bill_pattern_is_bound_for_postgres(monkeypatch):
    import app.db as db
    observed = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params):
            observed.append((sql, params))
            return self

        def fetchall(self):
            return []

    monkeypatch.setattr(db, "connect", Connection)
    assert db.list_recent_bills() == []
    sql, params = observed[0]
    assert "LIKE ?" in sql and "%validated%" not in sql
    assert params[0] == "%validated%"
