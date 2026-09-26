"""Optional PostgreSQL regression test using session-local, rolled-back tables."""
import os

import unittest
from unittest.mock import patch

from app import db, federal_sync as fs


def check_postgres_checkpoint():
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
        conn.execute('CREATE TEMP TABLE bills(state TEXT,year INTEGER,bill_num TEXT,special_session_key INTEGER)')
        for statement in db.SCHEMA.split(';'):
            if statement.strip().startswith('CREATE TABLE IF NOT EXISTS federal_'):
                conn.execute(statement.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMP TABLE'))
        with patch.object(fs, 'connect', lambda: Borrowed(conn)):
            fs.save_catalog(119, [{'type': 'HR', 'number': '1', 'congress': 119}])
            with fs.sync_lease() as renew:
                assert renew is not None
                renew()
                row = fs.pending_work([119], 1)[0]
                fs.mark_work(row, 'Temporary failure')
                assert fs.coverage([119])['failed'] == 1
                conn.execute("INSERT INTO bills VALUES ('us',119,'HR1',-1)")
                fs.mark_work(row)
                assert fs.coverage([119])['pending'] == 0
                assert fs.coverage([119])['failed'] == 0
    finally:
        conn.rollback()
        conn.close()


@unittest.skipUnless(os.getenv('KLS_TEST_POSTGRES_URL'), 'PostgreSQL integration URL not provided')
class FederalPostgresTests(unittest.TestCase):
    def test_checkpoint_accepts_null_error_without_touching_public_tables(self):
        check_postgres_checkpoint()


if __name__ == '__main__':
    unittest.main()
