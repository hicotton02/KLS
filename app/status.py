from __future__ import annotations


def classify_bill_status(
    bill_status: str | None,
    last_action: str | None,
    signed_date: str | None,
    chapter_no: str | None,
    enrolled_no: str | None,
) -> dict[str, str]:
    raw_status = (bill_status or "").strip().lower()
    raw_action = (last_action or "").strip().lower()
    chapter_no = (chapter_no or "").strip()
    enrolled_no = (enrolled_no or "").strip()

    if "mirror bill" in raw_action:
        return {
            "label": "Mirror Bill Used",
            "outcome": "replaced",
            "explanation": "This bill stopped moving because a mirror bill carried the same proposal forward instead.",
        }

    enacted_markers = [
        "assigned chapter number",
        "governor signed",
        "date signed by governor",
        "signed by governor",
        "approved by governor",
        "signed act",
        "became pub. ch.",
        "became public chapter",
        "chaptered",
        "became law",
    ]
    if signed_date or chapter_no or any(marker in raw_action for marker in enacted_markers):
        return {
            "label": "Enacted",
            "outcome": "passed",
            "explanation": "This bill passed the Legislature and reached final enactment based on the latest official action.",
        }

    if "veto" in raw_action:
        return {
            "label": "Vetoed",
            "outcome": "failed",
            "explanation": "The latest official action shows the governor vetoed this bill. Check the bill history to see whether lawmakers later overrode that veto.",
        }

    passed_markers = [
        "sent to the governor",
        "transmitted to governor",
        "delivered to governor",
        "signed by the speaker of the house",
        "signed by the president of the senate",
        "sent for executive approval",
    ]
    if "enrolled" in raw_status or enrolled_no or any(marker in raw_action for marker in passed_markers):
        return {
            "label": "Passed Legislature",
            "outcome": "passed",
            "explanation": "This bill passed both chambers and reached final enrollment, even if later executive action is not shown here.",
        }

    failed_markers = [
        "failed",
        "did not consider",
        "postponed indefinitely",
        "postpone indefinitely",
        "no report prior to",
        "indefinitely postponed",
        "did not pass",
        "died in committee",
        "died on calendar",
        "lost",
        "failed in committee",
    ]
    if any(marker in raw_action for marker in failed_markers):
        return {
            "label": "Did Not Pass",
            "outcome": "failed",
            "explanation": "The latest official action shows that this bill did not move forward in that session.",
        }

    if raw_status == "inactive":
        return {
            "label": "Inactive",
            "outcome": "failed",
            "explanation": "Wyoming marks this bill as inactive, which usually means it is no longer moving in the current session.",
        }

    return {
        "label": "Active",
        "outcome": "active",
        "explanation": "The official status still shows this bill as active or still awaiting another formal step.",
    }


def classify_federal_bill_status(latest_action: str | None, law_number: str | None = None) -> dict[str, str]:
    raw_action = (latest_action or "").strip().lower()
    law_number = (law_number or "").strip()

    if law_number or "became public law" in raw_action or "became private law" in raw_action or "signed by president" in raw_action:
        return {
            "label": "Enacted",
            "outcome": "passed",
            "explanation": "The latest official action shows this bill completed Congress and was signed into law.",
        }

    if "veto" in raw_action:
        return {
            "label": "Vetoed",
            "outcome": "failed",
            "explanation": "The latest official action shows the President vetoed this bill. Check the official history to see whether Congress later overrode that veto.",
        }

    if "presented to president" in raw_action or "cleared for white house" in raw_action:
        return {
            "label": "Passed Congress",
            "outcome": "passed",
            "explanation": "The latest official action shows this bill passed both chambers and was sent to the President.",
        }

    failed_markers = [
        "failed of passage",
        "failed to pass",
        "not agreed to",
        "rejected",
        "indefinitely postponed",
        "laid on table",
        "veto sustained",
    ]
    if any(marker in raw_action for marker in failed_markers):
        return {
            "label": "Did Not Pass",
            "outcome": "failed",
            "explanation": "The latest official action shows this bill did not move forward to final passage.",
        }

    chamber_markers = [
        "passed house",
        "passed senate",
        "agreed to in house",
        "agreed to in senate",
    ]
    if any(marker in raw_action for marker in chamber_markers):
        return {
            "label": "Passed One Chamber",
            "outcome": "active",
            "explanation": "The latest official action shows this bill passed one chamber and may still need more steps before it can become law.",
        }

    return {
        "label": "Active",
        "outcome": "active",
        "explanation": "The latest official action still shows this bill moving through Congress or waiting on another formal step.",
    }
