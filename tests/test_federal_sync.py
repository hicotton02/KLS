import json
from dataclasses import replace

import httpx
import pytest

from app.db import connect, get_sync_status, upsert_bill
from app.federal_api import CongressApiClient
from app.federal_sync import coverage, mark_work, pending_work, save_catalog, sync_federal_inventory, sync_lease
from app.settings import get_settings


def item(number, **changes):
    return {'congress':119,'type':'HR','number':str(number),'title':f'Bill {number}',
            'updateDate':'2026-09-20','updateDateIncludingText':'2026-09-20',**changes}


def bill_payload(number):
    with connect() as c:
        payload={r['name']:None for r in c.execute('PRAGMA table_info(bills)').fetchall()}
    return {**payload,'state':'us','year':119,'bill_num':f'HR{number}',
            'created_at':'2026-09-20T00:00:00+00:00','updated_at':'2026-09-20T00:00:00+00:00'}


def store_bill(number):
    upsert_bill(bill_payload(number))


def test_catalog_fetches_every_page_and_drops_api_urls(monkeypatch):
    api=CongressApiClient(get_settings())
    offsets=[]
    def request(path,params):
        offsets.append(params['offset'])
        numbers=range(1,251) if params['offset']==0 else range(251,252)
        return {'pagination':{'count':251},'bills':[item(n,url='https://example?api_key=secret') for n in numbers]}
    monkeypatch.setattr(api,'_request_json',request)
    try:
        result=api.fetch_bill_catalog(119)
    finally: api.close()
    assert len(result)==251 and offsets==[0,250]
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('pages',[
    [{'pagination':{},'bills':[]}],
    [{'pagination':{'count':2},'bills':[]}],
    [{'pagination':{'count':1},'bills':[item(1,congress=118)]}],
    [{'pagination':{'count':2},'bills':[item(1)]},{'pagination':{'count':3},'bills':[item(2)]}],
])
def test_catalog_rejects_incomplete_or_changing_inventory(monkeypatch,pages):
    api=CongressApiClient(get_settings())
    responses=iter(pages)
    monkeypatch.setattr(api,'_request_json',lambda *a,**kw: next(responses))
    try:
        with pytest.raises(ValueError): api.fetch_bill_catalog(119)
    finally: api.close()


@pytest.mark.parametrize('fault', [None, 'duplicate', 'wrong_type', 'short', 'changed', 'unknown'])
def test_catalog_overlap_uses_verified_type_partitions(monkeypatch, fault):
    api = CongressApiClient(get_settings())
    global_requests = []
    paths = []
    heartbeats = []

    def request(path, params):
        paths.append(path)
        if path == '/bill/119' and params['limit'] == 250:
            return {'pagination': {'count': 2}, 'bills': [item(1), item(1)]}
        if path == '/bill/119':
            global_requests.append(path)
            count = 3 if fault == 'changed' and len(global_requests) == 2 else 2
            return {'pagination': {'count': count}, 'bills': []}
        kind = path.rsplit('/', 1)[-1].upper()
        rows = [item(1, type=kind)] if kind in ('HR', 'S') else []
        if fault == 'duplicate' and kind == 'HR':
            rows *= 2
        if fault == 'wrong_type' and kind == 'HR':
            rows = [item(1, type='S')]
        if fault == 'short' and kind == 'S':
            rows = []
        count = None if fault == 'unknown' and kind == 'HR' else len(rows)
        return {'pagination': {'count': count}, 'bills': rows}

    monkeypatch.setattr(api, '_request_json', request)
    try:
        if fault:
            with pytest.raises(ValueError):
                api.fetch_bill_catalog(119)
        else:
            rows = api.fetch_bill_catalog(119, heartbeat=lambda: heartbeats.append(True))
            assert [(r['type'], r['number']) for r in rows] == [('HR', '1'), ('S', '1')]
            assert len(paths) == len(heartbeats) == 11
            assert len(global_requests) == 2
    finally:
        api.close()


def test_work_resumes_and_requeues_text_only_changes():
    save_catalog(119,[item(1),item(2)])
    assert coverage([119])=={'source_total':2,'inventory':2,'pending':2,'failed':0,'stored':0}
    row=pending_work([119],1)[0]
    store_bill(1)
    mark_work(row)
    assert [r['bill_num'] for r in pending_work([119],10)]==['HR2']
    save_catalog(119,[item(1),item(2)])
    assert coverage([119])['pending']==1
    save_catalog(119,[item(1,updateDateIncludingText='2026-09-21'),item(2)])
    assert coverage([119])['pending']==2
    mark_work(row)
    assert coverage([119])['pending']==2  # Old work cannot acknowledge a newer source version.


def test_failed_work_has_cooldown_and_does_not_starve_new_bills():
    save_catalog(119,[item(1),item(2)])
    mark_work(pending_work([119],1)[0],'Temporary source failure')
    assert [r['bill_num'] for r in pending_work([119],10)]==['HR2']
    assert coverage([119])['failed']==1
    assert coverage([119])['stored']==0


