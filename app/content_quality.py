from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit


def _text(value: object) -> str:
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def has_official_source(bill: Mapping[str, object]) -> bool:
    if bill.get("state") == "wy" and bill.get("year") and bill.get("bill_num"):
        return True  # Wyoming has a stable official bill-page URL.
    for key in ("summary_path", "introduced_path", "current_version_path", "digest_path"):
        try:
            url = urlsplit(str(bill.get(key) or ""))
        except ValueError:
            continue
        if url.scheme in {"http", "https"} and url.hostname:
            return True
    return False


def assess_bill_content(bill: Mapping[str, object], *, has_vote_record: bool = False) -> dict[str, object]:
    raw = bill.get("interpretation_json")
    explanation = raw if isinstance(raw, dict) else {}
    summary = _text(explanation.get("one_sentence_summary"))
    titles = {_text(bill.get(key)) for key in ("bill_num", "catch_title", "bill_title")}
    points = explanation.get("what_it_does")
    distinct_points = isinstance(points, list) and any(
        isinstance(point, str) and _text(point) and _text(point) not in titles | {summary}
        for point in points
    )
    source = has_official_source(bill)
    stale = explanation.get("fact_check_status") == "stale"
    ready = bool(source and summary and summary not in titles and distinct_points
                 and explanation.get("fact_check_status") == "validated")
    useful_votes = bool(source and has_vote_record)
    notice = ""
    if not ready:
        notice = ("This explanation needs another check against the official record."
                  if stale else "A plain-English explanation is not ready. Check the official record below.")
        if not source:
            notice = "This record is incomplete. We are checking its source and explanation."
    return {
        "summary_ready": ready,
        "indexable": ready or useful_votes,
        "ads_eligible": ready,
        "featured": ready,
        "has_source": source,
        "state": "summary" if ready else "vote_record" if useful_votes else "reference",
        "notice": notice,
    }
