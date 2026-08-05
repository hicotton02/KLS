import json

import app.sync_service as sync_service
from app.db import connect, get_bill
from app.sync_service import (
    FACT_CHECK_VERSION,
    _compute_source_hash,
    _fallback_interpretation,
    _interpretation_for_sync,
    _mark_validated_interpretation,
    _needs_refresh,
    _reusable_interpretation,
    repair_missing_interpretations,
)


def test_needs_refresh_when_fact_check_version_is_missing() -> None:
    existing = {
        "has_interpretation": 1,
        "fact_check_status": "",
        "fact_check_version": 0,
        "generator_model": "qwen3.5:27b",
        "bill_status": "inactive",
        "last_action": "Assigned Chapter Number 42",
        "last_action_date": "2026-03-05T00:00:00",
        "signed_date": "2026-03-05T00:00:00",
        "effective_date": "",
        "chapter_no": "0042",
        "enrolled_no": "16",
    }
    item = {
        "billStatus": "inactive",
        "lastAction": "Assigned Chapter Number 42",
        "lastActionDate": "2026-03-05T00:00:00",
        "signedDate": "2026-03-05T00:00:00",
        "chapterNo": "0042",
        "enrolledNo": "16",
    }

    assert _needs_refresh(existing, item, skip_interpretation=False, current_model="qwen3.5:27b") is True


def test_needs_refresh_when_existing_entry_is_fallback() -> None:
    existing = {
        "has_interpretation": 1,
        "fact_check_status": "fallback",
        "fact_check_version": FACT_CHECK_VERSION,
        "generator_model": "qwen3.5:27b",
        "bill_status": "inactive",
        "last_action": "Assigned Chapter Number 42",
        "last_action_date": "2026-03-05T00:00:00",
        "signed_date": "2026-03-05T00:00:00",
        "effective_date": "",
        "chapter_no": "0042",
        "enrolled_no": "16",
    }
    item = {
        "billStatus": "inactive",
        "lastAction": "Assigned Chapter Number 42",
        "lastActionDate": "2026-03-05T00:00:00",
        "signedDate": "2026-03-05T00:00:00",
        "chapterNo": "0042",
        "enrolledNo": "16",
    }

    assert _needs_refresh(existing, item, skip_interpretation=False, current_model="qwen3.5:27b") is True


def test_needs_refresh_when_model_changes() -> None:
    existing = {
        "has_interpretation": 1,
        "fact_check_status": "validated",
        "fact_check_version": FACT_CHECK_VERSION,
        "generator_model": "qwen2.5:7b-instruct",
        "bill_status": "inactive",
        "last_action": "Assigned Chapter Number 42",
        "last_action_date": "2026-03-05T00:00:00",
        "signed_date": "2026-03-05T00:00:00",
        "effective_date": "",
        "chapter_no": "0042",
        "enrolled_no": "16",
    }
    item = {
        "billStatus": "inactive",
        "lastAction": "Assigned Chapter Number 42",
        "lastActionDate": "2026-03-05T00:00:00",
        "signedDate": "2026-03-05T00:00:00",
        "chapterNo": "0042",
        "enrolledNo": "16",
    }

    assert _needs_refresh(existing, item, skip_interpretation=False, current_model="qwen3.5:27b") is True


def test_validated_interpretation_gets_fact_check_metadata() -> None:
    interpretation = _mark_validated_interpretation(
        {
            "plain_language_title": "Education funding",
            "one_sentence_summary": "This bill changes how money is sent to schools.",
            "what_it_does": ["It changes a school funding formula."],
            "who_it_affects": ["Public schools."],
            "terms_to_know": [],
            "limits_and_unknowns": ["The text shown here is only part of the full bill."],
            "removed_claims": ["Removed a claim about teacher raises that was not supported by the source text."],
            "validator_notes": ["The source excerpt does not explain the fiscal impact in detail."],
        },
        "qwen3.5:27b",
    )

    assert interpretation["fact_check_status"] == "validated"
    assert interpretation["fact_check_result"] == "trimmed"
    assert interpretation["fact_check_version"] == FACT_CHECK_VERSION
    assert interpretation["generator_model"] == "qwen3.5:27b"
    assert interpretation["fact_check_notes"]


