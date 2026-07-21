from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any


VOTE_POSITIONS = (
    ("yesVotes", "yes"),
    ("noVotes", "no"),
    ("absentVotes", "absent"),
    ("conflictVotes", "conflict"),
    ("excusedVotes", "excused"),
)
VOTE_POSITION_ORDER = {position: index for index, (_, position) in enumerate(VOTE_POSITIONS)}


def normalize_chamber(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if normalized in {"h", "house"}:
        return "H"
    if normalized in {"s", "senate"}:
        return "S"
    return str(value or "").strip().upper()


def chamber_title(chamber: object) -> str:
    return "Senator" if normalize_chamber(chamber) == "S" else "Representative"


def _slugify(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold()).strip("-")
    return slug or "unknown"


def _name_initials(value: object) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalpha())


def _roster_indexes(
    legislators_by_chamber: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, dict[str, Any]]:
    indexes: dict[str, dict[str, Any]] = {}
    for raw_chamber, legislators in (legislators_by_chamber or {}).items():
        chamber = normalize_chamber(raw_chamber)
        by_last_name: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        by_full_name: dict[str, dict[str, Any]] = {}
        for legislator in legislators or []:
            last_name = str(legislator.get("lastName") or "").strip()
            full_name = str(legislator.get("name") or "").strip()
            if last_name:
                by_last_name[last_name.casefold()].append(legislator)
            if full_name:
                by_full_name[full_name.casefold()] = legislator
        indexes[chamber] = {"by_last_name": by_last_name, "by_full_name": by_full_name}
    return indexes


def _matching_initial_candidate(candidates: list[dict[str, Any]], initials: str) -> dict[str, Any] | None:
    normalized_initials = _name_initials(initials)
    if not normalized_initials:
        return None
    matches = [
        candidate
        for candidate in candidates
        if _name_initials(candidate.get("firstName")).startswith(normalized_initials)
    ]
    return matches[0] if len(matches) == 1 else None


def _split_vote_names(raw_names: object, chamber_index: dict[str, Any]) -> list[str]:
    parts = [part.strip() for part in str(raw_names or "").split(",") if part.strip()]
    if not parts:
        return []

    by_last_name: dict[str, list[dict[str, Any]]] = chamber_index.get("by_last_name", {})
    labels: list[str] = []
    index = 0
    while index < len(parts):
        label = parts[index]
        if index + 1 < len(parts):
            possible_initials = parts[index + 1]
            candidates = by_last_name.get(label.casefold(), [])
            candidate = _matching_initial_candidate(candidates, possible_initials)
            next_is_known_last_name = possible_initials.casefold() in by_last_name
            fallback_initial = (
                not next_is_known_last_name
                and 1 <= len(_name_initials(possible_initials)) <= 2
                and possible_initials.replace(".", "").isalpha()
                and possible_initials.upper() == possible_initials
            )
            if candidate is not None or fallback_initial:
                label = f"{label}, {possible_initials}"
                index += 1
        labels.append(label)
        index += 1
    return labels


