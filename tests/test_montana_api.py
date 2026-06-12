from __future__ import annotations

import json

import httpx

from app.montana_api import MontanaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_montana_search_api() -> None:
    settings = get_settings()
    api = MontanaApiClient(settings)
    api.close()

    sessions_payload = [
        {
            "id": 2,
            "ordinals": "20251",
            "type": "REGULAR",
            "legislature": {"ordinals": "69"},
        }
    ]
    search_payload = {
        "totalElements": 2,
        "content": [
            {
                "id": 707,
                "billNumber": 3,
                "versionNumber": 1,
                "sessionLawChapterNumber": None,
                "billType": {"code": "HB"},
                "draft": {
                    "draftNumber": "LC0707",
                    "shortTitle": "Create a sample program",
                    "billStatuses": [
                        {"timeStamp": "2025-01-05T08:00:00", "billStatusCode": {"name": "Introduced"}},
                    ],
                },
            },
            {
                "id": 708,
                "billNumber": 2,
                "versionNumber": 2,
                "sessionLawChapterNumber": 776,
                "billType": {"code": "HB"},
                "draft": {
                    "draftNumber": "LC0708",
                    "shortTitle": "General appropriations",
                    "billStatuses": [
                        {"timeStamp": "2025-04-20T10:00:00", "billStatusCode": {"name": "Governor signed"}},
                    ],
                },
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/legislators/v1/sessions":
            return httpx.Response(200, json=sessions_payload, request=request)
        if request.url.path == "/bills/v1/bills/search":
            return httpx.Response(200, json=search_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.montana_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB2", "HB3"]
    assert items[0]["billTitle"] == "General appropriations"
    assert items[0]["detailPath"] == "https://bearbeta.legmt.gov/bills/v1/bills/findBySessionIdAndDraftNumber?sessionId=2&draftNumber=LC0708"


def test_fetch_bill_detail_extracts_versions_and_amendments() -> None:
    settings = get_settings()
    api = MontanaApiClient(settings)
    api.close()

    detail_payload = {
        "id": 708,
        "billNumber": 2,
        "sponsorId": 119,
        "sessionId": 2,
        "versionNumber": 4,
        "sessionLawChapterNumber": 776,
        "sessionLawChapter": {"number": 776, "assignedDate": "2025-04-22"},
        "billType": {"code": "HB"},
        "draft": {
            "shortTitle": "General appropriations",
            "billStatuses": [
                {"timeStamp": "2025-01-05T08:00:00", "billStatusCode": {"name": "Introduced", "chamber": "HOUSE"}},
                {"timeStamp": "2025-04-22T09:30:00", "billStatusCode": {"name": "Governor signed", "chamber": "HOUSE"}},
            ],
        },
    }
    versions_payload = [
        {
            "id": 101,
            "date": "2025-01-06 09:00:00 -0600",
            "creation": "2025-01-06 09:00:00 -0600",
            "fileName": "HB0002.001.001.pdf",
            "attributes": [{"name": "DocumentLink", "stringValue": "https://docs.legmt.gov/introduced.pdf"}],
        },
        {
            "id": 102,
            "date": "2025-04-22 09:35:00 -0600",
            "creation": "2025-04-22 09:35:00 -0600",
            "fileName": "HB0002.004.001.pdf",
            "attributes": [{"name": "DocumentLink", "stringValue": "https://docs.legmt.gov/current.pdf"}],
        },
    ]
    amendments_payload = [
        {
            "number": 2,
            "type": "FLOOR",
            "billVersion": 4,
            "bill": {"billType": {"chamber": "HOUSE"}},
        }
    ]
    amendment_docs_payload = [
        {
            "id": 201,
            "date": "2025-03-28 15:44:02 -0600",
            "creation": "2025-03-28 15:44:02 -0600",
            "fileName": "HB0002.002.004.pdf",
            "attributes": [{"name": "DocumentLink", "stringValue": "https://docs.legmt.gov/amendment-2.pdf"}],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/bills/v1/bills/findBySessionIdAndDraftNumber":
            return httpx.Response(200, json=detail_payload, request=request)
        if request.url.path == "/legislators/v1/sessions/2":
            return httpx.Response(200, json={"id": 2, "ordinals": "20251", "legislature": {"ordinals": "69"}}, request=request)
        if request.url.path == "/legislators/v1/legislators/119":
            return httpx.Response(200, json={"firstName": "Llew", "lastName": "Jones"}, request=request)
        if request.url.path == "/docs/v1/documents/getBillVersions":
            return httpx.Response(200, json=versions_payload, request=request)
        if request.url.path == "/bills/v1/amendments/findByBillId":
            return httpx.Response(200, json=amendments_payload, request=request)
        if request.url.path == "/docs/v1/documents/getBillAmendments":
            return httpx.Response(200, json=amendment_docs_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.montana_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://bearbeta.legmt.gov/bills/v1/bills/findBySessionIdAndDraftNumber?sessionId=2&draftNumber=LC0708"
        )
    finally:
        api.close()

    assert detail["bill"] == "HB2"
    assert detail["sponsor"] == "Llew Jones"
    assert detail["chapter"] == "776"
    assert detail["signedDate"] == "2025-04-22"
    assert detail["currentVersionPath"] == "https://docs.legmt.gov/current.pdf"
    assert detail["introduced"] == "https://docs.legmt.gov/introduced.pdf"
    assert detail["amendments"][0]["amendmentNumber"] == "Amendment 2"
    assert detail["amendments"][0]["documentUrl"] == "https://docs.legmt.gov/amendment-2.pdf"
