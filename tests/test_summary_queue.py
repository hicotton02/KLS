import json

import pytest

from app import summary_queue as queue
from app.db import connect, get_bill


GOOD = {'one_sentence_summary':'This bill funds public schools.',
        'what_it_does':['It sets the education budget for the coming year.'],
        'fact_check_status':'validated'}


def bill(number='HB0001', *, state='wy', text=None, interpretation=None, year=2026):
    with connect() as c:
        c.execute("""INSERT INTO bills (state,year,special_session_key,bill_num,catch_title,
            current_bill_text,current_version_path,source_hash,interpretation_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (state,year,-1,number,'Education funding',text if text is not None else 'Official education funding text. '*20,
             'https://example.gov/bill','source-v1',json.dumps(interpretation) if interpretation else None,
             '2026-10-04','2026-10-04'))
        row=c.execute('SELECT id FROM bills WHERE state=? AND year=? AND bill_num=?',(state,year,number)).fetchone()
        c.commit()
    return row['id']


def work_row(bill_id):
    with connect() as c:
        return dict(c.execute('SELECT * FROM bill_summary_work WHERE bill_id=?',(bill_id,)).fetchone())


def test_queue_resumes_without_resetting_attempts_or_cooldowns():
    bid=bill()
    queue.refresh_queue('wy')
    work=queue.claim('wy')
    queue.finish(work,'retry','Temporary failure',cooldown=3600)
    queue.refresh_queue('wy')
    assert queue.claim('wy') is None
    assert work_row(bid)['attempts']==1
    with connect() as c:
        c.execute("UPDATE bills SET source_hash='new-source' WHERE id=?",(bid,))
        c.commit()
    queue.refresh_queue('wy')
    assert work_row(bid)['attempts']==0
    assert queue.claim('wy') is not None


def test_claims_do_not_overlap_and_expired_lease_is_recovered():
    bid=bill()
    queue.refresh_queue('wy')
    old=queue.claim('wy')
    assert queue.claim('wy') is None
    with connect() as c:
        c.execute("UPDATE bill_summary_work SET lease_expires_at='2000-01-01' WHERE bill_id=?",(bid,))
        c.commit()
    new=queue.claim('wy')
    assert new['owner']!=old['owner'] and new['attempts']==2
    queue.finish(old,'complete')
    assert work_row(bid)['owner']==new['owner']


def test_retry_limit_holds_repeated_failures_and_expired_workers():
    bid=bill()
    queue.refresh_queue('wy')
    for _ in range(queue.MAX_ATTEMPTS):
        assert queue.claim('wy')
        with connect() as c:
            c.execute("UPDATE bill_summary_work SET lease_expires_at='2000-01-01' WHERE bill_id=?",(bid,))
            c.commit()
    assert queue.claim('wy') is None
    assert work_row(bid)['status']=='review_hold'
    queue.refresh_queue('wy')
    assert queue.claim('wy') is None


def test_ready_summary_is_kept_and_missing_source_is_held():
    ready=bill(interpretation=GOOD)
    missing=bill('HB0002',text='Title only')
    queue.refresh_queue('wy')
    assert queue.queue_status('wy')=={'complete':1,'source_hold':1}
    assert queue.claim('wy') is None
    assert work_row(ready)['attempts']==work_row(missing)['attempts']==0


def test_workers_keep_state_scope_and_process_newer_years_first():
    bill('HB0001',year=2025)
    new=bill('HB0002',year=2026)
    federal=bill('HR1',state='us',year=119)
    queue.refresh_queue('wy')
    queue.refresh_queue('us')
    assert queue.claim('wy')['bill_id']==new
    assert queue.claim('us')['bill_id']==federal


def test_saved_summary_is_validated_backed_up_and_does_not_change_source(monkeypatch):
    bid=bill()
    queue.refresh_queue('wy')
    monkeypatch.setattr(queue,'generate',lambda _:GOOD)
    assert queue.process_one(queue.claim('wy'))=='saved'
    saved=get_bill('wy',2026,'HB0001')
    assert saved['interpretation_json']==GOOD
    assert saved['source_hash']=='source-v1'
    assert 'public schools' in saved['search_blob']
    with connect() as c:
        history=c.execute('SELECT * FROM bill_summary_history WHERE bill_id=?',(bid,)).fetchone()
    assert history['interpretation_json'] is None
    assert work_row(bid)['status']=='complete'


def test_failed_quality_does_not_publish_or_count_as_complete(monkeypatch):
    bid=bill()
    queue.refresh_queue('wy')
    monkeypatch.setattr(queue,'generate',lambda _:{'one_sentence_summary':'Education funding','what_it_does':[], 'fact_check_status':'validated'})
    assert queue.process_one(queue.claim('wy'))=='quality_hold'
    assert get_bill('wy',2026,'HB0001')['interpretation_json'] is None
    queue.refresh_queue('wy')
    assert work_row(bid)['status']=='quality_hold'
    assert queue.claim('wy') is None


@pytest.mark.parametrize('change',['source','text','summary'])
def test_save_rejects_concurrent_source_or_summary_changes(monkeypatch,change):
    bid=bill()
    queue.refresh_queue('wy')
    def generate(_):
        with connect() as c:
            if change=='source': c.execute("UPDATE bills SET source_hash='changed' WHERE id=?",(bid,))
            if change=='text': c.execute("UPDATE bills SET current_bill_text='Changed official text' WHERE id=?",(bid,))
            if change=='summary': c.execute('UPDATE bills SET interpretation_json=? WHERE id=?',(json.dumps(GOOD),bid))
            c.commit()
        return GOOD
    monkeypatch.setattr(queue,'generate',generate)
    assert queue.process_one(queue.claim('wy'))=='changed'
    assert work_row(bid)['status']==('complete' if change=='summary' else 'pending')
    with connect() as c:
        assert c.execute('SELECT count(*) AS n FROM bill_summary_history').fetchone()['n']==0


def test_old_worker_cannot_save_after_lease_is_reclaimed(monkeypatch):
    bid=bill()
    queue.refresh_queue('wy')
    old=queue.claim('wy')
    with connect() as c:
        c.execute("UPDATE bill_summary_work SET lease_expires_at='2000-01-01' WHERE bill_id=?",(bid,))
        c.commit()
    new=queue.claim('wy')
    monkeypatch.setattr(queue,'generate',lambda _:GOOD)
    assert queue.process_one(old)=='changed'
    assert work_row(bid)['owner']==new['owner']
    assert get_bill('wy',2026,'HB0001')['interpretation_json'] is None


def test_backend_failure_has_bounded_retry_without_exposing_error_details(monkeypatch):
    bid=bill()
    queue.refresh_queue('wy')
    def error(_): raise RuntimeError('secret=should-not-be-recorded')
    monkeypatch.setattr(queue,'generate',error)
    assert queue.process_one(queue.claim('wy'))=='retry'
    row=work_row(bid)
    assert row['retry_at'] and 'secret' not in row['last_error']


def test_stale_summary_is_rechecked_and_history_keeps_previous_content(monkeypatch):
    old={**GOOD,'fact_check_status':'stale'}
    bid=bill(interpretation=old)
    queue.refresh_queue('wy')
    monkeypatch.setattr(queue,'generate',lambda _:GOOD)
    assert queue.process_one(queue.claim('wy'))=='saved'
    with connect() as c:
        row=c.execute('SELECT interpretation_json FROM bill_summary_history WHERE bill_id=?',(bid,)).fetchone()
    assert json.loads(row['interpretation_json'])==old


def test_run_is_bounded_and_resumes_next_bill(monkeypatch):
    bill()
    bill('HB0002')
    monkeypatch.setattr(queue,'generate',lambda _:GOOD)
    assert queue.run('wy',limit=1)['results']=={'saved':1}
    assert queue.run('wy',limit=1)['results']=={'saved':1}
    assert queue.queue_status('wy')=={'complete':2}


def test_shared_queue_wait_reuses_last_attempt_and_keeps_cooldown(monkeypatch):
    bid = bill()
    queue.refresh_queue('wy')
    with connect() as c:
        c.execute('UPDATE bill_summary_work SET attempts=? WHERE bill_id=?', (queue.MAX_ATTEMPTS - 1, bid))
        c.commit()
    keys = []
    def waiting(data):
        keys.append(data['_summary_queue_key'])
        raise queue.GPUQueuePending()
    monkeypatch.setattr(queue, 'generate', waiting)
    first = queue.claim('wy')
    assert first['attempts'] == queue.MAX_ATTEMPTS
    assert queue.process_one(first) == 'waiting'
    assert work_row(bid)['status'] == 'waiting'
    assert queue.claim('wy') is None
    queue.refresh_queue('wy')
    assert work_row(bid)['status'] == 'waiting'
    with connect() as c:
        c.execute("UPDATE bill_summary_work SET retry_at='2000-01-01' WHERE bill_id=?", (bid,))
        c.commit()
    second = queue.claim('wy')
    assert second['attempts'] == queue.MAX_ATTEMPTS
    assert queue.process_one(second) == 'waiting'
    assert keys[0] == keys[1]
    assert get_bill('wy', 2026, 'HB0001')['interpretation_json'] is None
    with connect() as c:
        c.execute("UPDATE bill_summary_work SET retry_at='2000-01-01' WHERE bill_id=?", (bid,))
        c.commit()
    monkeypatch.setattr(queue, 'generate', lambda _: GOOD)
    assert queue.process_one(queue.claim('wy')) == 'saved'
    assert work_row(bid)['attempts'] == queue.MAX_ATTEMPTS


def test_expired_waiting_worker_resumes_same_attempt_without_duplicate_claim():
    bid = bill()
    queue.refresh_queue('wy')
    original = queue.claim('wy')
    with connect() as c:
        c.execute("""UPDATE bill_summary_work SET last_error=?,lease_expires_at='2000-01-01',
            attempts=? WHERE bill_id=?""", (queue.QUEUE_WAIT_ERROR, queue.MAX_ATTEMPTS, bid))
        c.commit()
    resumed = queue.claim('wy')
    assert resumed['owner'] != original['owner']
    assert resumed['attempts'] == queue.MAX_ATTEMPTS
    assert queue.claim('wy') is None
    queue.finish(original, 'complete')
    assert work_row(bid)['owner'] == resumed['owner']


def test_real_failure_after_queue_wait_still_stops_at_retry_limit(monkeypatch):
    bid = bill()
    queue.refresh_queue('wy')
    with connect() as c:
        c.execute("UPDATE bill_summary_work SET status='waiting',attempts=? WHERE bill_id=?",
            (queue.MAX_ATTEMPTS, bid))
        c.commit()
    def failed(_):
        raise RuntimeError('Shared job failed')
    monkeypatch.setattr(queue, 'generate', failed)
    assert queue.process_one(queue.claim('wy')) == 'retry'
    assert work_row(bid)['status'] == 'review_hold'
    assert work_row(bid)['attempts'] == queue.MAX_ATTEMPTS
    assert queue.claim('wy') is None
