from app.sync_service import (
    FACT_CHECK_VERSION,
    _compute_source_hash,
    _fallback_interpretation,
    _mark_validated_interpretation,
    _needs_refresh,
    _reusable_interpretation,
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