def _resolve_legislator(
    label: str,
    chamber: str,
    roster_indexes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    chamber_index = roster_indexes.get(chamber, {})
    by_full_name: dict[str, dict[str, Any]] = chamber_index.get("by_full_name", {})
    by_last_name: dict[str, list[dict[str, Any]]] = chamber_index.get("by_last_name", {})

    candidate = by_full_name.get(label.casefold())
    if candidate is None:
        label_parts = [part.strip() for part in label.split(",", 1)]
        candidates = by_last_name.get(label_parts[0].casefold(), [])
        if len(label_parts) == 2:
            candidate = _matching_initial_candidate(candidates, label_parts[1])
        elif len(candidates) == 1:
            candidate = candidates[0]

    source_legislator_id = str((candidate or {}).get("legID") or "").strip()
    first_name = str((candidate or {}).get("firstName") or "").strip()
    last_name = str((candidate or {}).get("lastName") or "").strip()
    display_name = str((candidate or {}).get("name") or "").strip()
    if not display_name:
        display_name = " ".join(part for part in (first_name, last_name) if part) or label

    member_key = f"wy-{source_legislator_id}" if source_legislator_id else f"wy-{chamber.casefold()}-{_slugify(label)}"
    return {
        "member_key": member_key,
        "source_legislator_id": source_legislator_id or None,
        "legislator_name": display_name,
        "vote_label": label,
        "party": str((candidate or {}).get("party") or "").strip() or None,
        "district": str((candidate or {}).get("district") or "").strip() or None,
    }


def _normalize_vote_position(value: object) -> str:
    normalized = re.sub(r"[^a-z]", "", str(value or "").casefold())
    aliases = {
        "y": "yes",
        "yes": "yes",
        "yea": "yes",
        "aye": "yes",
        "n": "no",
        "no": "no",
        "nay": "no",
        "absent": "absent",
        "conflict": "conflict",
        "excused": "excused",
    }
    return aliases.get(normalized, normalized or "other")


def _int_value(value: object, fallback: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback


def _roll_call_key(roll_call: dict[str, Any], chamber: str) -> tuple[str, str]:
    raw_vote_id = roll_call.get("voteID")
    if raw_vote_id is None:
        raw_vote_id = roll_call.get("voteId")
    vote_id = str(raw_vote_id if raw_vote_id is not None else "").strip()
    if vote_id:
        return f"{chamber.casefold()}-{vote_id}", vote_id

    fingerprint = "|".join(
        [
            chamber,
            str(roll_call.get("voteDate") or ""),
            str(roll_call.get("action") or ""),
            str(roll_call.get("amendmentNumber") or ""),
        ]
    )
    return f"{chamber.casefold()}-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:16]}", ""


def build_wyoming_roll_calls(
    detail: dict[str, Any],
    legislators_by_chamber: dict[str, list[dict[str, Any]]] | None,
    *,
    timestamp: str,
) -> list[dict[str, Any]]:
    roster_indexes = _roster_indexes(legislators_by_chamber)
    payloads: list[dict[str, Any]] = []
    state = "wy"
    year = int(detail.get("year") or 0)
    bill_num = str(detail.get("bill") or "").strip()
    special_session_value = detail.get("specialSessionValue")

    for roll_call in detail.get("rollCalls") or []:
        if not isinstance(roll_call, dict):
            continue
        chamber = normalize_chamber(roll_call.get("chamber"))
        roll_call_key, vote_id = _roll_call_key(roll_call, chamber)
        chamber_index = roster_indexes.get(chamber, {})
        members: list[dict[str, Any]] = []

        detailed_votes = [
            item
            for item in (roll_call.get("rollCallLegVoteDtos") or [])
            if isinstance(item, dict) and str(item.get("legislator") or "").strip()
        ]
        if detailed_votes:
            for item in detailed_votes:
                label = str(item.get("legislator") or "").strip()
                member = _resolve_legislator(label, chamber, roster_indexes)
                member["vote_position"] = _normalize_vote_position(item.get("vote"))
                members.append(member)
        else:
            for source_field, position in VOTE_POSITIONS:
                for label in _split_vote_names(roll_call.get(source_field), chamber_index):
                    member = _resolve_legislator(label, chamber, roster_indexes)
                    member["vote_position"] = position
                    members.append(member)

        deduplicated_members: dict[str, dict[str, Any]] = {}
        for member in members:
            deduplicated_members[str(member["member_key"])] = member
        members = sorted(
            deduplicated_members.values(),
            key=lambda item: (
                VOTE_POSITION_ORDER.get(str(item.get("vote_position") or ""), len(VOTE_POSITION_ORDER)),
                str(item.get("legislator_name") or "").casefold(),
            ),
        )

        member_counts = defaultdict(int)
        for member in members:
            member_counts[str(member.get("vote_position") or "other")] += 1

        payloads.append(
            {
                "state": state,
                "year": year,
                "special_session_value": special_session_value,
                "bill_num": bill_num,
                "roll_call_key": roll_call_key,
                "vote_id": vote_id or None,
                "chamber": chamber,
                "vote_date": roll_call.get("voteDate"),
                "vote_type": roll_call.get("voteType"),
                "action": roll_call.get("action"),
                "amendment_number": roll_call.get("amendmentNumber"),
                "yes_count": _int_value(roll_call.get("yesVotesCount"), member_counts["yes"]),
                "no_count": _int_value(roll_call.get("noVotesCount"), member_counts["no"]),
                "absent_count": _int_value(roll_call.get("absentVotesCount"), member_counts["absent"]),
                "conflict_count": _int_value(roll_call.get("conflictVotesCount"), member_counts["conflict"]),
                "excused_count": _int_value(roll_call.get("excusedVotesCount"), member_counts["excused"]),
                "members": members,
                "source_synced_at": timestamp,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )

    return sorted(payloads, key=lambda item: str(item.get("vote_date") or ""), reverse=True)
