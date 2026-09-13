from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.analytics import GeoIPResolver
from app.db import connect
from app.settings import get_settings
from app.site_analytics import site_analytics_router


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("KLS_ANALYTICS_ENABLED", "1")
    monkeypatch.setenv("KLS_SITE_ANALYTICS_TOKEN", "test-site-secret")
    get_settings.cache_clear()
    application = FastAPI()
    application.include_router(site_analytics_router(GeoIPResolver(get_settings())))
    return TestClient(application)


def event(**overrides):
    return {
        "event_id": str(uuid4()), "occurred_at": datetime.now(UTC).isoformat(),
        "host": "www.keepinglawsimple.org", "path": "/area/wyoming", "status_code": 200,
        "user_agent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
        "client_ip": "203.0.113.10", "language": "en-US", "fetch_mode": "navigate",
        "referrer": "https://google.com/search?q=private", **overrides,
    }


def send(client, events, **kwargs):
    return client.post("/internal/site-page-views", json={"events": events},
                       headers={"x-kls-analytics-token": "test-site-secret"}, **kwargs)


def test_authentication_before_parsing(client):
    for token in ("", "wrong"):
        response = client.post("/internal/site-page-views", content=b"x" * 70000,
                               headers={"x-kls-analytics-token": token})
        assert response.status_code == 404


def test_anonymized_and_idempotent(client):
    item = event()
    assert send(client, [item]).json() == {"accepted": 1}
    assert send(client, [item]).json() == {"accepted": 0}
    with connect() as connection:
        rows = connection.execute("SELECT * FROM page_views").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["tracking_source"] == "site_server"
    assert row["route_label"] == "area_listing"
    assert row["is_bot"] == 0
    assert row["referrer_domain"] == "google.com"
    assert row["visitor_hash"] and row["visitor_hash"] != item["client_ip"]
    assert item["client_ip"] not in str(row)
    assert "q=private" not in str(row)


@pytest.mark.parametrize("overrides", [
    {"host": "evil.example"}, {"path": "/search?q=private"}, {"path": "/#private"},
    {"status_code": 500}, {"occurred_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()},
    {"occurred_at": "2026-09-12T12:00:00"}, {"event_id": "invalid"},
])
def test_rejects_invalid_events(client, overrides):
    assert send(client, [event(**overrides)]).status_code == 422


def test_bounded_batches(client):
    assert send(client, [event() for _ in range(17)]).status_code == 422
    response = client.post("/internal/site-page-views", content=b"x" * 70000,
                           headers={"x-kls-analytics-token": "test-site-secret"})
    assert response.status_code == 413


def test_excludes_private_and_probe_paths(client):
    paths = ["/healthz", "/readyz", "/api/overview", "/admin/analytics", "/beta/wyoming", "/_next/static"]
    assert send(client, [event(path=path) for path in paths]).json() == {"accepted": 0}


def test_bot_and_browser_counts_remain_separate(client):
    assert send(client, [event(user_agent="curl/8.0"), event(user_agent="unknown-client"), event()]).json() == {"accepted": 3}
    with connect() as connection:
        rows = connection.execute("SELECT is_bot FROM page_views ORDER BY id").fetchall()
    assert [row["is_bot"] for row in rows] == [1, 1, 0]


def test_disabled_collection(client, monkeypatch):
    monkeypatch.setenv("KLS_ANALYTICS_ENABLED", "0")
    get_settings.cache_clear()
    assert send(client, [event()]).status_code == 503
