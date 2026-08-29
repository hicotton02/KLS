from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from starlette.requests import Request

from app.analytics import bot_reason_for_request
from app.db import get_analytics_overview, init_db, record_page_view
from app.main import app


def _basic_auth(username: str = "admin", password: str = "test-password") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "client": ("203.0.113.10", 443),
            "server": ("www.keepinglawsimple.org", 443),
            "scheme": "https",
            "query_string": b"",
        }
    )


def test_bot_detection_catches_automation_and_spoofed_browsers() -> None:
    assert bot_reason_for_request(_request({"user-agent": "Lightpanda/1.0"})) == "automation_user_agent"
    assert bot_reason_for_request(_request({"user-agent": "Claude-User/1.0"})) == "automation_user_agent"
    assert bot_reason_for_request(_request({"user-agent": "ChatGPT-User/1.0"})) == "automation_user_agent"
    assert bot_reason_for_request(_request({"user-agent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36"})) == "missing_browser_headers"
    assert bot_reason_for_request(
        _request(
            {
                "user-agent": "Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36",
                "accept-language": "en-US,en;q=0.9",
                "sec-fetch-mode": "navigate",
            }
        )
    ) is None


def test_security_headers_are_applied_on_html_pages() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"].startswith("default-src 'self';")
    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "https://www.googletagmanager.com" in response.headers["content-security-policy"]
    assert "https://www.google-analytics.com" in response.headers["content-security-policy"]


def test_invalid_public_host_is_rejected() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/", headers={"host": "evil.example"}, follow_redirects=False)

    assert response.status_code == 400
    assert response.text == "Invalid host header"


def test_metrics_endpoint_exposes_prometheus_metrics() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/metrics", headers={"host": "127.0.0.1"})

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "kls_http_requests_total" in response.text
    assert "kls_http_request_duration_seconds" in response.text
    assert "kls_tracked_page_views_by_route_total" in response.text
    assert "kls_tracked_page_views_by_country_total" in response.text
    assert "kls_tracked_page_views_by_location_total" in response.text
    assert 'kls_legislative_media_backlog{stage="transcription",state="wy"} 0.0' in response.text


def test_metrics_endpoint_is_hidden_on_public_hosts() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/metrics", headers={"host": "www.keepinglawsimple.org"})

    assert response.status_code == 404


def test_admin_analytics_requires_basic_auth() -> None:
    init_db()
    client = TestClient(app)

    response = client.get("/admin/analytics")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="Keeping Law Simple Admin"'


def test_admin_analytics_shows_seeded_summary() -> None:
    init_db()
    now = datetime.now(tz=UTC).replace(microsecond=0)
    first_view = (now - timedelta(hours=2)).isoformat()
    second_view = (now - timedelta(hours=1)).isoformat()
    bot_view = now.isoformat()
    record_page_view(
        {
            "occurred_at": first_view,
            "created_at": first_view,
            "host": "www.keepinglawsimple.org",
            "path": "/states/wyoming",
            "route_label": "state_listing",
            "method": "GET",
            "status_code": 200,
            "referrer_domain": "google.com",
            "country_code": "US",
            "country_name": "United States",
            "visitor_hash": "visitor-a",
            "is_bot": False,
            "user_agent": "Mozilla/5.0",
        }
    )
    record_page_view(
        {
            "occurred_at": second_view,
            "created_at": second_view,
            "host": "www.keepinglawsimple.org",
            "path": "/states/wyoming/bills/2026/HB0126",
            "route_label": "state_bill_detail",
            "method": "GET",
            "status_code": 200,
            "referrer_domain": "www.keepinglawsimple.org",
            "country_code": "CA",
            "country_name": "Canada",
            "visitor_hash": "visitor-b",
            "is_bot": False,
            "user_agent": "Mozilla/5.0",
        }
    )
    record_page_view(
        {
            "occurred_at": bot_view,
            "created_at": bot_view,
            "host": "www.keepinglawsimple.org",
            "path": "/search",
            "route_label": "search",
            "method": "GET",
            "status_code": 200,
            "referrer_domain": "bing.com",
            "country_code": "US",
            "country_name": "United States",
            "visitor_hash": "bot-1",
            "is_bot": True,
            "bot_reason": "declared_bot",
            "user_agent": "bingbot/2.0",
        }
    )
    client = TestClient(app)

    response = client.get("/admin/analytics", headers=_basic_auth())

    assert response.status_code == 200
    assert "Traffic and audience snapshot." in response.text
    assert "google.com" in response.text
    assert "United States" in response.text
    assert "Canada" in response.text
    assert "Declared Bot" in response.text
    assert "private, no-store" in response.headers["cache-control"]


def test_get_analytics_overview_filters_internal_referrers() -> None:
    init_db()
    record_page_view(
        {
            "occurred_at": "2026-04-11T09:00:00+00:00",
            "created_at": "2026-04-11T09:00:00+00:00",
            "host": "www.keepinglawsimple.org",
            "path": "/states/wyoming",
            "route_label": "state_listing",
            "method": "GET",
            "status_code": 200,
            "referrer_domain": "duckduckgo.com",
            "country_code": "US",
            "country_name": "United States",
            "visitor_hash": "visitor-a",
            "is_bot": False,
            "user_agent": "Mozilla/5.0",
        }
    )
    record_page_view(
        {
            "occurred_at": "2026-04-11T10:00:00+00:00",
            "created_at": "2026-04-11T10:00:00+00:00",
            "host": "www.keepinglawsimple.org",
            "path": "/federal",
            "route_label": "federal_listing",
            "method": "GET",
            "status_code": 200,
            "referrer_domain": "www.keepinglawsimple.org",
            "country_code": "US",
            "country_name": "United States",
            "visitor_hash": "visitor-b",
            "is_bot": False,
            "user_agent": "Mozilla/5.0",
        }
    )

    analytics = get_analytics_overview(
        internal_hosts=("keepinglawsimple.org", "www.keepinglawsimple.org"),
        since_24h="2026-04-10T00:00:00+00:00",
        since_7d="2026-04-04T00:00:00+00:00",
        since_30d="2026-03-12T00:00:00+00:00",
    )

    assert analytics["windows"]["24h"]["total_views"] == 2
    assert analytics["windows"]["24h"]["human_views"] == 2
    assert analytics["windows"]["24h"]["human_visitors"] == 2
    assert analytics["top_referrers"] == [{"referrer_domain": "duckduckgo.com", "hits": 1}]
