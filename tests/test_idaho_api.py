from __future__ import annotations

import httpx

from app.idaho_api import IdahoApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_official_legislation_index() -> None:
    settings = get_settings()
    api = IdahoApiClient(settings)
    api.close()

    listing_html = """
    <html><body>
      <h3>HOUSE BILLS</h3>
      <table class="mini-data-table">
        <tr id="billH0489">
          <td><a href="/sessioninfo/2026/legislation/H0489">H0489</a></td>
          <td>Mask, disguise prohibited</td>
          <td></td>
          <td>H Jud</td>
        </tr>
      </table>
      <h3>SENATE BILLS</h3>
      <table class="mini-data-table">
        <tr id="billS1001">
          <td><a href="/sessioninfo/2026/legislation/S1001">S1001</a></td>
          <td>Public records update</td>
          <td></td>
          <td>LAW +</td>
        </tr>
      </table>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.idaho_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=listing_html, request=request)),
    )

    try:
        bills = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["H0489", "S1001"]
    assert bills[0]["detailPath"] == "https://legislature.idaho.gov/sessioninfo/2026/legislation/H0489"
    assert bills[1]["billStatus"] == "LAW +"


def test_fetch_bill_detail_parses_actions_and_status_fields() -> None:
    settings = get_settings()
    api = IdahoApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <table class="bill-table">
        <tr>
          <td>H0491</td>
          <td></td>
          <td>by JUDICIARY, RULES AND ADMINISTRATION COMMITTEE</td>
        </tr>
      </table>
      <table class="bill-table">
        <tr><td>EMERGENCY FIRST AID - Revises immunity rules for first aid.</td></tr>
      </table>
      <table class="bill-table">
        <tr><td></td><td>03/24</td><td>Returned from Senate Passed; to JRA for Enrolling</td></tr>
        <tr><td></td><td></td><td>Signed by President; returned to House</td></tr>
        <tr><td></td><td>03/26</td><td>Delivered to Governor at 4:39 p.m. on March 25, 2026</td></tr>
        <tr><td></td><td>03/30</td><td>Reported Signed by Governor on March 26, 2026 Session Law Chapter 162 Effective: 07/01/2026</td></tr>
      </table>
      <a href="/wp-content/uploads/sessioninfo/2026/legislation/H0491.pdf">Bill Text</a>
      <a href="/wp-content/uploads/sessioninfo/2026/legislation/H0491SOP.pdf">Statement of Purpose / Fiscal Note</a>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.idaho_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=detail_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://legislature.idaho.gov/sessioninfo/2026/legislation/H0491/",
            {"billNum": "H0491", "billStatus": "LAW +", "sessionYear": 2026},
        )
    finally:
        api.close()

    assert detail["bill"] == "H0491"
    assert detail["sponsor"] == "JUDICIARY, RULES AND ADMINISTRATION COMMITTEE"
    assert detail["catchTitle"] == "EMERGENCY FIRST AID - Revises immunity rules for first aid."
    assert detail["lastAction"].startswith("Reported Signed by Governor")
    assert detail["lastActionDate"] == "2026-03-30"
    assert detail["signedDate"] == "2026-03-26"
    assert detail["effectiveDate"] == "2026-07-01"
    assert detail["chapter"] == "Chapter 162"
    assert detail["introduced"].endswith("/wp-content/uploads/sessioninfo/2026/legislation/H0491.pdf")
    assert detail["digest"].endswith("/wp-content/uploads/sessioninfo/2026/legislation/H0491SOP.pdf")
