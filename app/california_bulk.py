"""California's public bulk bill metadata; no browser challenge or login needed."""
from __future__ import annotations

import csv
from datetime import datetime
import io
import re

from bs4 import BeautifulSoup
from remotezip import RemoteZip

BASE = 'https://downloads.leginfo.legislature.ca.gov'
BILL_FIELDS = ('bill_id','session_year','session_num','measure_type','measure_num','measure_state',
    'chapter_year','chapter_type','chapter_session_num','chapter_num','latest_bill_version_id',
    'active_flg','trans_uid','trans_update','current_location','current_secondary_loc','current_house',
    'current_status','days_31st_in_print')
VERSION_FIELDS = ('version_id','bill_id','version_num','action_date','action','request_num','subject',
    'vote_required','appropriation','fiscal_committee','local_program','substantive_changes','urgency',
    'taxlevy','lob','active_flg','trans_uid','trans_update')
AUTHOR_FIELDS = ('version_id','type','house','name','contribution','committee_members','active_flg',
    'trans_uid','trans_update','primary_author_flg')
HISTORY_FIELDS = ('bill_id','history_id','action_date','action','trans_uid','trans_update','sequence',
    'action_code','action_status','primary_location','secondary_location','ternary_location','end_status')


def table_rows(archive, name: str, fields: tuple[str, ...]) -> list[dict]:
    if archive.getinfo(name).file_size > 64 * 1024 * 1024:
        raise ValueError('California metadata member exceeds the safe size limit')
    with archive.open(name) as member:
        reader = csv.reader(io.TextIOWrapper(member, encoding='utf-8'), delimiter='\t', quotechar='`')
        rows = []
        for values in reader:
            if len(values) != len(fields):
                raise ValueError(f'California {name} column count changed')
            rows.append(dict(zip(fields, ('' if v == 'NULL' else v for v in values))))
        return rows


def archive_url(client, year: int) -> str:
    start = year if year % 2 else year - 1
    current = datetime.now().year
    if start != (current if current % 2 else current - 1):
        return f'{BASE}/pubinfo_{start}.zip'
    response = client.get(BASE + '/')
    response.raise_for_status()
    candidates = []
    for row in BeautifulSoup(response.text, 'html.parser').select('tr'):
        anchor = row.find('a', href=re.compile(r'^pubinfo_daily_[A-Za-z]{3}\.zip$'))
        cells = row.find_all('td')
        if anchor is not None and len(cells) >= 3:
            modified = datetime.strptime(cells[2].get_text(' ', strip=True), '%Y-%m-%d %H:%M')
            candidates.append((modified, str(anchor['href'])))
    if not candidates:
        raise ValueError('California has no published full daily snapshot')
    modified, name = max(candidates)
    if (datetime.now() - modified).total_seconds() > 3 * 86400:
        raise ValueError('California full snapshot is stale; freshness is not confirmed')
    return BASE + '/' + name


def fetch_bulk_bills(client, year: int, site_base: str) -> list[dict]:
    url = archive_url(client, year)
    head = client.head(url)
    head.raise_for_status()
    etag = head.headers.get('etag')
    if not etag or head.headers.get('accept-ranges') != 'bytes':
        raise ValueError('California snapshot cannot be read with a stable version')
    # Read only the four metadata tables, not a gigabyte of unrelated media/LOBs.
    with RemoteZip(url, timeout=60, headers={'User-Agent':'keeping-law-simple/1.0', 'If-Match':etag},
            initial_buffer_size=4 * 1024 * 1024) as archive:
        bills = table_rows(archive, 'BILL_TBL.dat', BILL_FIELDS)
        versions = {r['version_id']:r for r in table_rows(archive, 'BILL_VERSION_TBL.dat', VERSION_FIELDS)}
        authors = {}
        for r in table_rows(archive, 'BILL_VERSION_AUTHORS_TBL.dat', AUTHOR_FIELDS):
            if r['active_flg'] == 'Y' and r['primary_author_flg'] == 'Y':
                authors.setdefault(r['version_id'], []).append(r['name'])
        history = {}
        for r in table_rows(archive, 'BILL_HISTORY_TBL.dat', HISTORY_FIELDS):
            previous = history.get(r['bill_id'])
            key = (r['action_date'], int(r['sequence'] or 0), int(r['history_id'] or 0))
            if previous is None or key > previous[0]:
                history[r['bill_id']] = (key, r)
    after = client.head(url)
    after.raise_for_status()
    if after.headers.get('etag') != etag:
        raise ValueError('California snapshot changed during reading; retry later')
    session = f'{year if year % 2 else year-1}{year+1 if year % 2 else year}'
    result = {}
    for b in bills:
        if b['session_year'] != session:
            raise ValueError('California snapshot contains a different session')
        prefix, number, special = b['measure_type'], b['measure_num'], b['session_num']
        if (prefix not in {'AB','SB','ACA','SCA','ACR','SCR','AJR','SJR','HR','SR','GRP'} or not number.isdigit() or not special.isdigit()
                or b['bill_id'] != session + special + prefix + str(int(number))):
            raise ValueError('California snapshot contains a mismatched bill identity')
        version = versions.get(b['latest_bill_version_id'])
        if version is None or version['bill_id'] != b['bill_id']:
            raise ValueError('California snapshot is missing a current bill version')
        bill_num = prefix + (f'X{int(special)}' if int(special) else '') + '-' + str(int(number))
        if bill_num in result:
            raise ValueError('California snapshot repeats a bill identity')
        action = history.get(b['bill_id'], (None, {}))[1]
        path = f"{site_base.rstrip('/')}/faces/billTextClient.xhtml?bill_id={b['bill_id']}"
        title = version['subject'] or bill_num
        result[bill_num] = {'billNum':bill_num,'billType':bill_num.split('-')[0],
            'catchTitle':title,'billTitle':title,'sponsor':', '.join(dict.fromkeys(authors.get(version['version_id'], []))),
            'billStatus':b['current_status'] or b['measure_state'],
            'lastAction':action.get('action') or b['current_status'] or b['measure_state'],
            'lastActionDate':action.get('action_date', '')[:10],
            'chapter':f"Chapter {b['chapter_num']}" if b['chapter_num'] else '',
            'detailPath':path,'currentVersionPath':path + '&version=' + version['version_id'],
            'currentVersionFingerprint':'|'.join((version['version_id'],b['trans_update'],action.get('action_date',''))),
            'sourceArchive':url}
    if not result:
        raise ValueError('California full snapshot contains no bills')
    return list(result.values())
