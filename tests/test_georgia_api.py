from __future__ import annotations

import httpx

from app.georgia_api import GeorgiaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_georgia_search_results() -> None:
    settings = get_settings()
    api = GeorgiaApiClient(settings)
    api.close()

    sessions_payload = [
        {
            "id": 1033,
            "description": "2025-2026 Regular Session",
            "library": "20252026",
        }
    ]
    search_payload = {
        "resultCount": 2,
        "results": [
            {
                "legislationId": 69281,
                "chamberType": 1,
                "documentType": 1,
                "number": 1,
                "caption": "School safety plan",
                "status": "House Second Readers",
                "statusDate": "2025-01-15T00:00:00",
                "sponsors": [{"name": "Rep. Tester", "sequence": 1}],
            },
            {
                "legislationId": 69285,
                "chamberType": 2,
                "documentType": 1,
                "number": 7,
                "caption": "Budget changes",
                "status": "Senate Date Signed by Governor",
                "statusDate": "2025-04-19T00:00:00",
                "sponsors": [{"name": "Sen. Example", "sequence": 1}],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/authentication/token":
            return httpx.Response(200, text='"token"', request=request)
        if request.url.path == "/api/sessions":
            return httpx.Response(200, json=sessions_payload, request=request)
        if request.url.path == "/api/Legislation/Search/100/0":
            return httpx.Response(200, json=search_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.georgia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SB7"]
    assert items[0]["billTitle"] == "School safety plan"
    assert items[1]["signedDate"] == "2025-04-19"
    assert items[1]["detailPath"] == "https://www.legis.ga.gov/api/Legislation/detail/69285"


def test_fetch_bill_detail_extracts_versions_and_history() -> None:
    settings = get_settings()
    api = GeorgiaApiClient(settings)
    api.close()

    detail_payload = {
        "id": 69285,
        "chamberType": 2,
        "documentType": 1,
        "number": 7,
        "title": "Budget changes",
        "status": "Senate Date Signed by Governor",
        "session": {"library": "20252026"},
        "sponsors": [{"name": "Sen. Example", "sequence": 1}],
        "statusHistory": [
            {"date": "2025-04-19T00:00:00", "name": "Senate Date Signed by Governor"},
            {"date": "2025-04-18T00:00:00", "name": "Act 132"},
        ],
        "versions": [
            {"id": 10, "versionNumber": 1},
            {"id": 11, "versionNumber": 2},
        ],
        "committees": [{"name": "Appropriations"}],
        "votes": [{"id": 1}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/authentication/token":
            return httpx.Response(200, text='"token"', request=request)
        if request.url.path == "/api/Legislation/detail/69285":
            return httpx.Response(200, json=detail_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.georgia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.legis.ga.gov/api/Legislation/detail/69285")
    finally:
        api.close()

    assert detail["bill"] == "SB7"
    assert detail["sponsor"] == "Sen. Example"
    assert detail["signedDate"] == "2025-04-19"
    assert detail["chapter"] == "132"
    assert detail["introduced"] == "https://www.legis.ga.gov/api/legislation/document/20252026/10"
    assert detail["currentVersionPath"] == "https://www.legis.ga.gov/api/legislation/document/20252026/11"
    assert "Appropriations" in detail["digestHTML"]
    assert len(detail["billActions"]) == 2

