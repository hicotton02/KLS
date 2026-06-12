from __future__ import annotations

import httpx

from app.settings import get_settings
from app.utah_api import UtahApiClient


def test_fetch_year_bills_parses_official_bill_list() -> None:
    settings = get_settings()
    api = UtahApiClient(settings)
    api.close()

    list_html = """
    <html><body>
      <ul>
        <li><a href="https://le.utah.gov/~2026/bills/static/HB0001.html" class="billlink">H.B. 1</a> -- <b>Education budget</b> <i>(Rep. Whyte, S.)</i></li>
        <li><a href="https://le.utah.gov/~2026/bills/static/SB0002.html" class="billlink">S.B. 2</a> -- <b>Justice budget</b> <i>(Sen. Millner, A.)</i></li>
      </ul>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.utah_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=list_html, request=request)),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB0001", "SB0002"]
    assert items[0]["catchTitle"] == "Education budget"
    assert items[1]["sponsor"] == "(Sen. Millner, A.)"


def test_fetch_bill_detail_reads_json_payload_and_bill_text() -> None:
    settings = get_settings()
    api = UtahApiClient(settings)
    api.close()

    payload = {
        "sessionID": "2026GS",
        "billNumber": "HB0005",
        "shortTitle": "Natural resources budget",
        "generalProvisions": "This bill sets the natural resources budget.",
        "highlightedProvisions": "This bill:<hr><ltbullet>funds wildlife programs;<hr><ltbullet>sets reporting duties.",
        "primeSponsorName": "Rep. Barlow, S.",
        "primeSponsorHouse": "H",
        "lastAction": "Governor Signed",
        "lastActionDate": "1/31/2026",
        "actionHistoryList": [
            {"owner": "House", "actionDate": "2026-01-20 09:00:00.000", "description": "Introduced"},
            {"owner": "Governor", "actionDate": "2026-01-31 15:00:00.000", "description": "Governor Signed"},
        ],
        "billVersionList": [
            {
                "activeVersion": True,
                "billDocs": [
                    {"fileType": "Introduced", "shortDesc": "Introduced", "url": "/Session/2026/bills/introduced/HB0005.xml", "fileDate": "2026-01-20 09:00:00.000"},
                    {"fileType": "PubSub", "shortDesc": "Substitute #1", "url": "/Session/2026/bills/introduced/HB0005S01.xml", "fileDate": "2026-01-25 09:00:00.000"},
                    {"fileType": "Enrolled", "shortDesc": "Enrolled", "url": "/Session/2026/bills/enrolled/HB0005.xml", "fileDate": "2026-01-30 16:00:00.000"},
                    {"fileType": "PubFN", "shortDesc": "Fiscal Note", "url": "https://pf.utleg.gov/public-web/sessions/2026GS/fiscal-notes/HB0005.fn.html", "fileDate": "2026-01-21 09:00:00.000"},
                ],
            }
        ],
    }
    xml_text = "<bill><title>A bill for an act relating to natural resources.</title><section>Section 1. Budget language.</section></bill>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/data/2026GS/HB0005.json"):
            return httpx.Response(200, json=payload, request=request)
        if request.url.path.endswith("/Session/2026/bills/enrolled/HB0005.xml"):
            return httpx.Response(200, text=xml_text, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.utah_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://le.utah.gov/~2026/bills/static/HB0005.html")
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
    finally:
        api.close()

    assert detail["bill"] == "HB0005"
    assert detail["catchTitle"] == "Natural resources budget"
    assert detail["sponsor"] == "Rep. Barlow, S."
    assert detail["lastAction"] == "Governor Signed"
    assert detail["lastActionDate"] == "2026-01-31"
    assert detail["signedDate"] == "2026-01-31"
    assert detail["introduced"].endswith("/Session/2026/bills/introduced/HB0005.xml")
    assert detail["currentVersionPath"].endswith("/Session/2026/bills/enrolled/HB0005.xml")
    assert detail["digest"].endswith("/fiscal-notes/HB0005.fn.html")
    assert "Budget language." in bill_text
