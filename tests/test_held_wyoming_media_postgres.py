"""Optional publication-guard test using rolled-back PostgreSQL temporary rows."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import db
from scripts import recover_held_wyoming_media as recovery


def check_postgres_held_media():
    import psycopg
    from psycopg.rows import dict_row
    class Borrowed(db.PostgresConnection):
        def __exit__(self, *args): pass
        def commit(self): pass
    connection=psycopg.connect(os.environ['KLS_TEST_POSTGRES_URL'], row_factory=dict_row,
                              options='-c default_transaction_read_only=off -c statement_timeout=10000')
    try:
        connection.execute('CREATE TEMP TABLE legislative_media (LIKE public.legislative_media INCLUDING ALL)')
        connection.execute("""INSERT INTO legislative_media(id,state,year,session_date,chamber,time_of_day,
            source_url,source_kind,transcript_status,source_synced_at,created_at,updated_at)
            VALUES (2000000001,'wy',2018,'2018-02-27','H','PM','https://wyoleg.gov/recording.mp3',
            'official_media','quality_failed','2026-11-21','2026-11-21','2026-11-21')""")
        original=dict(connection.execute('SELECT * FROM legislative_media').fetchone())
        with tempfile.TemporaryDirectory() as temp, patch.object(recovery,'connect',lambda:Borrowed(connection)), \
                patch.object(recovery,'_require_durable_backup',lambda _:None):
            root=Path(temp)
            recovery._write_json(root/'2000000001-before.json',{'media':original})
            recovery._write_json(root/'2000000001-candidate.json',{'media_id':2000000001,'status':'available',
                'source':'speech_to_text','duration_seconds':60,'segments':[{'start':0,'end':20,'text':'Official speech.'}]})
            connection.execute("UPDATE legislative_media SET source_url='https://wyoleg.gov/changed.mp3'")
            assert recovery.publish_candidate(2000000001,root)=='changed'
            assert connection.execute('SELECT transcript_status FROM legislative_media').fetchone()['transcript_status']=='quality_failed'
            connection.execute('UPDATE legislative_media SET source_url=%s',(original['source_url'],))
            assert recovery.publish_candidate(2000000001,root)=='saved'
            assert recovery.publish_candidate(2000000001,root)=='preserved'
            row=connection.execute('SELECT * FROM legislative_media').fetchone()
            assert row['transcript_status']=='available' and row['explanation_scan_status']=='pending'
    finally:
        connection.rollback()
        connection.close()


@unittest.skipUnless(os.getenv('KLS_TEST_POSTGRES_URL'),'PostgreSQL integration URL not provided')
class HeldMediaPostgresTests(unittest.TestCase):
    def test_publication_guard_without_public_writes(self):
        check_postgres_held_media()
