from __future__ import annotations

import httpx

from app.settings import get_settings
from app.southcarolina_api import SouthCarolinaApiClient


def test_fetch_year_bills_collects_house_and_senate_introductions() -> None:
    settings = get_settings()
    api = SouthCarolinaApiClient(settings)
    api.close()

    house_index = """
    <html><body>
      <a href="/sess126_2025-2026/hintro26/20260113.htm">Tuesday, January 13, 2026</a>
    </body></html>
    """
    senate_index = """
    <html><body>
      <a href="/sess126_2025-2026/sintro26/20260203.htm">Tuesday, February 3, 2026</a>
    </body></html>
    """
    house_intro = """
    <html><body>
      <a href="/billsearch.php?billnumbers=4563&session=126&summary=B">H. 4563</a> (<a href="/sess126_2025-2026/bills/4563.docx">Word</a> version) -- Reps. Long and Oremus: A HOUSE RESOLUTION TO HONOR SOMETHING.
      <p>Next block</p>
    </body></html>
    """
    senate_intro = """
    <html><body>
      <a href="/billsearch.php?billnumbers=1106&session=126&summary=B">S. 1106</a> (<a href="/sess126_2025-2026/bills/1106.docx">Word</a> version) -- Senators Alexander and Rice: A BILL TO REQUIRE DISCLOSURES.
      <p>Next block</p>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sessphp/hintros.php"):
            return httpx.Response(200, text=house_index, request=request)
        if path.endswith("/sessphp/sintros.php"):
            return httpx.Response(200, text=senate_index, request=request)
        if path.endswith("/sess126_2025-2026/hintro26/20260113.htm"):
            return httpx.Response(200, text=house_intro, request=request)
        if path.endswith("/sess126_2025-2026/sintro26/20260203.htm"):
            return httpx.Response(200, text=senate_intro, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.south_carolina_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["H4563", "S1106"]
    assert items[0]["catchTitle"] == "A HOUSE RESOLUTION TO HONOR SOMETHING."
    assert items[1]["sponsor"] == "Senators Alexander and Rice"
    assert items[0]["detailPath"] == "https://www.scstatehouse.gov/sess126_2025-2026/bills/4563.htm"


def test_fetch_bill_detail_reads_status_cover_sheet() -> None:
    settings = get_settings()
    api = SouthCarolinaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <div class="statusCoverSheet WordSection1">
        <p style="text-align: center;"><span style="font-weight: bold;">South Carolina General Assembly</span><br>126th Session, 2025-2026</p>
        <p>Download <a href="4563.docx">This Bill</a> in Microsoft Word Format</p>
        <p style="font-weight: bold;">H. 4563</p>
        <p style="font-weight: bold;">STATUS INFORMATION</p>
        <p>House Resolution<br>Sponsors: Reps. Long, Oremus and White<br>Document Path: LC-0174AHB-AHB26.docx<br></p>
        <p>Introduced in the House on January 13, 2026<br>Currently residing in the House<br></p>
        Summary: House Rules, Standing Committee membership
        <p style="font-weight: bold;">HISTORY OF LEGISLATIVE ACTIONS</p>
        <table>
          <tr><th>Date</th><th>Body</th><th>Action Description with journal page number</th></tr>
          <tr><td>12/16/2025</td><td>House</td><td>Prefiled</td></tr>
          <tr><td>1/13/2026</td><td>House</td><td>Introduced (House Journal-page 23)</td></tr>
          <tr><td>1/20/2026</td><td>House</td><td>Member(s) request name added as sponsor: White</td></tr>
        </table>
      </div>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.south_carolina_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=detail_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.scstatehouse.gov/sess126_2025-2026/bills/4563.htm",
            {"billNum": "H4563", "billTitle": "House Rules, Standing Committee membership"},
        )
    finally:
        api.close()

    assert detail["bill"] == "H4563"
    assert detail["catchTitle"] == "House Rules, Standing Committee membership"
    assert detail["sponsor"] == "Reps. Long, Oremus and White"
    assert detail["lastAction"] == "Member(s) request name added as sponsor: White"
    assert detail["lastActionDate"] == "2026-01-20"
    assert detail["introduced"].endswith("/sess126_2025-2026/bills/4563.docx")
    assert detail["currentVersionPath"].endswith("/sess126_2025-2026/bills/4563.htm")
    assert len(detail["billActions"]) == 3