def test_fallback_interpretation_is_marked_source_only() -> None:
    interpretation = _fallback_interpretation(
        detail={"catchTitle": "Education funding", "bill": "HB0001", "billTitle": "AN ACT relating to schools"},
        official_summary_text="This bill changes school funding rules.",
        official_digest_text="",
        current_bill_text="",
        generator_model="qwen3.5:27b",
    )

    assert interpretation["fact_check_status"] == "fallback"
    assert interpretation["fact_check_result"] == "source-only"
    assert interpretation["fact_check_version"] == FACT_CHECK_VERSION
    assert interpretation["generator_model"] == "qwen3.5:27b"
    assert "official source text" in interpretation["fact_check_notes"][0]


def test_reusable_interpretation_is_kept_when_source_hash_matches() -> None:
    existing = {
        "source_hash": "same-hash",
        "interpretation_json": {
            "plain_language_title": "Education funding",
            "one_sentence_summary": "This bill changes school funding rules.",
            "generator_model": "qwen3.5:27b",
        },
    }

    reused = _reusable_interpretation(existing, "same-hash", "qwen3.5:27b")

    assert reused is not None
    assert reused["one_sentence_summary"] == "This bill changes school funding rules."


def test_reusable_interpretation_is_not_kept_when_model_changes() -> None:
    existing = {
        "source_hash": "same-hash",
        "interpretation_json": {
            "plain_language_title": "Education funding",
            "one_sentence_summary": "This bill changes school funding rules.",
            "generator_model": "qwen2.5:7b-instruct",
        },
    }

    reused = _reusable_interpretation(existing, "same-hash", "qwen3.5:27b")

    assert reused is None


def test_source_only_sync_preserves_interpretation_when_source_changes() -> None:
    existing = {
        "source_hash": "old-hash",
        "interpretation_json": {
            "plain_language_title": "Education funding",
            "one_sentence_summary": "This bill changes school funding rules.",
            "generator_model": "older-model",
        },
    }

    reused = _interpretation_for_sync(
        existing,
        "new-hash",
        "current-model",
        skip_interpretation=True,
    )

    assert reused is not None
    assert reused["one_sentence_summary"] == "This bill changes school funding rules."
    assert reused["fact_check_status"] == "stale"
    assert reused["fact_check_result"] == "source-changed"


def test_interpreting_sync_refreshes_interpretation_when_source_changes() -> None:
    existing = {
        "source_hash": "old-hash",
        "interpretation_json": {
            "one_sentence_summary": "This bill changes school funding rules.",
            "generator_model": "current-model",
        },
    }

    reused = _interpretation_for_sync(
        existing,
        "new-hash",
        "current-model",
        skip_interpretation=False,
    )

    assert reused is None


