import csv
from datetime import datetime
import io
import zipfile

import httpx
import pytest

from app import california_bulk as bulk
from app.california_api import CaliforniaApiClient
from app.settings import get_settings


class FixedDate(datetime):
    @classmethod
    def now(cls):
        return cls(2026,10,17,14)


def snapshot(*, special='0', prefix='AB', duplicate=False, missing_version=False, bad_session=False):
    session = '20232024' if bad_session else '20252026'
    bid = session + special + prefix + '1'
    version = 'current-version'
    bill = dict(bill_id=bid,session_year=session,session_num=special,measure_type=prefix,measure_num='1',
        latest_bill_version_id=version,active_flg='Y',current_status='Chaptered',chapter_num='42',
        trans_update='2026-10-16 10:00:00')
    tables = [('BILL_TBL.dat', bulk.BILL_FIELDS, [bill,bill] if duplicate else [bill]),
        ('BILL_VERSION_TBL.dat', bulk.VERSION_FIELDS, [] if missing_version else [dict(version_id=version,bill_id=bid,subject='Flood protection.')]),
        ('BILL_VERSION_AUTHORS_TBL.dat', bulk.AUTHOR_FIELDS, [dict(version_id=version,name='Example',active_flg='Y',primary_author_flg='Y')]),
        ('BILL_HISTORY_TBL.dat', bulk.HISTORY_FIELDS, [dict(bill_id=bid,history_id='1',sequence='1',action_date='2026-10-15',action='Passed.'),
            dict(bill_id=bid,history_id='2',sequence='2',action_date='2026-10-16',action='Chaptered.')])]
    output = io.BytesIO()
    with zipfile.ZipFile(output,'w') as z:
        for name,fields,rows in tables:
            text = io.StringIO()
            writer = csv.writer(text,delimiter='\t',quotechar='`',lineterminator='\n')
            writer.writerows([[r.get(f,'NULL') for f in fields] for r in rows])
            z.writestr(name,text.getvalue())
    return output.getvalue()


def client(monkeypatch, data=None, *, changed=False, stale=False):
    monkeypatch.setattr(bulk,'datetime',FixedDate)
    payload = snapshot() if data is None else data
    def remote(url, **kwargs):
        assert url == bulk.BASE + '/pubinfo_daily_Sat.zip'
        assert kwargs['headers']['If-Match'] == '"test-snapshot"'
        return zipfile.ZipFile(io.BytesIO(payload))
    monkeypatch.setattr(bulk,'RemoteZip',remote)
    heads = []
    def handler(r):
        if r.url.path.endswith('billSearchClient.xhtml'):
            return httpx.Response(403,request=r)
        if r.method == 'HEAD':
            heads.append(r)
            etag = '"changed"' if changed and len(heads)>1 else '"test-snapshot"'
            return httpx.Response(200,headers={'etag':etag,'accept-ranges':'bytes'},request=r)
        date = '2026-10-01' if stale else '2026-10-17'
        return httpx.Response(200,text=f'<table><tr><td></td><td><a href="pubinfo_daily_Sat.zip">file</a></td><td>{date} 04:23</td></tr></table>',request=r)
    api = CaliforniaApiClient(get_settings())
    api.client.close()
    api.client = httpx.Client(base_url=api.settings.california_site_base,transport=httpx.MockTransport(handler))
    return api


def test_blocked_search_uses_official_full_metadata_snapshot(monkeypatch):
    api = client(monkeypatch)
    try:
        rows = api.fetch_year_bills(2026)
    finally:
        api.close()
    assert len(rows)==1
    r=rows[0]
    assert r['billNum']=='AB-1' and r['catchTitle']=='Flood protection.'
    assert r['sponsor']=='Example' and r['chapter']=='Chapter 42'
    assert r['lastAction']=='Chaptered.' and r['lastActionDate']=='2026-10-16'
    assert 'bill_id=202520260AB1' in r['detailPath']
    assert 'current-version' in r['currentVersionFingerprint']


@pytest.mark.parametrize('kwargs', [dict(duplicate=True),dict(missing_version=True),dict(bad_session=True)])
def test_bad_snapshot_does_not_report_complete_coverage(monkeypatch,kwargs):
    api=client(monkeypatch,snapshot(**kwargs))
    try:
        with pytest.raises(ValueError): api.fetch_year_bills(2026)
    finally:
        api.close()


@pytest.mark.parametrize('kwargs',[dict(changed=True),dict(stale=True)])
def test_changed_or_stale_snapshot_is_rejected(monkeypatch,kwargs):
    api=client(monkeypatch,**kwargs)
    try:
        with pytest.raises(ValueError): api.fetch_year_bills(2026)
    finally:
        api.close()


@pytest.mark.parametrize('prefix,special,expected',[('AB','1','ABX1-1'),('GRP','0','GRP-1'),('HR','0','HR-1')])
def test_extraordinary_sessions_and_other_official_measure_types(monkeypatch,prefix,special,expected):
    api=client(monkeypatch,snapshot(prefix=prefix,special=special))
    try:
        assert api.fetch_year_bills(2026)[0]['billNum']==expected
    finally:
        api.close()


def test_column_changes_fail_closed():
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z: z.writestr('table.dat','one\ttwo\n')
    with zipfile.ZipFile(data) as z:
        with pytest.raises(ValueError): bulk.table_rows(z,'table.dat',('one',))
