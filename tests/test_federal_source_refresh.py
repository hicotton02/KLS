import json
from types import SimpleNamespace

import pytest

from app import federal_sync as federal
from app import summary_queue as queue
from app.db import connect, get_bill
from tests.test_federal_sync import bill_payload, item


def held(number=1, *, title='Public services', status='source_hold', processed='2026-09-01T00:00:00Z'):
    federal.save_catalog(119, [item(number)])
    payload = {**bill_payload(number), 'catch_title': title,
               'source_hash': 'original', 'current_version_path': 'https://www.congress.gov/bill',
               'current_bill_text': '', 'interpretation_json': None}
    federal.upsert_bill(payload)
    queue.refresh_queue('us')
    row = federal.pending_work([119], 1)[0]
    federal.mark_work(row)
    with connect() as c:
        c.execute('UPDATE federal_bill_work SET processed_at=?', (processed,))
        c.execute('UPDATE bill_summary_work SET status=?', (status,))
        c.commit()
    return {**row, 'bill_id': get_bill('us', 119, f'HR{number}')['id']}


def published(number=1):
    return SimpleNamespace(payload={**bill_payload(number), 'source_hash': 'published',
        'current_version_path': 'https://www.congress.gov/bill',
        'current_bill_text': 'Official bill text about public services. ' * 20})


def test_unchanged_inventory_rechecks_source_holds_and_obeys_cooldown():
    row = held()
    assert federal.pending_work([119], 10) == []
    assert [r['bill_num'] for r in federal.source_hold_work([119], 10)] == ['HR1']
    federal.mark_work(row)
    assert federal.source_hold_work([119], 10) == []
    assert federal.source_hold_work([119], 0) == []


@pytest.mark.parametrize('status', ['review_hold', 'quality_hold', 'waiting', 'processing', 'complete'])
def test_rechecks_never_select_ai_holds_or_active_work(status):
    held(status=status)
    assert federal.source_hold_work([119], 10) == []


def test_reserved_numbers_and_source_retry_cooldowns_are_not_requeued():
    row = held(title='Reserved for the Speaker.')
    assert federal.source_hold_work([119], 10) == []
    with connect() as c:
        c.execute("UPDATE bills SET catch_title='Public services'")
        c.commit()
    federal.mark_work(row, 'Temporary official-source failure')
    assert federal.source_hold_work([119], 10) == []


def test_late_text_enters_normal_queue_without_resetting_ai_attempts(monkeypatch):
    row = held()
    monkeypatch.setattr(federal, 'refresh_bill', lambda *a, **k: published())
    assert federal.refresh_held_source(None, row)
    queue.refresh_queue('us')
    with connect() as c:
        work = c.execute('SELECT * FROM bill_summary_work WHERE bill_id=?', (row['bill_id'],)).fetchone()
    assert work['status'] == 'pending' and work['attempts'] == 0
    assert get_bill('us', 119, 'HR1')['current_bill_text'] == published().payload['current_bill_text']


def test_missing_source_does_not_write_title_only_content_or_queue_ai(monkeypatch):
    row = held()
    original = get_bill('us', 119, 'HR1')
    candidate = published()
    candidate.payload['current_bill_text'] = ''
    monkeypatch.setattr(federal, 'refresh_bill', lambda *a, **k: candidate)
    assert not federal.refresh_held_source(None, row)
    assert get_bill('us', 119, 'HR1') == original
    assert queue.queue_status('us') == {'source_hold': 1}


def test_newer_record_and_saved_summary_are_preserved(monkeypatch):
    row = held()
    summary = {'one_sentence_summary': 'This bill funds public services.',
               'what_it_does': ['It provides money for services.'], 'fact_check_status': 'validated'}
    def changed(*a, **k):
        with connect() as c:
            c.execute('UPDATE bills SET interpretation_json=?', (json.dumps(summary),))
            c.commit()
        return published()
    monkeypatch.setattr(federal, 'refresh_bill', changed)
    assert not federal.refresh_held_source(None, row)
    assert get_bill('us', 119, 'HR1')['interpretation_json'] == summary
    assert get_bill('us', 119, 'HR1')['current_bill_text'] == ''


def test_wrong_source_identity_cannot_replace_a_held_record(monkeypatch):
    row = held()
    monkeypatch.setattr(federal, 'refresh_bill', lambda *a, **k: published(2))
    with pytest.raises(ValueError, match='identity'):
        federal.refresh_held_source(None, row)
    assert get_bill('us', 119, 'HR1')['current_bill_text'] == ''


def test_lost_source_lease_prevents_a_write(monkeypatch):
    row = held()
    monkeypatch.setattr(federal, 'refresh_bill', lambda *a, **k: published())
    def lost():
        raise RuntimeError('Federal sync lease was lost')
    with pytest.raises(RuntimeError, match='lease was lost'):
        federal.refresh_held_source(None, row, heartbeat=lost)
    assert get_bill('us', 119, 'HR1')['current_bill_text'] == ''


def test_normal_sync_recovers_text_without_an_inventory_version_change(monkeypatch):
    row = held()
    monkeypatch.setattr(federal, 'refresh_bill', lambda *a, **k: published())
    result = federal.sync_federal_inventory([119], limit=1, skip_interpretation=True)
    assert result.seen == result.updated == 1 and result.failed == 0
    assert federal.source_hold_work([119], 10) == []
    queue.refresh_queue('us')
    assert queue.queue_status('us') == {'pending': 1}


def test_source_hold_checks_are_capped_even_with_a_large_batch():
    federal.save_catalog(119, [item(n) for n in range(25)])
    for number in range(25):
        federal.upsert_bill({**bill_payload(number), 'catch_title': 'Public services',
                             'current_version_path': 'https://www.congress.gov/bill'})
    queue.refresh_queue('us')
    for row in federal.pending_work([119], 30):
        federal.mark_work(row)
    with connect() as c:
        c.execute("UPDATE federal_bill_work SET processed_at='2026-09-01T00:00:00Z'")
        c.commit()
    assert len(federal.source_hold_work([119], 1000)) == 20
