from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
import re
from typing import Callable

from app.db import list_bills, normalize_special_session, replace_bill_relationships
from app.ollama import OllamaClient
from app.settings import Settings, get_settings
from app.text_utils import first_non_empty, iso_now, truncate_for_prompt


Logger = Callable[[str], None]
RELATIONSHIP_ANALYSIS_VERSION = 1

STOPWORDS = {
    "act",
    "acts",
    "agency",
    "agencies",
    "amend",
    "bill",
    "bills",
    "chapter",
    "committee",
    "date",
    "department",
    "effective",
    "government",
    "law",
    "laws",
    "office",
    "official",
    "provide",
    "provides",
    "providing",
    "relating",
    "section",
    "sections",
    "shall",
    "state",
    "states",
    "summary",
    "title",
    "wyoming",
}

TOPIC_PATTERNS = {
    "abortion": ["abortion", "terminations of pregnancy", "termination of pregnancy", "reproductive", "heartbeat", "pregnancy"],
    "children": ["child", "children", "minor", "minors", "parental"],
    "education": ["school", "schools", "education", "student", "students", "scholarship", "teacher"],
    "elections": ["election", "elections", "ballot", "voter", "voting"],
    "firearms": ["firearm", "firearms", "gun", "guns", "weapon", "weapons"],
    "healthcare": ["health", "medical", "hospital", "hospitals", "clinic", "clinics"],
    "insurance": ["insurance", "insurer", "insurers", "coverage"],
    "labor": ["employee", "employees", "employment", "employer", "wages", "worker", "workers"],
    "land": ["land", "property", "zoning", "mineral", "minerals"],
    "licensing": ["license", "licenses", "licensing", "permit", "permits", "registration"],
    "crime": ["crime", "criminal", "felony", "misdemeanor", "sentencing", "penalty", "penalties"],
    "taxes": ["tax", "taxes", "sales tax", "property tax", "assessment", "revenue"],
}

ACTION_PATTERNS = {
    "definitions": ["define", "defines", "definition", "means", "term means"],
    "enforcement": ["enforce", "enforcement", "injunction", "attorney general", "civil action"],
    "funding": ["appropriation", "appropriations", "funding", "grant", "grants", "loan", "loans"],
    "notice": ["notice", "notices", "disclosure", "disclosures", "inform", "informed"],
    "penalties": ["penalty", "penalties", "felony", "misdemeanor", "punishable", "liability"],
    "prohibition": ["prohibit", "prohibits", "ban", "bans", "forbid", "forbids"],
    "reporting": ["report", "reports", "reporting", "recordkeeping", "records"],
    "rights": ["right", "rights", "protection", "protections", "discrimination", "autonomy"],
}

COMPLEMENTARY_ACTIONS = {
    frozenset({"definitions", "enforcement"}),
    frozenset({"definitions", "penalties"}),
    frozenset({"funding", "rights"}),
    frozenset({"notice", "prohibition"}),
    frozenset({"penalties", "rights"}),
    frozenset({"prohibition", "rights"}),
    frozenset({"prohibition", "enforcement"}),
    frozenset({"reporting", "enforcement"}),
}

STATUTE_PATTERN = re.compile(
    r"\b(?:W\.S\.\s*)?(?:\d{1,2}-\d{1,3}-\d{1,4}(?:\([a-z0-9]+\))?)\b",
    re.IGNORECASE,
)
TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9']{2,}")


@dataclass(frozen=True)
class BillProfile:
    state: str
    year: int
    special_session_value: int | None
    special_session_key: int
    bill_num: str
    catch_title: str
    bill_title: str
    sponsor: str
    outcome: str
    status_label: str
    summary_text: str
    digest_text: str
    current_text: str
    citations: frozenset[str]
    topic_tags: frozenset[str]
    action_tags: frozenset[str]
    tokens: frozenset[str]

    @property
    def display_title(self) -> str:
        return self.catch_title or self.bill_title or self.bill_num


@dataclass(frozen=True)
class CandidatePair:
    bill_a: BillProfile
    bill_b: BillProfile
    candidate_score: float
    heuristic_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RelationshipStats:
    candidates: int = 0
    saved: int = 0
    failed: int = 0


def analyze_bill_relationships_for_year(
    state: str,
    year: int,
    *,
    settings: Settings | None = None,
    ollama: OllamaClient | None = None,
    logger: Logger | None = None,
) -> RelationshipStats:
    log = logger or (lambda message: None)
    source_bills = list_bills(state, year)
    profiles = [
        _build_profile(bill)
        for bill in source_bills
        if (bill.get("outcome") or "") not in {"failed", "replaced"}
    ]
    candidates = _find_candidate_pairs(profiles)
    if not candidates:
        replace_bill_relationships(state, year, [])
        return RelationshipStats()

    client = ollama
    owns_client = False
    if client is None:
        client = OllamaClient(settings or get_settings())
        owns_client = True

    timestamp = iso_now()
    payloads: list[dict[str, object]] = []
    failed = 0
    try:
        for candidate in candidates:
            try:
                analysis = client.analyze_bill_relationship(candidate.bill_a.__dict__, candidate.bill_b.__dict__, candidate.heuristic_reasons)
                payload = _build_relationship_payload(candidate, analysis, timestamp)
                if payload is not None:
                    payloads.append(payload)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                log(f"Relationship analysis failed for {candidate.bill_a.bill_num} + {candidate.bill_b.bill_num}: {exc}")
    finally:
        if owns_client:
            client.close()

    replace_bill_relationships(state, year, payloads)
    return RelationshipStats(candidates=len(candidates), saved=len(payloads), failed=failed)


