import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import recent_bill_index_sql
from tests.test_main import _seed_federal_bill


@pytest.mark.parametrize('query,tag,expected', [('', '', 17), ('school', '', None), ('', 'education', None)])
def test_area_limits_browse_at_database_but_keeps_full_search_ranking(monkeypatch, query, tag, expected):
    _seed_federal_bill()
    calls = []
    monkeypatch.setattr(main, 'list_bills', lambda *a, **kw: calls.append(kw) or [])
    with TestClient(main.app) as client:
        response = client.get('/api/v1/areas/federal', params={'q': query, 'tag': tag, 'limit': 17})
    assert response.status_code == 200
    assert calls[0]['limit'] == expected
    assert response.json()['counts']['total'] == 1


def test_recent_index_has_the_same_date_guard_and_sort_as_the_listing():
    sql = recent_bill_index_sql(concurrently=True)
    assert sql.startswith('CREATE INDEX CONCURRENTLY IF NOT EXISTS')
    assert "SUBSTR(last_action_date, 5, 1) = '-'" in sql
    assert "SUBSTR(last_action_date, 8, 1) = '-'" in sql
    assert 'updated_at DESC, year DESC, bill_num ASC' in sql
    assert "WHERE interpretation_json LIKE '%validated%'" in sql
    assert 'CONCURRENTLY' not in recent_bill_index_sql()