def test_repair_missing_interpretations_only_repairs_blank_summaries(monkeypatch) -> None:
    existing_summary = {
        "plain_language_title": "Existing title",
        "one_sentence_summary": "Keep this summary.",
    }
    with connect() as connection:
        connection.executemany(
            """
            INSERT INTO bills (
                state, year, special_session_key, bill_num, catch_title,
                official_summary_text, interpretation_json, source_synced_at,
                created_at, updated_at
            ) VALUES (?, ?, -1, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "wy",
                    2026,
                    "HB0001",
                    "School funding",
                    "This bill changes school funding rules.",
                    None,
                    "2026-07-31T12:00:00+00:00",
                    "2026-07-31T12:00:00+00:00",
                    "2026-07-31T12:00:00+00:00",
                ),
                (
                    "wy",
                    2026,
                    "HB0002",
                    "Teacher pay",
                    "This bill changes teacher pay.",
                    json.dumps(existing_summary),
                    "2026-07-31T12:00:00+00:00",
                    "2026-07-31T12:00:00+00:00",
                    "2026-07-31T12:00:00+00:00",
                ),
            ],
        )
        connection.commit()

    calls: list[str] = []

    def fake_interpret(**kwargs):
        calls.append(str(kwargs["bill"]["bill"]))
        return (
            {
                "plain_language_title": "School funding",
                "one_sentence_summary": "This bill changes school funding rules.",
                "what_it_does": ["It changes school funding rules."],
            },
            1,
            1,
            0,
        )

    monkeypatch.setattr(sync_service, "_interpret_bill_text", fake_interpret)

    stats = repair_missing_interpretations(state="wy", years=[2026])

    repaired = get_bill("wy", 2026, "HB0001")
    preserved = get_bill("wy", 2026, "HB0002")
    assert stats.repaired == 1
    assert stats.skipped == 1
    assert calls == ["HB0001"]
    assert repaired is not None
    assert repaired["interpretation_json"]["one_sentence_summary"] == "This bill changes school funding rules."
    assert preserved is not None
    assert preserved["interpretation_json"] == existing_summary


def test_source_hash_ignores_status_only_changes() -> None:
    detail_a = {
        "bill": "HB0001",
        "catchTitle": "Education funding",
        "billTitle": "AN ACT relating to schools",
        "currentVersionPath": "https://example.test/HB0001.pdf",
        "currentVersionFingerprint": "fingerprint-1",
        "lastAction": "Introduced",
        "signedDate": "",
        "chapter": "",
    }
    detail_b = {
        **detail_a,
        "lastAction": "Governor signed",
        "signedDate": "2026-03-05",
        "chapter": "0042",
    }

    hash_a = _compute_source_hash(
        detail_a,
        official_summary_text="This bill changes school funding rules.",
        official_digest_text="The bill adjusts how money moves to districts.",
        current_bill_text="Section 1. School funding is updated.",
    )
    hash_b = _compute_source_hash(
        detail_b,
        official_summary_text="This bill changes school funding rules.",
        official_digest_text="The bill adjusts how money moves to districts.",
        current_bill_text="Section 1. School funding is updated.",
    )

    assert hash_a == hash_b


def test_indexed_sync_order_covers_every_jurisdiction_once() -> None:
    assert len(sync_service.SYNC_STATE_ORDER) == 52
    assert len(set(sync_service.SYNC_STATE_ORDER)) == 52
    assert set(sync_service.SYNC_STATE_ORDER) == set(sync_service.SYNC_STATE_FUNCTIONS)
    assert sync_service.SYNC_STATE_ORDER[:2] == ("wyoming", "federal")


def test_sync_state_by_name_forwards_source_only_mode(monkeypatch) -> None:
    calls: list[tuple[bool, object]] = []
    logger = calls.append

    def fake_sync(*, skip_interpretation: bool, logger):
        calls.append((skip_interpretation, logger))
        return sync_service.SyncStats(years=[2026])

    monkeypatch.setitem(sync_service.SYNC_STATE_FUNCTIONS, "wyoming", fake_sync)

    stats = sync_service.sync_state_by_name(
        "Wyoming",
        skip_interpretation=True,
        logger=logger,
    )

    assert stats.years == [2026]
    assert calls == [(True, logger)]


def test_sync_states_forwards_source_only_mode(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_sync_state_by_name(state_name: str, *, skip_interpretation: bool, logger):
        calls.append((state_name, skip_interpretation))
        return sync_service.SyncStats(years=[2026])

    monkeypatch.setattr(sync_service, "sync_state_by_name", fake_sync_state_by_name)
    monkeypatch.setattr(sync_service, "reset_stale_sync_statuses", lambda *_args, **_kwargs: 0)

    completed, failed = sync_service.sync_states(
        ["wyoming", "federal"],
        skip_interpretation=True,
    )

    assert list(completed) == ["wyoming", "federal"]
    assert failed == {}
    assert calls == [("wyoming", True), ("federal", True)]
