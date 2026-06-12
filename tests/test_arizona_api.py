from __future__ import annotations

import httpx

from app.arizona_api import ArizonaApiClient
from app.settings import get_settings


def test_fetch_year_bills_filters_to_hb_and_sb() -> None:
    settings = get_settings()
    api = ArizonaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/Session/":
            return httpx.Response(
                200,
                json=[{"SessionId": 130, "Code": "2R", "Name": "2026 - Fifty-seventh Legislature - Second Regular Session", "Legislature": "57"}],
                request=request,
            )
        if request.url.path == "/api/BillStatus/":
            return httpx.Response(
                200,
                json=[
                    {"BillNumber": "HB2001", "ShortTitle": "First bill", "Description": "HB2001 - First bill", "PrimarySponsorName": "Alice", "Chapter": None},
                    {"BillNumber": "SCR1001", "ShortTitle": "Skip me", "Description": "SCR1001 - Skip me", "PrimarySponsorName": "Bob", "Chapter": None},
                    {"BillNumber": "SB1002", "ShortTitle": "Second bill", "Description": "SB1002 - Second bill", "PrimarySponsorName": "Carol", "Chapter": "7"},
                ],
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.url}")

    api.client = httpx.Client(
        base_url=settings.arizona_api_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB2001", "SB1002"]
    assert items[1]["billStatus"] == "Chapter 7"


def test_amendment_documents_include_status_in_label() -> None:
    settings = get_settings()
    api = ArizonaApiClient(settings)

    amendments = api._amendment_documents(
        [
            {
                "DocumentGroupCode": "AdoptedAmendments",
                "Documents": [{"DocumentName": "SENATE - Education", "PdfPath": "/BillStatus/GetDocumentPdf/1"}],
            },
            {
                "DocumentGroupCode": "ProposedAmendments",
                "Documents": [{"DocumentName": "SENATE - Education", "PdfPath": "/BillStatus/GetDocumentPdf/2"}],
            },
        ]
    )
    api.close()

    assert [item["amendmentNumber"] for item in amendments] == [
        "SENATE - Education (Adopted)",
        "SENATE - Education (Proposed)",
    ]


def test_amendment_documents_skip_duplicate_labels_with_same_status() -> None:
    settings = get_settings()
    api = ArizonaApiClient(settings)

    amendments = api._amendment_documents(
        [
            {
                "DocumentGroupCode": "AdoptedAmendments",
                "Documents": [
                    {"DocumentName": "SENATE - Education", "PdfPath": "/BillStatus/GetDocumentPdf/1"},
                    {"DocumentName": "SENATE - Education", "PdfPath": "/BillStatus/GetDocumentPdf/2"},
                ],
            }
        ]
    )
    api.close()

    assert [item["amendmentNumber"] for item in amendments] == ["SENATE - Education (Adopted)"]
