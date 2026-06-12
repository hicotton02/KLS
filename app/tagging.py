from __future__ import annotations

import re
from functools import lru_cache


PUBLIC_TAG_PATTERNS = {
    "abortion": [
        "abortion",
        "abortions",
        "termination of pregnancy",
        "terminations of pregnancy",
        "pregnancy center",
        "pregnancy centers",
        "heartbeat act",
    ],
    "agriculture": ["agriculture", "livestock", "ranch", "ranching", "farm", "farming"],
    "budget": ["appropriation", "appropriations", "budget", "budgets", "transfer of funds"],
    "children": ["child", "children", "minor", "minors", "juvenile", "juveniles"],
    "crime": ["crime", "felony", "misdemeanor", "sentencing", "prison", "jail", "criminal justice"],
    "education": ["school", "schools", "student", "students", "teacher", "teachers", "education"],
    "elections": ["election", "elections", "ballot", "voter", "voters", "voting"],
    "energy": ["energy", "oil", "gas", "coal", "pipeline", "wind", "solar", "nuclear"],
    "firearms": ["firearm", "firearms", "gun", "guns", "weapon", "weapons", "second amendment"],
    "healthcare": ["health care", "healthcare", "medical", "hospital", "hospitals", "clinic", "clinics"],
    "housing": ["housing", "tenant", "tenants", "landlord", "landlords", "rent", "rental"],
    "labor": ["worker", "workers", "employee", "employees", "employer", "employers", "wage", "wages", "union", "unions"],
    "land": ["land use", "landowner", "landowners", "property rights", "property owner", "zoning", "mineral", "minerals"],
    "parental-rights": ["parental rights", "parent rights", "parents' rights", "guardian", "guardians"],
    "privacy": ["privacy", "private information", "personal information", "data breach", "surveillance"],
    "small-business": ["small business", "small businesses", "small-business", "startup", "startups", "entrepreneur", "entrepreneurs", "smb"],
    "taxes": ["tax", "taxes", "property tax", "sales tax", "assessment", "revenue"],
    "technology": ["technology", "digital asset", "digital assets", "blockchain", "crypto", "cybersecurity", "artificial intelligence", "ai"],
    "water": ["water rights", "water right", "groundwater", "irrigation", "watershed", "aquifer"],
}

PUBLIC_TAG_LABELS = {
    "abortion": "Abortion",
    "agriculture": "Agriculture",
    "budget": "Budget",
    "children": "Children",
    "crime": "Crime",
    "education": "Education",
    "elections": "Elections",
    "energy": "Energy",
    "firearms": "Firearms",
    "healthcare": "Healthcare",
    "housing": "Housing",
    "labor": "Labor",
    "land": "Land",
    "parental-rights": "Parental Rights",
    "privacy": "Privacy",
    "small-business": "Small Business",
    "taxes": "Taxes",
    "technology": "Technology",
    "water": "Water",
}

STRONG_TAG_SOURCE_WEIGHT = 3
MEDIUM_TAG_SOURCE_WEIGHT = 2
TAG_SCORE_THRESHOLD = 3


def extract_bill_tags(
    *,
    catch_title: str | None = None,
    sponsor: str | None = None,
    official_summary_text: str | None = None,
    official_digest_text: str | None = None,
    interpretation: dict[str, object] | None = None,
    amendment_snippets: list[str] | None = None,
) -> list[str]:
    strong_sources = [
        catch_title,
        sponsor,
        _interpretation_search_text(interpretation),
        " ".join(str(item or "") for item in amendment_snippets or []),
    ]
    medium_sources = [
        official_summary_text,
        official_digest_text,
    ]

    tags: list[str] = []
    for tag, patterns in PUBLIC_TAG_PATTERNS.items():
        score = 0
        for source in strong_sources:
            score += _tag_source_score(source, patterns, STRONG_TAG_SOURCE_WEIGHT)
        for source in medium_sources:
            score += _tag_source_score(source, patterns, MEDIUM_TAG_SOURCE_WEIGHT)
        if score >= TAG_SCORE_THRESHOLD:
            tags.append(tag)
    return sorted(tags)


def _interpretation_search_text(interpretation: dict[str, object] | None) -> str:
    if not isinstance(interpretation, dict):
        return ""
    parts = [
        str(interpretation.get("plain_language_title") or ""),
        str(interpretation.get("one_sentence_summary") or ""),
        " ".join(str(item or "") for item in interpretation.get("what_it_does", []) or []),
        " ".join(str(item or "") for item in interpretation.get("who_it_affects", []) or []),
        " ".join(str(item or "") for item in interpretation.get("terms_to_know", []) or []),
    ]
    return " ".join(part for part in parts if part.strip())


def _tag_source_score(source_text: str | None, patterns: list[str], base_weight: int) -> int:
    normalized_source = str(source_text or "").strip().lower()
    if not normalized_source:
        return 0
    hits = sum(1 for pattern in patterns if _contains_pattern(normalized_source, pattern))
    if hits <= 0:
        return 0
    return base_weight + min(hits - 1, 1)


def _contains_pattern(source_text: str, pattern: str) -> bool:
    return bool(_pattern_regex(pattern).search(source_text))


@lru_cache(maxsize=256)
def _pattern_regex(pattern: str) -> re.Pattern[str]:
    normalized = str(pattern or "").strip().lower()
    escaped = re.escape(normalized)
    escaped = escaped.replace(r"\ ", r"[\s\-]+")
    escaped = escaped.replace(r"\-", r"[\s\-]+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def tag_label(tag: str) -> str:
    normalized = str(tag or "").strip().lower()
    if not normalized:
        return ""
    return PUBLIC_TAG_LABELS.get(normalized, normalized.replace("-", " ").title())
