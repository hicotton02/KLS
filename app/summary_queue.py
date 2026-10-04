"""Durable, bounded generation and validation of missing bill summaries."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
import uuid

import httpx

from app.content_quality import assess_bill_content
from app.db import PostgresConnection, _parse_row, connect, init_db, list_bill_amendments
from app.ollama import OllamaClient
from app.settings import get_settings
from app.sync_service import _amendment_search_snippets, _build_search_blob, _mark_validated_interpretation
from app.tagging import extract_bill_tags

MAX_ATTEMPTS = 3
LEASE_SECONDS = 1800
SOURCE_FIELDS = (
    'source_hash', 'catch_title', 'bill_title', 'sponsor', 'status_label',
    'status_explainer', 'outcome', 'last_action', 'last_action_date', 'effective_date',
    'introduced_path', 'current_version_path', 'summary_path', 'digest_path',
)
TEXT_FIELDS = ('official_summary_text', 'official_digest_text', 'current_bill_text')


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def later(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='seconds')


def source_version(bill: dict, *, include_text: bool = False) -> str:
    fields = SOURCE_FIELDS + (TEXT_FIELDS if include_text else ())
    data = {key: bill.get(key) for key in fields}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def refresh_queue(state: str) -> dict:
    columns = ','.join(('id', 'state', 'year', 'bill_num', 'interpretation_json') + SOURCE_FIELDS)
    lengths = ','.join(f'length(coalesce({key},\'\')) AS {key}_length' for key in TEXT_FIELDS)
    with connect() as c:
        rows = c.execute(f'SELECT {columns},{lengths} FROM bills WHERE state=?', (state,)).fetchall()
    timestamp = now()
    entries = []
    for raw in rows:
        bill = _parse_row(raw)
        if assess_bill_content(bill)['summary_ready']:
            status = 'complete'
        elif max(bill[f'{key}_length'] for key in TEXT_FIELDS) < 300 or not assess_bill_content(bill)['has_source']:
            status = 'source_hold'
        else:
            status = 'pending'
        entries.append((bill['id'], state, source_version(bill), status, timestamp, timestamp))
    with connect() as c:
        c.executemany("""INSERT INTO bill_summary_work
            (bill_id,state,source_version,status,created_at,updated_at) VALUES (?,?,?,?,?,?)
            ON CONFLICT(bill_id) DO UPDATE SET
            source_version=excluded.source_version,
            status=excluded.status, attempts=0, owner=NULL,lease_expires_at=NULL,retry_at=NULL,last_error=NULL,
            updated_at=excluded.updated_at
            WHERE bill_summary_work.source_version<>excluded.source_version
              OR (excluded.status='complete' AND bill_summary_work.status<>'complete')
              OR (excluded.status='pending' AND bill_summary_work.status IN ('complete','source_hold'))""", entries)
        c.commit()
    return queue_status(state)


def queue_status(state: str) -> dict:
    with connect() as c:
        rows = c.execute('SELECT status,count(*) AS n FROM bill_summary_work WHERE state=? GROUP BY status', (state,)).fetchall()
    return {r['status']: r['n'] for r in rows}


def claim(state: str) -> dict | None:
    owner, timestamp = uuid.uuid4().hex, now()
    with connect() as c:
        if not isinstance(c, PostgresConnection):
            c.execute('BEGIN IMMEDIATE')
        c.execute("""UPDATE bill_summary_work SET status='review_hold',owner=NULL,lease_expires_at=NULL,
            last_error='Worker lease expired after the maximum attempts',updated_at=?
            WHERE state=? AND status='processing' AND lease_expires_at<=? AND attempts>=?""",
            (timestamp, state, timestamp, MAX_ATTEMPTS))
        lock = ' FOR UPDATE OF w SKIP LOCKED' if isinstance(c, PostgresConnection) else ''
        row = c.execute("""SELECT w.* FROM bill_summary_work w JOIN bills b ON b.id=w.bill_id
            WHERE w.state=? AND w.attempts<? AND
              ((w.status IN ('pending','retry','quality_hold') AND (w.retry_at IS NULL OR w.retry_at<=?))
                OR (w.status='processing' AND w.lease_expires_at<=?))
            ORDER BY w.attempts,b.year DESC,b.bill_num LIMIT 1""" + lock,
            (state, MAX_ATTEMPTS, timestamp, timestamp)).fetchone()
        if row is None:
            c.commit()
            return None
        c.execute("""UPDATE bill_summary_work SET status='processing',attempts=attempts+1,
            owner=?,lease_expires_at=?,updated_at=? WHERE bill_id=?""",
            (owner, later(LEASE_SECONDS), timestamp, row['bill_id']))
        c.commit()
    return {**dict(row), 'owner': owner, 'attempts': row['attempts'] + 1}


def finish(work: dict, status: str, error: str | None = None, *, cooldown: int = 0) -> None:
    with connect() as c:
        c.execute("""UPDATE bill_summary_work SET status=?,last_error=?,retry_at=?,owner=NULL,
            lease_expires_at=NULL,updated_at=? WHERE bill_id=? AND owner=? AND source_version=?
            AND status='processing' AND lease_expires_at>?""",
            (status, error, later(cooldown) if cooldown else None, now(), work['bill_id'], work['owner'], work['source_version'], now()))
        c.commit()


def generate(bill: dict) -> dict:
    settings = get_settings()
    client = OllamaClient(settings)
    args = {
        'bill': {'bill':bill['bill_num'],'catchTitle':bill.get('catch_title'),'billTitle':bill.get('bill_title'),
                 'sponsor':bill.get('sponsor'),'lastAction':bill.get('last_action'),
                 'lastActionDate':bill.get('last_action_date'),'effectiveDate':bill.get('effective_date')},
        'status_info': {'label':bill.get('status_label'),'explanation':bill.get('status_explainer'),'outcome':bill.get('outcome')},
        **{key: str(bill.get(key) or '') for key in TEXT_FIELDS},
    }
    try:
        draft = client.generate_interpretation(**args)
        result = client.fact_check_interpretation(**args, candidate_interpretation=draft)
        return _mark_validated_interpretation(result, settings.ollama_model)
    finally:
        client.close()


def save_result(work: dict, original: dict, interpretation: dict) -> bool:
    amendments = list_bill_amendments(original['state'], original['year'], original['bill_num'],
        special_session_value=original.get('special_session_value'))
    tags = extract_bill_tags(catch_title=original.get('catch_title'),sponsor=original.get('sponsor'),
        official_summary_text=original.get('official_summary_text'),official_digest_text=original.get('official_digest_text'),
        interpretation=interpretation,amendment_snippets=_amendment_search_snippets(amendments))
    with connect() as c:
        if not isinstance(c, PostgresConnection):
            c.execute('BEGIN IMMEDIATE')
        lock = ' FOR UPDATE' if isinstance(c, PostgresConnection) else ''
        current_work = c.execute('SELECT * FROM bill_summary_work WHERE bill_id=?' + lock, (work['bill_id'],)).fetchone()
        if (current_work is None or current_work['owner'] != work['owner']
                or current_work['status'] != 'processing' or current_work['source_version'] != work['source_version']
                or current_work['lease_expires_at'] <= now()):
            return False
        raw = c.execute('SELECT * FROM bills WHERE id=?' + lock, (work['bill_id'],)).fetchone()
        current = _parse_row(raw)
        if current is None:
            return False
        if source_version(current, include_text=True) != source_version(original, include_text=True):
            c.execute("""UPDATE bill_summary_work SET status='pending',source_version=?,attempts=0,
                owner=NULL,lease_expires_at=NULL,retry_at=NULL,last_error='Source changed during generation',
                updated_at=? WHERE bill_id=?""", (source_version(current), now(), work['bill_id']))
            return False
        if assess_bill_content(current)['summary_ready']:
            c.execute("UPDATE bill_summary_work SET status='complete',owner=NULL,lease_expires_at=NULL,updated_at=? WHERE bill_id=?", (now(),work['bill_id']))
            return False
        if current['interpretation_json'] != original['interpretation_json']:
            c.execute("""UPDATE bill_summary_work SET status='pending',owner=NULL,lease_expires_at=NULL,
                updated_at=?,last_error='Summary changed during generation' WHERE bill_id=?""", (now(),work['bill_id']))
            return False
        candidate = {**current,'interpretation_json':interpretation}
        if not assess_bill_content(candidate)['summary_ready']:
            raise ValueError('Generated summary did not pass the content quality check')
        c.execute("""INSERT INTO bill_summary_history
            (bill_id,source_version,interpretation_json,bill_tags_json,search_blob,saved_at)
            VALUES (?,?,?,?,?,?) ON CONFLICT(bill_id,source_version) DO NOTHING""",
            (work['bill_id'],source_version(original,include_text=True),raw['interpretation_json'],raw['bill_tags_json'],raw['search_blob'],now()))
        c.execute("""UPDATE bills SET interpretation_json=?,bill_tags_json=?,search_blob=?,updated_at=? WHERE id=?""",
            (json.dumps(interpretation),json.dumps(tags),_build_search_blob(candidate,interpretation,tags,amendments),now(),work['bill_id']))
        c.execute("""UPDATE bill_summary_work SET status='complete',owner=NULL,lease_expires_at=NULL,
            retry_at=NULL,last_error=NULL,updated_at=? WHERE bill_id=?""", (now(),work['bill_id']))
        c.commit()
    return True


def process_one(work: dict) -> str:
    with connect() as c:
        bill = _parse_row(c.execute('SELECT * FROM bills WHERE id=?', (work['bill_id'],)).fetchone())
    if bill is None:
        return 'gone'
    if assess_bill_content(bill)['summary_ready']:
        finish(work, 'complete')
        return 'preserved'
    if max(len(str(bill.get(key) or '')) for key in TEXT_FIELDS) < 300 or not assess_bill_content(bill)['has_source']:
        finish(work, 'source_hold', 'Usable official source text or link is missing')
        return 'source_hold'
    if source_version(bill) != work['source_version']:
        finish(work, 'pending', 'Source changed before generation')
        refresh_queue(work['state'])
        return 'changed'
    try:
        interpretation = generate(bill)
        if not assess_bill_content({**bill,'interpretation_json':interpretation})['summary_ready']:
            raise ValueError('Generated summary did not pass the content quality check')
        return 'saved' if save_result(work, bill, interpretation) else 'changed'
    except ValueError:
        finish(work, 'quality_hold' if work['attempts'] < MAX_ATTEMPTS else 'review_hold',
            'Generated summary did not pass validation', cooldown=21600)
        return 'quality_hold'
    except Exception as exc:
        reason = f'{type(exc).__name__}: summary generation failed'
        if isinstance(exc, httpx.HTTPStatusError):
            reason = f'Summary service returned HTTP {exc.response.status_code}'
        finish(work, 'retry' if work['attempts'] < MAX_ATTEMPTS else 'review_hold',reason,cooldown=1800)
        return 'retry'


def run(state: str, limit: int = 32, *, deadline_seconds: int = 1500, refresh: bool = True) -> dict:
    init_db()
    if refresh:
        print(json.dumps({'state':state,'queue':refresh_queue(state)}),flush=True)
    started = time.monotonic()
    counts: dict[str, int] = {}
    for _ in range(max(0, limit)):
        if time.monotonic() - started >= deadline_seconds:
            break
        work = claim(state)
        if work is None:
            break
        result = process_one(work)
        counts[result] = counts.get(result,0) + 1
        print(json.dumps({'state':state,'bill_id':work['bill_id'],'result':result,'seconds':round(time.monotonic()-started,1)}),flush=True)
    result = {'state':state,'results':counts,'queue':queue_status(state)}
    print(json.dumps(result),flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',choices=['wy','us'],required=True)
    parser.add_argument('--limit',type=int,default=32)
    parser.add_argument('--deadline-seconds',type=int,default=1500)
    parser.add_argument('--no-refresh',action='store_true')
    parser.add_argument('--status',action='store_true')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(queue_status(args.state)),flush=True)
    else:
        run(args.state,args.limit,deadline_seconds=args.deadline_seconds,refresh=not args.no_refresh)
