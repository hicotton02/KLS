"""Complete federal inventory and bounded, resumable source refreshes."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

from app.db import connect, get_bill, init_db, update_sync_status, upsert_bill
from app.federal_api import CongressApiClient, congress_bill_identifier
from app.settings import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def sync_lease():
    owner = uuid.uuid4().hex
    def renew():
        now = utc_now()
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        with connect() as c:
            changed = c.execute("""UPDATE federal_sync_lease SET expires_at=?
                WHERE name='federal' AND owner=? AND expires_at>=?""", (expiry, owner, now)).rowcount
            c.commit()
        if changed != 1:
            raise RuntimeError("Federal sync lease was lost; stopping this worker")

    with connect() as c:
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
        acquired = c.execute("""INSERT INTO federal_sync_lease(name,owner,expires_at) VALUES ('federal',?,?)
            ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,expires_at=excluded.expires_at
            WHERE federal_sync_lease.expires_at < ?""", (owner, expiry, utc_now())).rowcount == 1
        c.commit()
    try:
        yield renew if acquired else None
    finally:
        if acquired:
            with connect() as c:
                c.execute("DELETE FROM federal_sync_lease WHERE name='federal' AND owner=?", (owner,))
                c.commit()


def save_catalog(congress: int, items: list[dict]) -> None:
    # Distinct inventory generations must not include records from an earlier scan.
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    rows = []
    for item in items:
        payload = json.dumps(item, sort_keys=True)
        rows.append((congress, congress_bill_identifier(item['type'], item['number']), payload,
                     hashlib.sha256(payload.encode()).hexdigest(), now))
    with connect() as c:
        c.executemany("""INSERT INTO federal_bill_work(congress,bill_num,item_json,source_version,last_seen_at)
            VALUES (?,?,?,?,?) ON CONFLICT(congress,bill_num) DO UPDATE SET
            item_json=excluded.item_json,source_version=excluded.source_version,last_seen_at=excluded.last_seen_at,
            retry_at=CASE WHEN federal_bill_work.source_version<>excluded.source_version THEN NULL ELSE federal_bill_work.retry_at END,
            last_error=CASE WHEN federal_bill_work.source_version<>excluded.source_version THEN NULL ELSE federal_bill_work.last_error END""", rows)
        c.execute("""INSERT INTO federal_catalog(congress,source_total,scanned_at) VALUES (?,?,?)
            ON CONFLICT(congress) DO UPDATE SET source_total=excluded.source_total,scanned_at=excluded.scanned_at""",
            (congress, len(items), now))
        c.commit()


PENDING = """(w.processed_version IS NULL OR w.processed_version<>w.source_version OR NOT EXISTS
    (SELECT 1 FROM bills b WHERE b.state='us' AND b.year=w.congress AND b.bill_num=w.bill_num AND b.special_session_key=-1))"""


def coverage(congresses: list[int]) -> dict:
    placeholders = ','.join('?' for _ in congresses)
    with connect() as c:
        catalog = c.execute(f"SELECT count(*) AS n,SUM(source_total) AS total FROM federal_catalog WHERE congress IN ({placeholders})", congresses).fetchone()
        rows = c.execute(f"""SELECT COUNT(*) AS inventory,
            SUM(CASE WHEN {PENDING} THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN w.last_error IS NOT NULL AND {PENDING} THEN 1 ELSE 0 END) AS failed,
            SUM(CASE WHEN EXISTS (SELECT 1 FROM bills b WHERE b.state='us' AND b.year=w.congress
                AND b.bill_num=w.bill_num AND b.special_session_key=-1) THEN 1 ELSE 0 END) AS stored
            FROM federal_bill_work w JOIN federal_catalog f ON f.congress=w.congress AND f.scanned_at=w.last_seen_at
            WHERE w.congress IN ({placeholders})""", congresses).fetchone()
    return {'source_total': int(catalog['total'] or 0) if catalog['n']==len(congresses) else None,
            **{k:int(rows[k] or 0) for k in ('inventory','pending','failed','stored')}}


def pending_work(congresses: list[int], limit: int) -> list[dict]:
    placeholders = ','.join('?' for _ in congresses)
    with connect() as c:
        return [dict(r) for r in c.execute(f"""SELECT w.* FROM federal_bill_work w
            JOIN federal_catalog f ON f.congress=w.congress AND f.scanned_at=w.last_seen_at
            WHERE w.congress IN ({placeholders}) AND {PENDING} AND (w.retry_at IS NULL OR w.retry_at<=?)
            ORDER BY w.attempts,w.congress DESC,w.bill_num LIMIT ?""", [*congresses, utc_now(), limit]).fetchall()]


def mark_work(row: dict, error: str | None = None) -> None:
    retry_at = (datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(timespec="seconds") if error else None
    with connect() as c:
        c.execute("""UPDATE federal_bill_work SET attempts=attempts+1,last_error=?,retry_at=?,
            processed_version=CASE WHEN ?=0 THEN source_version ELSE processed_version END,
            processed_at=CASE WHEN ?=0 THEN ? ELSE processed_at END
            WHERE congress=? AND bill_num=? AND source_version=?""",
            (error, retry_at, int(error is not None), int(error is not None), utc_now(), row['congress'], row['bill_num'], row['source_version']))
        c.commit()


def refresh_bill(api: CongressApiClient, row: dict, *, skip_interpretation: bool):
    from app.sync_service import _prepare_federal_bill, _complete_federal_bill
    settings = get_settings()
    item = json.loads(row['item_json'])
    congress, kind, number = row['congress'], str(item['type']).upper(), str(item['number'])
    detail = api.fetch_bill_detail(congress, kind, number)
    if (str(detail.get('number')) != number or str(detail.get('type')).upper() != kind
            or detail.get('congress') != congress):
        raise ValueError('Congress detail identity did not match the inventory')
    summaries = api.fetch_bill_summaries(congress, kind, number)
    versions = api.fetch_bill_text_versions(congress, kind, number)
    actions = api.fetch_bill_actions(congress, kind, number)
    _, _, prompt, status, source_hash, payload, index = _prepare_federal_bill(
        api=api, congress=congress, bill_num=row['bill_num'], bill_type=kind, item=item,
        detail=detail, summaries=summaries, text_versions=versions, actions=actions)
    completed = _complete_federal_bill(settings=settings, index_key=(congress,-1,row['bill_num']),
        congress=congress, base_payload=payload, index_payload=index, prompt_bill=prompt, status_info=status,
        official_summary_text=payload['official_summary_text'], official_digest_text=payload['official_digest_text'],
        current_bill_text=payload['current_bill_text'],
        skip_interpretation=skip_interpretation, existing_bill=get_bill('us',congress,row['bill_num']))
    return completed


def sync_federal_inventory(congresses=None, limit=None, skip_interpretation=False, logger=None):
    from app.sync_service import SyncStats
    settings = get_settings()
    congresses = list(congresses or settings.federal_congresses)
    batch_size = max(0, int(settings.federal_sync_limit if limit is None else limit))
    stats = SyncStats(years=congresses)
    log = logger or (lambda message: None)
    init_db()
    with sync_lease() as renew:
        if renew is None:
            log('Federal source worker is already active; leaving its progress unchanged.')
            return stats
        api = CongressApiClient(settings)
        started = utc_now()
        error = None
        update_sync_status('us', is_running=True, started_at=started, finished_at=None,
                           years_json=congresses, last_message='Checking complete federal inventory.')
        try:
            for congress in congresses:
                with connect() as c:
                    catalog = c.execute('SELECT * FROM federal_catalog WHERE congress=?',(congress,)).fetchone()
                if catalog is None or datetime.fromisoformat(catalog['scanned_at']) < datetime.now(timezone.utc)-timedelta(hours=6):
                    items = api.fetch_bill_catalog(congress, heartbeat=renew)
                    renew()
                    save_catalog(congress, items)
                    log(f'Federal inventory {congress}: {len(items)} official records verified.')
            for row in pending_work(congresses, batch_size):
                renew()
                stats.seen += 1
                try:
                    completed = refresh_bill(api, row, skip_interpretation=skip_interpretation)
                    renew()
                    upsert_bill(completed.payload)
                    mark_work(row)
                    stats.updated += 1
                    stats.interpreted += completed.interpreted
                    stats.validated += completed.validated
                except Exception as exc:
                    safe_error = str(exc).replace(settings.congress_api_key, '[redacted]') if settings.congress_api_key else str(exc)
                    renew()
                    mark_work(row, safe_error[:500])
                    stats.failed += 1
                    log(f"Federal {row['bill_num']}: {safe_error[:200]}")
                if stats.seen % 10 == 0:
                    totals = coverage(congresses)
                    update_sync_status('us', seen=stats.seen, updated=stats.updated, failed=totals['failed'],
                        source_total=totals['source_total'], stored_total=totals['stored'],
                        current_bill_num=row['bill_num'], last_message=f"Federal catch-up: {totals['pending']} records still need source refresh.")
        except Exception as exc:
            error = str(exc).replace(settings.congress_api_key, '[redacted]') if settings.congress_api_key else str(exc)
            stats.failed += 1
            log(f'Federal inventory stopped: {error[:200]}')
        finally:
            api.close()
            renew()
            totals = coverage(congresses)
            complete = error is None and totals['source_total'] is not None and totals['pending']==0 and totals['inventory']==totals['source_total']
            payload = dict(is_running=False, seen=stats.seen, updated=stats.updated, skipped=stats.skipped,
                failed=max(stats.failed,totals['failed']), interpreted=stats.interpreted, validated=stats.validated,
                source_total=totals['source_total'], stored_total=totals['stored'], finished_at=utc_now(),
                current_bill_num='', last_message=error[:300] if error else
                f"Federal source coverage: {totals['stored']} of {totals['source_total']} stored; {totals['pending']} awaiting refresh; {totals['failed']} held for retry.")
            if complete:
                payload['last_success_at'] = utc_now()
            update_sync_status('us', **payload)
            log(payload['last_message'])
    return stats


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit',type=int,default=100)
    args = parser.parse_args()
    result = sync_federal_inventory(limit=args.limit,skip_interpretation=True,logger=lambda m: print(m,flush=True))
    raise SystemExit(1 if result.failed else 0)