def _build_profile(bill: dict[str, object]) -> BillProfile:
    summary_text = str(bill.get("official_summary_text") or "")
    digest_text = str(bill.get("official_digest_text") or "")
    current_text = str(bill.get("current_bill_text") or "")
    source_text = " ".join(
        value
        for value in [
            str(bill.get("catch_title") or ""),
            str(bill.get("bill_title") or ""),
            truncate_for_prompt(summary_text, 2500),
            truncate_for_prompt(digest_text, 2500),
            truncate_for_prompt(current_text, 3500),
        ]
        if value
    )
    return BillProfile(
        state=str(bill.get("state") or ""),
        year=int(bill.get("year") or 0),
        special_session_value=bill.get("special_session_value") if bill.get("special_session_value") is not None else None,
        special_session_key=normalize_special_session(bill.get("special_session_value")),
        bill_num=str(bill.get("bill_num") or ""),
        catch_title=str(bill.get("catch_title") or ""),
        bill_title=str(bill.get("bill_title") or ""),
        sponsor=str(bill.get("sponsor") or ""),
        outcome=str(bill.get("outcome") or ""),
        status_label=str(bill.get("status_label") or ""),
        summary_text=summary_text,
        digest_text=digest_text,
        current_text=current_text,
        citations=_extract_citations(source_text),
        topic_tags=_extract_tags(source_text, TOPIC_PATTERNS),
        action_tags=_extract_tags(source_text, ACTION_PATTERNS),
        tokens=_extract_tokens(source_text),
    )


def _extract_citations(value: str) -> frozenset[str]:
    return frozenset(match.lower().replace("w.s.", "").strip() for match in STATUTE_PATTERN.findall(value))


def _extract_tags(value: str, mapping: dict[str, list[str]]) -> frozenset[str]:
    lowered = value.lower()
    tags = {tag for tag, patterns in mapping.items() if any(pattern in lowered for pattern in patterns)}
    return frozenset(tags)


def _extract_tokens(value: str) -> frozenset[str]:
    tokens = {
        token
        for token in TOKEN_PATTERN.findall(value.lower())
        if token not in STOPWORDS and not token.isdigit()
    }
    return frozenset(tokens)


def _find_candidate_pairs(profiles: list[BillProfile], limit: int = 40) -> list[CandidatePair]:
    candidates: list[CandidatePair] = []
    for bill_a, bill_b in combinations(profiles, 2):
        candidate = _score_pair(bill_a, bill_b)
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            item.candidate_score,
            item.bill_a.bill_num,
            item.bill_b.bill_num,
        ),
        reverse=True,
    )
    return candidates[:limit]


def _score_pair(bill_a: BillProfile, bill_b: BillProfile) -> CandidatePair | None:
    score = 0.0
    reasons: list[str] = []

    shared_citations = sorted(bill_a.citations & bill_b.citations)
    if shared_citations:
        score += 8 + min(2, len(shared_citations))
        reasons.append(f"Shared statute references: {', '.join(shared_citations[:3])}")

    shared_topics = sorted(bill_a.topic_tags & bill_b.topic_tags)
    if shared_topics:
        score += 3 * len(shared_topics)
        reasons.append(f"Shared issue tags: {', '.join(shared_topics[:3])}")

    overlapping_tokens = sorted(token for token in (bill_a.tokens & bill_b.tokens) if len(token) >= 5)
    if len(overlapping_tokens) >= 2:
        score += min(4, len(overlapping_tokens) / 2)
        reasons.append(f"Shared bill terms: {', '.join(overlapping_tokens[:4])}")

    if shared_topics and _has_complementary_actions(bill_a.action_tags, bill_b.action_tags):
        score += 3
        reasons.append("The bills mix different rule types on the same issue, such as rights, penalties, notice, or enforcement.")

    if bill_a.sponsor and bill_a.sponsor == bill_b.sponsor and shared_topics:
        score += 1
        reasons.append(f"Same sponsor or committee: {bill_a.sponsor}")

    if score < 6:
        return None

    first, second = _canonical_pair(bill_a, bill_b)
    return CandidatePair(
        bill_a=first,
        bill_b=second,
        candidate_score=round(score, 2),
        heuristic_reasons=tuple(reasons),
    )


