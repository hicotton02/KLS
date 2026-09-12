"""Repair legacy Wyoming media entries with a dry run and an explicit backup."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.db import PostgresConnection, connect, init_db
from app.text_utils import iso_now
from app.wyoming_media_sources import normalize_wyoming_media_url


def plan_media_repair(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("duplicate_of_id"):
            continue
        if row["transcript_status"] == "transcribing" or row["explanation_scan_status"] == "scanning":
            raise ValueError("Pause Wyoming workers and wait for active recordings before repairing media.")
        canonical = normalize_wyoming_media_url(row["source_url"])
        groups[(int(row["year"]), int(row["special_session_key"]), canonical)].append(row)

    changes = []
    for (_, _, canonical), members in groups.items():
        if len({(r["session_date"], r["chamber"]) for r in members}) != 1:
            raise ValueError(f"Recording identity needs review before merging: {[r['id'] for r in members]}")
        members.sort(key=lambda r: (r["transcript_status"] != "available", r["source_url"] != canonical, r["id"]))
        keeper = members[0]
        # Keep a working legacy URL when its only difference is HTTP versus HTTPS.
        keeper_url = keeper["source_url"] if keeper["transcript_status"] == "available" else canonical
        if any(r["source_url"] == keeper_url for r in members[1:]):
            raise ValueError(f"Canonical URL conflict needs review for recording {keeper['id']}")
        for row in members:
            status = row["transcript_status"]
            error = str(row.get("transcript_error") or "").casefold()
            duplicate_of = None if row is keeper else keeper["id"]
            source_url = keeper_url if row is keeper else row["source_url"]
            if duplicate_of:
                status = "duplicate"
            elif status == "source_unavailable":
                if any(marker in error for marker in (
                    "invalid data found", "chunk rejected", "no timestamped speech",
                )):
                    # One recovery attempt; subsequent failures keep their specific held status.
                    status = "pending"
                elif "sign in to confirm your age" in error:
                    status = "source_restricted"
                elif "400 bad request" in error or "source url is invalid" in error:
                    status = "source_invalid"
            if source_url != row["source_url"] or status != row["transcript_status"] or duplicate_of:
                changes.append({
                    "id": row["id"], "source_url": source_url, "status": status,
                    "duplicate_of_id": duplicate_of,
                    "previous_url": row["source_url"], "previous_status": row["transcript_status"],
                })
    return changes


def repair_media(*, apply: bool = False, backup_path: Path | None = None) -> dict[str, Any]:
    with connect() as connection:
        lock = " FOR UPDATE" if apply and isinstance(connection, PostgresConnection) else ""
        rows = [dict(row) for row in connection.execute(
            """SELECT id, year, special_session_key, session_date, chamber, source_url,
            transcript_status, transcript_error, explanation_scan_status, duplicate_of_id
            FROM legislative_media WHERE state = 'wy'""" + lock,
        ).fetchall()]
        changes = plan_media_repair(rows)
        report = {"applied": apply, "counts": dict(Counter(c["status"] for c in changes)), "changes": changes}
        if not apply or not changes:
            return report
        if backup_path is None:
            raise ValueError("An unused backup path is required before applying a media repair.")
        ids = [c["id"] for c in changes]
        originals = [dict(row) for row in connection.execute(
            f"SELECT * FROM legislative_media WHERE id IN ({', '.join('?' for _ in ids)})", ids,
        ).fetchall()]
        with backup_path.open("x", encoding="utf-8") as handle:
            json.dump({"saved_at": iso_now(), "rows": originals, "plan": report}, handle)
        for change in changes:
            connection.execute(
                """UPDATE legislative_media SET source_url = ?, transcript_status = ?,
                duplicate_of_id = ?, updated_at = ? WHERE id = ?""",
                (change["source_url"], change["status"], change["duplicate_of_id"], iso_now(), change["id"]),
            )
        connection.commit()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    init_db()
    print(json.dumps(repair_media(apply=args.apply, backup_path=args.backup), indent=2))


if __name__ == "__main__":
    main()