def test_new_catalog_does_not_count_removed_inventory_items(monkeypatch):
    monkeypatch.setattr('app.federal_sync.utc_now', lambda: '2026-09-26T00:00:00+00:00')
    save_catalog(119, [item(1), item(2)])
    save_catalog(119, [item(2)])
    assert coverage([119])['inventory'] == 1
    assert [r['bill_num'] for r in pending_work([119], 10)] == ['HR2']
    with connect() as c:
        assert c.execute('SELECT count(*) AS n FROM federal_bill_work').fetchone()['n'] == 2


def test_failed_save_leaves_work_pending_and_retries_bounded(monkeypatch):
    from app import federal_sync
    from types import SimpleNamespace
    monkeypatch.setattr(CongressApiClient, 'fetch_bill_catalog', lambda *a, **kw: [item(1)])
    monkeypatch.setattr(federal_sync, 'refresh_bill', lambda *a, **kw: SimpleNamespace(payload={}))
    def failed_save(payload):
        raise RuntimeError('Database write failed')
    monkeypatch.setattr(federal_sync, 'upsert_bill', failed_save)
    result = sync_federal_inventory([119], limit=1, skip_interpretation=True)
    assert result.failed == 1 and coverage([119])['pending'] == 1
    assert pending_work([119], 10) == []
    assert not get_sync_status('us').get('last_success_at')


def test_wrong_source_identity_is_not_saved(monkeypatch):
    monkeypatch.setattr(CongressApiClient, 'fetch_bill_catalog', lambda *a, **kw: [item(1)])
    monkeypatch.setattr(CongressApiClient, 'fetch_bill_detail', lambda *a, **kw: item(2))
    result = sync_federal_inventory([119], limit=1, skip_interpretation=True)
    assert result.failed == 1 and coverage([119])['stored'] == 0
    assert 'identity' in get_sync_status('us')['last_message'] or coverage([119])['failed'] == 1


def test_lease_prevents_overlapping_daily_and_catchup_workers():
    with sync_lease() as first:
        assert first is not None
        first()
        with sync_lease() as second:
            assert second is None
    with sync_lease() as next_worker:
        assert next_worker is not None


def test_expired_lease_cannot_acknowledge_or_clear_other_worker():
    with sync_lease() as first:
        with connect() as c:
            c.execute("UPDATE federal_sync_lease SET expires_at='2000-01-01T00:00:00+00:00'")
            c.commit()
        with sync_lease() as second:
            assert second is not None
            with pytest.raises(RuntimeError,match='lease was lost'): first()
            second()


def test_partial_batch_never_claims_complete_scan(monkeypatch):
    monkeypatch.setattr(CongressApiClient,'fetch_bill_catalog',lambda *a,**kw:[item(1),item(2)])
    result=sync_federal_inventory([119],limit=0,skip_interpretation=True)
    assert result.failed==0
    state=get_sync_status('us')
    assert state['source_total']==2 and state['stored_total']==0
    assert not state.get('last_success_at')
    assert '2 awaiting refresh' in state['last_message']


def test_source_errors_do_not_expose_api_key(monkeypatch):
    api=CongressApiClient(replace(get_settings(),congress_api_key='test-sensitive-key'))
    api.client.close()
    api.client=httpx.Client(base_url='https://example.test',transport=httpx.MockTransport(lambda r:httpx.Response(403)))
    try:
        with pytest.raises(RuntimeError) as error: api.fetch_bill_detail(119,'HR',1)
        assert 'test-sensitive-key' not in str(error.value)
        assert '403' in str(error.value)
    finally: api.close()


def test_refresh_uses_existing_source_pipeline_and_acknowledges_saved_bill(monkeypatch):
    from app import federal_sync
    from app.sync_service import CompletedBillSync
    monkeypatch.setattr(CongressApiClient,'fetch_bill_catalog',lambda *a,**kw:[item(1),item(2)])
    calls=[]
    def refresh(api,row,**kwargs):
        calls.append(row['bill_num'])
        return CompletedBillSync(index_key=(119,-1,row['bill_num']),bill_num=row['bill_num'],year=119,
            payload=bill_payload(row['bill_num'][2:]),
            index_payload={},amendments=[])
    monkeypatch.setattr(federal_sync,'refresh_bill',refresh)
    sync_federal_inventory([119],limit=1,skip_interpretation=True)
    assert calls==['HR1'] and not get_sync_status('us').get('last_success_at')
    sync_federal_inventory([119],limit=1,skip_interpretation=True)
    assert calls==['HR1','HR2']
    assert coverage([119])['pending']==0 and get_sync_status('us')['last_success_at']


def test_actual_refresh_pipeline_saves_source_only_bill(monkeypatch):
    from app.db import get_bill
    monkeypatch.setattr(CongressApiClient,'fetch_bill_catalog',lambda *a,**kw:[item(1)])
    monkeypatch.setattr(CongressApiClient,'fetch_bill_detail',lambda *a,**kw:item(1))
    for method in ('fetch_bill_summaries','fetch_bill_text_versions','fetch_bill_actions'):
        monkeypatch.setattr(CongressApiClient,method,lambda *a,**kw:[])
    result=sync_federal_inventory([119],limit=1,skip_interpretation=True)
    assert result.failed==0 and result.updated==1
    assert get_bill('us',119,'HR1')['catch_title']=='Bill 1'
    assert coverage([119])['pending']==0