def _has_complementary_actions(tags_a: frozenset[str], tags_b: frozenset[str]) -> bool:
    if not tags_a or not tags_b:
        return False
    for tag_a in tags_a:
        for tag_b in tags_b:
            if tag_a == tag_b:
                continue
            if frozenset({tag_a, tag_b}) in COMPLEMENTARY_ACTIONS:
                return True
    return False


def _canonical_pair(bill_a: BillProfile, bill_b: BillProfile) -> tuple[BillProfile, BillProfile]:
    left_key = (bill_a.special_session_key, bill_a.bill_num)
    right_key = (bill_b.special_session_key, bill_b.bill_num)
    if left_key <= right_key:
        return bill_a, bill_b
    return bill_b, bill_a


def _build_relationship_payload(
    candidate: CandidatePair,
    analysis: dict[str, object],
    timestamp: str,
) -> dict[str, object] | None:
    if not bool(analysis.get("is_material_relationship")):
        return None

    pair_summary = str(analysis.get("pair_summary") or "").strip()
    combined_effect = str(analysis.get("combined_effect") or "").strip()
    bill_a_evidence = [str(item).strip() for item in analysis.get("bill_a_evidence", []) if str(item).strip()]
    bill_b_evidence = [str(item).strip() for item in analysis.get("bill_b_evidence", []) if str(item).strip()]
    if not pair_summary or not combined_effect or not bill_a_evidence or not bill_b_evidence:
        return None

    relationship_strength = str(analysis.get("relationship_strength") or "low").strip().lower()
    if relationship_strength not in {"low", "medium", "high"}:
        relationship_strength = "low"

    relationship_type = str(analysis.get("relationship_type") or "other").strip().lower()
    needs_human_review = bool(analysis.get("needs_human_review", True))
    confidence_score = {
        "low": 0.45,
        "medium": 0.68,
        "high": 0.84,
    }[relationship_strength]

    return {
        "state": candidate.bill_a.state,
        "year": candidate.bill_a.year,
        "special_session_value_a": candidate.bill_a.special_session_value,
        "bill_num_a": candidate.bill_a.bill_num,
        "special_session_value_b": candidate.bill_b.special_session_value,
        "bill_num_b": candidate.bill_b.bill_num,
        "relationship_type": relationship_type,
        "relationship_strength": relationship_strength,
        "confidence_score": confidence_score,
        "candidate_score": candidate.candidate_score,
        "needs_human_review": needs_human_review,
        "pair_summary": pair_summary,
        "combined_effect": combined_effect,
        "why_review": str(analysis.get("why_review") or "").strip(),
        "bill_a_evidence_json": bill_a_evidence,
        "bill_b_evidence_json": bill_b_evidence,
        "limits_and_unknowns_json": [
            str(item).strip() for item in analysis.get("limits_and_unknowns", []) if str(item).strip()
        ],
        "heuristic_reasons_json": list(candidate.heuristic_reasons),
        "analysis_version": RELATIONSHIP_ANALYSIS_VERSION,
        "source_synced_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def relationship_bill_href(state_slug: str, relationship: dict[str, object], side: str) -> str:
    bill_num = relationship[f"bill_num_{side}"]
    year = relationship["year"]
    special_session_value = relationship.get(f"special_session_value_{side}")
    href = f"/states/{state_slug}/bills/{year}/{bill_num}"
    if special_session_value is not None:
        href = f"{href}?special_session={special_session_value}"
    return href


def relationship_peer(relationship: dict[str, object], bill_num: str, special_session_value: int | None) -> dict[str, object]:
    special_session_key = normalize_special_session(special_session_value)
    if relationship.get("bill_num_a") == bill_num and relationship.get("special_session_key_a") == special_session_key:
        return {
            "bill_num": relationship.get("bill_num_b"),
            "catch_title": relationship.get("bill_b_catch_title"),
            "outcome": relationship.get("bill_b_outcome"),
            "status_label": relationship.get("bill_b_status_label"),
            "special_session_value": relationship.get("special_session_value_b"),
        }
    return {
        "bill_num": relationship.get("bill_num_a"),
        "catch_title": relationship.get("bill_a_catch_title"),
        "outcome": relationship.get("bill_a_outcome"),
        "status_label": relationship.get("bill_a_status_label"),
        "special_session_value": relationship.get("special_session_value_a"),
    }


def build_pair_prompt_payload(bill: BillProfile) -> str:
    return f"""
Bill number: {bill.bill_num}
Catch title: {bill.catch_title}
Bill title: {bill.bill_title}
Sponsor: {bill.sponsor}
Outcome: {bill.status_label or bill.outcome}
Issue tags: {', '.join(sorted(bill.topic_tags)) or '[none found]'}
Rule tags: {', '.join(sorted(bill.action_tags)) or '[none found]'}
Summary excerpt:
{truncate_for_prompt(first_non_empty(bill.summary_text, bill.digest_text, bill.current_text, bill.bill_title), 2200)}
""".strip()
