from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app import alaska_api, sync_service
from app.alaska_api import AlaskaApiClient, AlaskaSourceDeferred
from app.alaska_source_cache import cached_detail, catalog_signature, ensure_source_cache, save_detail
from app.db import connect, get_pipeline_circuit_breaker, update_sync_status
from app.settings import get_settings
from scripts.verify_alaska_sources import finish_verification, validate_catalog
from tests.test_main import _seed_federal_bill


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(alaska_api.time, 'sleep', lambda _: None)
    client = AlaskaApiClient(get_settings())
    client.client.close()
    yield client
    client.close()


def transport(api, handler):
    api.client = httpx.Client(base_url=api.settings.alaska_site_base, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('status,delay', [(429, '1200'), (429, None), (403, None)])
def test_source_hold_survives_a_new_client_without_more_requests(api, status, delay):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'retry-after': delay} if delay else {})
    transport(api, handler)
    with pytest.raises(AlaskaSourceDeferred):
        api.fetch_year_bills(2026)
    assert len(calls) == 1
    hold = get_pipeline_circuit_breaker('alaska-source')
    assert str(status) in hold['reason']
    if delay:
        assert datetime.fromisoformat(hold['open_until']) >= datetime.now(timezone.utc) + timedelta(seconds=1198)
    other = AlaskaApiClient(get_settings())
    other.client.close()
    transport(other, lambda _: pytest.fail('Saved hold must prevent another network request'))
    try:
        with pytest.raises(AlaskaSourceDeferred, match='saved cooldown'):
            other.fetch_year_bills(2026)
    finally:
        other.close()


def test_transport_retries_are_bounded(api):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout('source timeout', request=request)
    transport(api, handler)
    with pytest.raises(AlaskaSourceDeferred, match='three bounded attempts'):
        api.fetch_year_bills(2026)
    assert len(calls) == 3


def test_requests_are_paced(api, monkeypatch):
    sleeps = []
    api._last_request = 100.0
    monkeypatch.setattr(alaska_api.time, 'monotonic', lambda: 101.0)
    monkeypatch.setattr(alaska_api.time, 'sleep', sleeps.append)
    transport(api, lambda _: httpx.Response(200, text='no catalog'))
    with pytest.raises(AlaskaSourceDeferred):
        api.fetch_year_bills(2026)
    assert sleeps == [2.0]


def test_empty_catalog_does_not_become_verified_zero_coverage(api):
    transport(api, lambda _: httpx.Response(200, text='<html>No data available</html>'))
    with pytest.raises(AlaskaSourceDeferred, match='no recognizable bills'):
        api.fetch_year_bills(2026)


def test_checkpoint_expires_and_catalog_change_invalidates_it():
    ensure_source_cache()
    now = datetime.now(timezone.utc)
    item = {'billNum': 'HB1', 'billStatus': 'Pending'}
    save_detail('official/HB1', catalog_signature(item), {'bill': 'HB1'}, now=now)
    assert cached_detail('official/HB1', catalog_signature(item), now=now) == {'bill': 'HB1'}
    assert cached_detail('official/HB1', catalog_signature(item), now=now + timedelta(hours=6)) is None
    assert cached_detail('official/HB1', catalog_signature({**item, 'billStatus': 'Signed'}), now=now) is None


def test_checkpoint_resumes_in_another_worker_and_rejects_wrong_identity(api):
    path = '/basis/Bill/Detail/34?Root=hb1'
    item = {'billNum': 'HB1', 'detailPath': path}
    api._catalog_items[path] = item
    transport(api, lambda _: httpx.Response(200, text='<ul class="information"><li><span>Bill</span><strong>HB 1</strong></li></ul>'))
    assert api.fetch_bill_detail(path)['bill'] == 'HB1'
    other = AlaskaApiClient(get_settings())
    other.client.close()
    other._catalog_items[path] = item
    transport(other, lambda _: pytest.fail('Previously checked detail should be resumed'))
    try:
        assert other.fetch_bill_detail(path)['bill'] == 'HB1'
    finally:
        other.close()
    api._catalog_items[path] = {**item, 'billNum': 'HB2'}
    with pytest.raises(AlaskaSourceDeferred, match='mismatched bill identity'):
        api.fetch_bill_detail(path)


def test_interrupted_scan_keeps_previous_last_success(monkeypatch):
    previous = '2026-10-30T08:00:00+00:00'
    update_sync_status('ak', last_success_at=previous, failed=3)
    class HeldSource:
        def __init__(self, settings): pass
        def close(self): pass
        def fetch_year_bills(self, year):
            return [{'billNum': 'HB1', 'detailPath': 'official/HB1'}, {'billNum': 'HB2'}]
        def fetch_bill_detail(self, path):
            raise AlaskaSourceDeferred('HTTP 429 cooldown')
    monkeypatch.setattr(sync_service, 'AlaskaApiClient', HeldSource)
    with pytest.raises(AlaskaSourceDeferred):
        sync_service.sync_alaska([2026], skip_interpretation=True)
    with connect() as c:
        row = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone())
    assert row['last_success_at'] == previous
    assert row['seen'] == 1 and row['failed'] == 1 and not row['is_running']


@pytest.mark.parametrize('total', [0, 1])
def test_partial_or_empty_verification_cannot_clear_failures(total):
    update_sync_status('ak', failed=3)
    with connect() as c:
        original = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone())
    with pytest.raises(RuntimeError, match='not fully verified'):
        finish_verification(original, [], set(), total)
    with connect() as c:
        assert dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone()) == original


def test_full_verification_preserves_bills_and_cannot_overwrite_a_newer_scan():
    _seed_federal_bill()
    update_sync_status('ak', failed=3)
    with connect() as c:
        c.execute("UPDATE bills SET state='ak',year=2026,bill_num='HB1'")
        c.commit()
        original = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone())
        rows = [dict(r) for r in c.execute("SELECT * FROM bills WHERE state='ak' ORDER BY bill_num").fetchall()]
    update_sync_status('ak', failed=2)
    with pytest.raises(RuntimeError, match='newer Alaska worker'):
        finish_verification(original, rows, {'HB1'}, 1)
    with connect() as c:
        newer = dict(c.execute("SELECT * FROM sync_status WHERE state='ak'").fetchone())
    finish_verification(newer, rows, {'HB1'}, 1)
    with connect() as c:
        assert [dict(r) for r in c.execute("SELECT * FROM bills WHERE state='ak' ORDER BY bill_num").fetchall()] == rows
        assert c.execute("SELECT failed FROM sync_status WHERE state='ak'").fetchone()['failed'] == 0


@pytest.mark.parametrize('items,total,year', [([], 693, 2026), ([{'billNum':'HB1'}], 693, 2026),
    ([{'billNum':'HB1'}], None, 2026), ([{'billNum':'HB1'}], 1, 2025),
    ([{'billNum':'HB1'},{'billNum':'HB1'}], 2, 2026)])
def test_partial_unknown_duplicate_or_wrong_scope_catalog_cannot_clear_failures(items,total,year):
    with pytest.raises(RuntimeError, match='catalog total'):
        validate_catalog(items, {'source_total':total,'current_year':year}, {'HB1':{}})


def test_verifier_checks_every_official_identity_not_just_a_matching_total():
    original = {'source_total':1,'current_year':2026}
    validate_catalog([{'billNum':'HB1'}], original, {'HB1':{}})
    with pytest.raises(RuntimeError, match='New official bills'):
        validate_catalog([{'billNum':'HB2'}], original, {'HB1':{}})
