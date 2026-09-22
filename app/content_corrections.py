from __future__ import annotations

from copy import deepcopy


WY_BUDGET_SOURCE_HASH = "e7acfda1a258c822be39ee2b0d9b31692bb009c7baa6c937cf4ad42792905674"
WY_BUDGET_SOURCE = "https://www.wyoleg.gov/2026/Summaries/SF0001.pdf"


def apply_content_corrections(bill: dict) -> dict:
    """Keep a source-version-specific correction from being overwritten by a refresh."""
    if (bill.get("state"), bill.get("year"), bill.get("bill_num")) != ("wy", 2026, "SF0001"):
        return bill
    if bill.get("special_session_value") or bill.get("source_hash") != WY_BUDGET_SOURCE_HASH:
        return bill
    original = bill.get("interpretation_json")
    if not isinstance(original, dict) or original.get("fact_check_status") != "validated":
        return bill
    corrected = dict(bill)
    explanation = deepcopy(original)
    explanation["what_it_does"] = [
        "Sets aside $10,099,046,462 (about $10.10 billion) for state operations, including the changes and conditions listed in the source summary.",
        "Sets staffing limits and funding rules for state agencies, schools, and universities for July 1, 2026, through June 30, 2028.",
        "Transfers money between state accounts and allows some unused funds to carry into the next budget period.",
        "Requires reports and audits for specified programs.",
    ]
    explanation["limits_and_unknowns"] = [
        "Some appropriations depend on other legislation or conditions listed in the source.",
        "The source crosses out the earlier $9,989,774,932 total and replaces it with $10,099,046,462. It identifies increases from vetoes in the General Fund and Other Funds.",
        "Some sections have different effective dates. Check the official text for a specific program.",
    ]
    explanation["removed_claims"] = []
    explanation["validator_notes"] = []
    explanation["fact_check_result"] = "source-correction"
    explanation["fact_check_notes"] = ["Correction checked against the replacement amounts on page 1 of the source summary."]
    explanation["correction"] = {
        "date": "2026-09-21", "source_url": WY_BUDGET_SOURCE,
        "note": "Corrected the budget total and veto explanation to match the source's revised figures.",
    }
    corrected["interpretation_json"] = explanation
    return corrected
