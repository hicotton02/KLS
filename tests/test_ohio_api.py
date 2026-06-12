from __future__ import annotations

import httpx

from app.ohio_api import OhioApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_ohio_legislation_list() -> None:
    settings = get_settings()
    api = OhioApiClient(settings)
    api.close()

    list_payload = [
        {
            "number": "hb1",
            "short_title": "School funding",
            "name": "HB 1",
            "version": "As Introduced",
            "download_html": "/documents/hb1.html",
            "sponsors": [{"full_name": "Rep. Alpha"}],
        },
        {
            "number": "sb2",
            "short_title": "Tax update",
            "name": "SB 2",
            "version": "Passed Senate",
            "governor_signed_date": "2026-03-01",
            "download": "/documents/sb2.pdf",
            "sponsors": [{"full_name": "Sen. Beta"}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/general_assembly_136/legislation/":
            return httpx.Response(200, json=list_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.ohio_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SB2"]
    assert items[0]["sponsor"] == "Rep. Alpha"
    assert items[1]["signedDate"] == "2026-03-01"
    assert items[1]["detailPath"] == "https://search-prod.lis.state.oh.us/api/v2/general_assembly_136/legislation/sb2/"


def test_fetch_bill_detail_extracts_versions_and_amendments() -> None:
    settings = get_settings()
    api = OhioApiClient(settings)
    api.close()

    versions_payload = [
        {
            "number": "hb20",
            "name": "HB 20",
            "short_title": "Tax update",
            "long_title": "To change the tax code.",
            "version": "As Introduced",
            "download_html": "/documents/intro.html",
            "subjects": ["Taxes"],
            "sponsors": [{"full_name": "Rep. Alpha"}],
            "amendments": "/api/v2/general_assembly_136/legislation/hb20/amendments/",
            "chamber": "House",
        },
        {
            "number": "hb20",
            "name": "HB 20",
            "short_title": "Tax update",
            "long_title": "To change the tax code and reporting rules.",
            "version": "Passed House",
            "download": "/documents/current.pdf",
            "subjects": ["Taxes", "Reporting"],
            "sponsors": [{"full_name": "Rep. Alpha"}],
            "amendments": "/api/v2/general_assembly_136/legislation/hb20/amendments/",
            "chamber": "House",
            "effective_date": "2026-06-01",
        },
    ]
    amendments_payload = [
        {
            "amendment_number": "A0001",
            "version": "Committee amendment",
            "html_link": "/documents/amendment-a0001.html",
            "sponsors": [{"full_name": "Rep. Alpha"}],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/general_assembly_136/legislation/hb20/":
            return httpx.Response(200, json=versions_payload, request=request)
        if request.url.path == "/api/v2/general_assembly_136/legislation/hb20/amendments/":
            return httpx.Response(200, json=amendments_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.ohio_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://search-prod.lis.state.oh.us/api/v2/general_assembly_136/legislation/hb20/")
    finally:
        api.close()

    assert detail["bill"] == "HB20"
    assert detail["sponsor"] == "Rep. Alpha"
    assert detail["effectiveDate"] == "2026-06-01"
    assert detail["introduced"] == "https://search-prod.lis.state.oh.us/documents/intro.html"
    assert detail["currentVersionPath"] == "https://search-prod.lis.state.oh.us/documents/current.pdf"
    assert detail["officialPage"] == "https://www.legislature.ohio.gov/legislation/136/hb20"
    assert detail["amendments"][0]["amendmentNumber"] == "A0001"
    assert detail["amendments"][0]["documentUrl"] == "https://search-prod.lis.state.oh.us/documents/amendment-a0001.html"

