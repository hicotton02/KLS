from app import db
from tests.test_main import _seed_state_bill


def test_postgres_search_matches_before_sorting_and_keeps_candidate_limit(monkeypatch):
    calls=[]
    class PGSpy(db.PostgresConnection):
        def __init__(self):pass
        def __exit__(self,*args):pass
        def execute(self,sql,params=None):
            calls.append((sql,params))
            return self
        def fetchall(self):return []
    monkeypatch.setattr(db,'connect',PGSpy)
    assert db.search_bills('Wyoming',state='wy',year=2026,status='passed',tag='firearms',limit=60)==[]
    sql,params=calls[0]
    assert 'WITH search_matches AS MATERIALIZED' in sql
    assert 'SELECT id, year, last_action_date, bill_num FROM bills' in sql
    assert 'SELECT id FROM search_matches ORDER BY' in sql
    assert 'id IN (' in sql and sql.count('LIMIT ?')==1
    assert params==['wy',2026,'passed','"wyoming"','%"firearms"%',300]


def test_tag_deduplication_keeps_all_tags_and_scope():
    _seed_state_bill('HB0001','One',year=2026,tags=['education','taxes'])
    _seed_state_bill('HB0002','Two',year=2026,tags=['education','taxes'])
    _seed_state_bill('HB0003','Three',year=2026,tags=[' Education ','firearms'])
    _seed_state_bill('HB0004','Earlier',year=2019,tags=['healthcare'])
    _seed_state_bill('HB0005','Malformed',year=2026,tags=[])
    with db.connect() as c:
        c.execute("UPDATE bills SET bill_tags_json='not-json' WHERE bill_num='HB0005'")
        c.commit()
    assert db.list_available_tags('wy',2026)==['education','firearms','taxes']
    assert db.list_available_tags()==['education','firearms','healthcare','taxes']
