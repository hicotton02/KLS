"""Bounded, resumable official-source verification without changing bill content."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from app.alaska_api import AlaskaApiClient
from app.db import PostgresConnection, connect
from app.settings import get_settings
from app.sync_service import _compute_source_hash, _state_detail_comparisons
from app.text_utils import first_non_empty, html_to_text, iso_now
from scripts.review_held_summaries import _require_durable_backup


def source_fingerprint(detail: dict) -> str:
    return _compute_source_hash(detail,
        first_non_empty(html_to_text(detail.get('summaryHTML')), detail.get('catchTitle')),
        first_non_empty(html_to_text(detail.get('digestHTML')), detail.get('billTitle')), '')


def validate_catalog(items: list[dict], original: dict, stored: dict) -> None:
    expected = original.get('source_total')
    if (not expected or original.get('current_year') != 2026 or len(items) != expected
            or len({i['billNum'] for i in items}) != len(items)):
        raise RuntimeError('Official catalog total changed or is unknown; a full source refresh is required.')
    if not all(i['billNum'] in stored for i in items):
        raise RuntimeError('New official bills require a full source refresh.')


def finish_verification(original: dict, rows: list[dict], verified: set[str], total: int) -> None:
    with connect() as c:
        lock = ' FOR UPDATE' if isinstance(c, PostgresConnection) else ''
        current = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'" + lock).fetchone())
        # This guarded write cannot replace the result of a newer daily worker.
        after = [dict(r) for r in c.execute("""SELECT * FROM bills
            WHERE state='ak' AND year=2026 ORDER BY bill_num""" + lock).fetchall()]
        if current != original or after != rows or original['is_running']:
            raise RuntimeError('A newer Alaska worker changed the scope; keep its result.')
        if total <= 0 or len(verified) != total or not verified.issubset({r['bill_num'] for r in rows}):
            raise RuntimeError('Official coverage is not fully verified.')
        stamp = iso_now()
        changed = c.execute("""UPDATE sync_status SET failed=0,seen=?,skipped=?,updated=0,
            source_total=?,stored_total=?,finished_at=?,last_success_at=?,updated_at=?,last_message=?
            WHERE state='ak' AND updated_at=? AND is_running=0""",
            (total,total,total,len(rows),stamp,stamp,stamp,
             f'Verified all {total} official bill details. Existing text and summaries preserved.', original['updated_at']))
        if changed.rowcount != 1:
            raise RuntimeError('Alaska scan changed during completion; verification was not published.')
        c.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=3200)
    parser.add_argument('--max-checks', type=int, default=800)
    args = parser.parse_args()
    started = time.monotonic()
    with connect() as c:
        original = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone())
        rows = [dict(r) for r in c.execute("""SELECT * FROM bills
            WHERE state='ak' AND year=2026 ORDER BY bill_num""").fetchall()]
    assert not original['is_running'], 'An Alaska scan is already running.'
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    with args.backup.open('x', encoding='utf-8') as backup:
        _require_durable_backup(backup)
        backup.write(json.dumps({'sync_status': original, 'bills': rows}) + '\n')
        backup.flush()
        os.fsync(backup.fileno())
    print(json.dumps({'backup': str(args.backup), 'sha256': hashlib.sha256(args.backup.read_bytes()).hexdigest()}), flush=True)
    stored = {r['bill_num']: r for r in rows}
    api = AlaskaApiClient(get_settings())
    verified = set()
    try:
        items = api.fetch_year_bills(2026)
        validate_catalog(items, original, stored)
        for item in items:
            if len(verified) >= args.max_checks or time.monotonic() - started > args.max_seconds:
                raise RuntimeError('Bounded pass stopped. Saved checkpoints will resume; existing status retained.')
            detail = api.fetch_bill_detail(item['detailPath'])
            assert detail['bill'] == item['billNum'], 'Official bill identity mismatch.'
            assert source_fingerprint(detail) == stored[item['billNum']]['source_hash'], 'Source changed; full guarded refresh required.'
            assert all(str(stored[item['billNum']].get(k) or '') == str(v or '')
                       for k, v in _state_detail_comparisons(detail).items()), 'Bill actions changed; full source refresh required.'
            verified.add(item['billNum'])
            if len(verified) % 25 == 0:
                print(json.dumps({'verified': len(verified), 'total': len(items), 'seconds': round(time.monotonic()-started)}), flush=True)
        finish_verification(original, rows, verified, len(items))
        print(json.dumps({'verified': len(verified), 'bill_rows_changed': 0, 'failed': 0}), flush=True)
    finally:
        api.close()
        print(json.dumps({'verified_this_pass': len(verified), 'bill_rows_changed': 0}), flush=True)


if __name__ == '__main__':
    main()
