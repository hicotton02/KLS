from app.relationship_service import _build_profile, _find_candidate_pairs


def _bill(
    bill_num: str,
    catch_title: str,
    summary: str,
    *,
    outcome: str = "passed",
) -> dict[str, object]:
    return {
        "state": "wy",
        "year": 2026,
        "special_session_value": None,
        "bill_num": bill_num,
        "catch_title": catch_title,
        "bill_title": catch_title,
        "sponsor": "Test Committee",
        "outcome": outcome,
        "status_label": "Active" if outcome == "active" else "Passed",
        "official_summary_text": summary,
        "official_digest_text": "",
        "current_bill_text": summary,
    }


def test_find_candidate_pairs_surfaces_related_bills() -> None:
    bill_a = _build_profile(
        _bill(
            "HB0003",
            "Pregnancy center protections.",
            "This bill protects pregnancy centers from discrimination and creates legal protections for pregnancy services.",
        )
    )
    bill_b = _build_profile(
        _bill(
            "HB0126",
            "Abortion restrictions and enforcement.",
            "This bill prohibits abortions after a detectable heartbeat and allows civil action and enforcement by the attorney general.",
        )
    )
    unrelated = _build_profile(
        _bill(
            "HB0099",
            "Road maintenance funding.",
            "This bill provides highway funding and transfers money for bridge repairs.",
        )
    )

    candidates = _find_candidate_pairs([bill_a, bill_b, unrelated], limit=10)
    candidate_pairs = {(item.bill_a.bill_num, item.bill_b.bill_num) for item in candidates}

    assert ("HB0003", "HB0126") in candidate_pairs
    assert ("HB0003", "HB0099") not in candidate_pairs
