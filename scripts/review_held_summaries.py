"""Explicit, backed-up, one-time review of selected held summaries after a repair."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re

from app.content_quality import assess_bill_content
from app.db import PostgresConnection, _parse_row, connect
from app import summary_queue as queue

RECOVERABLE = {'Generated summary did not pass validation', 'ReadTimeout: summary generation failed',
    'Summary response exceeded its output limit', 'Summary response was not complete JSON'}


def _is_nfs_path(path: str, mountinfo: str) -> bool:
    target = PurePosixPath(path)
    matches = []
    for line in mountinfo.splitlines():
        left, separator, right = line.partition(' - ')
        fields = left.split()
        if not separator or len(fields) < 5 or not right.split():
            continue
        mount = PurePosixPath(re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),fields[4]))
        if target == mount or mount in target.parents:
            matches.append((len(mount.parts), right.split()[0]))
    return bool(matches and max(matches)[1] in ('nfs','nfs4'))


def _require_durable_backup(backup) -> None:
    if os.environ.get('KUBERNETES_SERVICE_HOST'):
        error = 'KLS pod review backups require the persistent NFS maintenance volume, not scratch storage'
        if not isinstance(backup.name,(str,os.PathLike)):
            raise ValueError(error)
        try:
            mounts=Path('/proc/self/mountinfo').read_text()
        except OSError:
            raise ValueError(error) from None
        if not _is_nfs_path(str(Path(backup.name).resolve()),mounts):
            raise ValueError(error)


def review_one(bill_id: int, backup) -> str:
    _require_durable_backup(backup)
    with connect() as c:
        if not isinstance(c, PostgresConnection):
            c.execute('BEGIN IMMEDIATE')
        lock = ' FOR UPDATE SKIP LOCKED' if isinstance(c, PostgresConnection) else ''
        work = c.execute('SELECT * FROM bill_summary_work WHERE bill_id=?' + lock, (bill_id,)).fetchone()
        if (work is None or work['status'] != 'review_hold' or work['attempts'] != queue.MAX_ATTEMPTS
                or work['last_error'] not in RECOVERABLE or work['owner'] or work['lease_expires_at']
                or (work['retry_at'] and work['retry_at'] > queue.now())):
            return 'not_eligible'
        raw = c.execute('SELECT * FROM bills WHERE id=?' + lock, (bill_id,)).fetchone()
        bill = _parse_row(raw) if raw is not None else None
        if bill is None or bill['state'] not in ('us','wy') or queue.source_version(bill) != work['source_version']:
            return 'source_changed'
        assessment = assess_bill_content(bill)
        if assessment['summary_ready']:
            return 'preserved'
        if not assessment['has_source'] or max(len(str(bill.get(k) or '')) for k in queue.TEXT_FIELDS) < 300:
            return 'source_hold'
        backup.write(json.dumps({'bill':dict(raw),'work':dict(work),'reviewed_at':queue.now()}) + '\n')
        backup.flush()
        os.fsync(backup.fileno())
        changed = c.execute("""UPDATE bill_summary_work SET status='waiting',retry_at=?,updated_at=?,
            last_error='Approved one-time review after summary output-limit repair'
            WHERE bill_id=? AND status='review_hold' AND attempts=? AND source_version=?
            AND updated_at=? AND owner IS NULL AND lease_expires_at IS NULL""",
            (queue.now(),queue.now(),bill_id,work['attempts'],work['source_version'],work['updated_at']))
        c.commit()
        return 'approved' if changed.rowcount == 1 else 'changed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bill-id',type=int,action='append',required=True)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup',type=Path)
    args = parser.parse_args()
    ids = list(dict.fromkeys(args.bill_id))
    if len(ids)>128:
        parser.error('A review batch is limited to 128 explicitly selected bills')
    if args.apply and not args.backup:
        parser.error('--apply requires an unused durable --backup file')
    if not args.apply:
        print(json.dumps({'apply':False,'bill_ids':ids,'attempts_reset':False}))
        return
    with args.backup.open('x',encoding='utf-8') as backup:
        for bill_id in ids:
            print(json.dumps({'bill_id':bill_id,'result':review_one(bill_id,backup),'attempts_reset':False}),flush=True)


if __name__ == '__main__':
    main()
