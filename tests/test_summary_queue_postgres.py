"""Optional PostgreSQL regression test using rolled-back temporary tables."""
import json
import os
import unittest
from unittest.mock import patch

from app import db, summary_queue as queue


def check_postgres_summary_queue():
    import psycopg
    from psycopg.rows import dict_row

    class Borrowed(db.PostgresConnection):
        def __exit__(self, *args):
            pass

        def commit(self):
            pass

    conn = psycopg.connect(os.environ['KLS_TEST_POSTGRES_URL'], row_factory=dict_row,
        options='-c default_transaction_read_only=off -c statement_timeout=10000')
    try:
        conn.execute('CREATE TEMP TABLE bills (LIKE public.bills INCLUDING ALL)')
        for statement in db.SCHEMA.split(';'):
            if statement.strip().startswith('CREATE TABLE IF NOT EXISTS bill_summary_'):
                conn.execute(statement.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE'))
        conn.execute("""INSERT INTO bills
            (id,state,year,special_session_key,bill_num,catch_title,current_bill_text,
             current_version_path,source_hash,created_at,updated_at)
            VALUES (2000000000,'wy',2026,-1,'HBTEST','School funding',
                repeat('Official school funding text. ',20),'https://example.gov/bill',
                'original','2026-10-04','2026-10-04')""")
        good = {'one_sentence_summary': 'This bill funds public schools.',
                'what_it_does': ['It sets the education budget for the coming year.'],
                'fact_check_status': 'validated'}
        with patch.object(queue, 'connect', lambda: Borrowed(conn)), \
                patch.object(queue, 'list_bill_amendments', lambda *a, **kw: []):
            queue.refresh_queue('wy')
            old = queue.claim('wy')
            assert queue.claim('wy') is None
            conn.execute("UPDATE bill_summary_work SET lease_expires_at='2000-01-01'")
            work = queue.claim('wy')
            queue.finish(old, 'complete')
            assert queue.queue_status('wy') == {'processing': 1}
            original = db._parse_row(conn.execute('SELECT * FROM bills').fetchone())
            conn.execute("UPDATE bills SET source_hash='changed'")
            assert not queue.save_result(work, original, good)
            assert queue.queue_status('wy') == {'pending': 1}
            assert conn.execute('SELECT count(*) AS n FROM bill_summary_history').fetchone()['n'] == 0
            work = queue.claim('wy')
            original = db._parse_row(conn.execute('SELECT * FROM bills').fetchone())
            assert queue.save_result(work, original, good)
            assert queue.queue_status('wy') == {'complete': 1}
            saved = conn.execute('SELECT * FROM bills').fetchone()
            assert saved['source_hash'] == 'changed'
            assert json.loads(saved['interpretation_json']) == good
            assert conn.execute('SELECT count(*) AS n FROM bill_summary_history').fetchone()['n'] == 1
    finally:
        conn.rollback()
        conn.close()


@unittest.skipUnless(os.getenv('KLS_TEST_POSTGRES_URL'), 'PostgreSQL integration URL not provided')
class SummaryQueuePostgresTests(unittest.TestCase):
    def test_postgres_claim_and_guarded_publish_without_public_writes(self):
        check_postgres_summary_queue()


if __name__ == '__main__':
    unittest.main()
