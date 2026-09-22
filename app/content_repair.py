"""Bounded, backed-up repair of the records identified in the September audit."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from app.content_corrections import apply_content_corrections
from app.db import connect, iso_now
from app.minnesota_api import MinnesotaApiClient
from app.settings import get_settings
from app.status import classify_bill_status


DETAIL_FIELDS = {
    "bill_status": "billStatus", "last_action": "lastAction",
    "last_action_date": "lastActionDate", "signed_date": "signedDate",
    "chapter_no": "chapter", "enrolled_no": "enrolledNumber",
    "bill_actions_json": "billActions",
}
REPAIR_FIELDS = {*DETAIL_FIELDS, "status_label", "status_explainer", "outcome",
                 "interpretation_json", "updated_at"}


def plan_minnesota_repair(row: dict, detail: dict) -> dict:
    if row["state"] != "mn" or detail.get("bill") != row["bill_num"]:
        raise ValueError("Source bill does not match the stored bill")
    if int(detail.get("specialSessionValue") or 0) != int(row.get("special_session_value") or 0):
        raise ValueError("Source session does not match the stored session")
    changes = {}
    for column, field in DETAIL_FIELDS.items():
        value = detail.get(field)
        if column.endswith("_json"):
            value = json.dumps(value or [])
            same = json.loads(row.get(column) or "[]") == json.loads(value)
        else:
            same = (row.get(column) or "") == (value or "")
        if not same:
            changes[column] = value
    status = classify_bill_status(detail.get("billStatus"), detail.get("lastAction"),
                                  detail.get("signedDate"), detail.get("chapter"), detail.get("enrolledNumber"))
    for column, field in (("status_label", "label"), ("status_explainer", "explanation"), ("outcome", "outcome")):
        if row.get(column) != status[field]:
            changes[column] = status[field]
    if changes:
        explanation = json.loads(row.get("interpretation_json") or "null")
        if isinstance(explanation, dict) and explanation:
            explanation.update(fact_check_status="stale", fact_check_result="source-metadata-corrected")
            changes["interpretation_json"] = json.dumps(explanation)
    return changes


def plan_budget_repair(row: dict) -> dict:
    parsed = {**row, "interpretation_json": json.loads(row.get("interpretation_json") or "null")}
    corrected = apply_content_corrections(parsed)
    if parsed == corrected:
        return {}
    return {"interpretation_json": json.dumps(corrected["interpretation_json"])}


def apply_repair(row: dict, changes: dict, backup) -> bool:
    if not changes or not changes.keys() <= REPAIR_FIELDS:
        raise ValueError("Repair must contain only allowed, changed fields")
    backup.write(json.dumps({"before": row, "changes": changes}) + "\n")
    backup.flush()
    os.fsync(backup.fileno())
    values = {**changes, "updated_at": iso_now()}
    columns = list(values)
    # An intervening sync or interpretation wins; never overwrite its newer data.
    with connect() as connection:
        result = connection.execute(
            "UPDATE bills SET " + ", ".join(f"{column} = ?" for column in columns)
            + " WHERE id = ? AND COALESCE(updated_at, '') = ?"
            + " AND COALESCE(source_hash, '') = ? AND COALESCE(interpretation_json, '') = ?",
            [values[column] for column in columns] + [row["id"], row.get("updated_at") or "",
                row.get("source_hash") or "", row.get("interpretation_json") or ""],
        )
        connection.commit()
        return result.rowcount == 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 5))
    args = parser.parse_args()
    if args.apply and not args.backup:
        parser.error("--apply requires an unused --backup path")
    with connect() as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM bills WHERE state = 'mn' OR (state = 'wy' AND year = 2026 AND bill_num = 'SF0001')"
            " ORDER BY state DESC, year DESC, bill_num"
        ).fetchall()]
    if args.limit:
        rows = rows[:args.limit]
    client = MinnesotaApiClient(get_settings())

    def plan(row):
        try:
            if row["state"] == "mn":
                changes = plan_minnesota_repair(row, client.fetch_bill_detail(row["summary_path"]))
            else:
                changes = plan_budget_repair(row)
            return row, changes, None
        except Exception as error:
            return row, {}, type(error).__name__

    counts = dict(checked=0, changed=0, applied=0, concurrent=0, failed=0)
    backup = args.backup.open("x", encoding="utf-8") if args.apply else None
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for row, changes, error in executor.map(plan, rows):
                counts["checked"] += 1
                if error:
                    counts["failed"] += 1
                    print(json.dumps({"bill": row["bill_num"], "year": row["year"], "error": error}), flush=True)
                elif changes:
                    counts["changed"] += 1
                    if backup:
                        counts["applied" if apply_repair(row, changes, backup) else "concurrent"] += 1
                if counts["checked"] % 100 == 0:
                    print(json.dumps(counts), flush=True)
    finally:
        client.close()
        if backup:
            backup.close()
    print(json.dumps(counts), flush=True)
    if counts["failed"] or counts["concurrent"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
