from __future__ import annotations

import httpx

from app.settings import get_settings
from app.westvirginia_api import WestVirginiaApiClient


def test_fetch_year_bills_reads_official_all_bills_page() -> None:
    settings = get_settings()
    api = WestVirginiaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><body>
              <table>
                <tr><td><a href="Bills_history.cfm?input=1&year=2026&sessiontype=RS&btype=bill">SB 1</a></td><td>First bill</td><td>Signed</td><td>Effective from passage - (February 16, 2026)</td></tr>
                <tr><td><a href="Bills_history.cfm?input=2&year=2026&sessiontype=RS&btype=bill">HB 2</a></td><td>Second bill</td><td>Pending</td><td>01/14/26</td></tr>
              </table>
            </body></html>
            """,
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.west_virginia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["SB1", "HB2"]
    assert items[1]["lastActionDate"] == "2026-01-14"


def test_fetch_bill_detail_reads_versions_actions_and_amendments() -> None:
    settings = get_settings()
    api = WestVirginiaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><body>
              <h3>Senate Bill 1</h3>
              <table class="bstat">
                <tr><td><strong>LAST ACTION:</strong></td><td>Effective from passage - (February 16, 2026)</td></tr>
                <tr><td><strong>SUMMARY:</strong></td><td>Small Business Growth Act</td></tr>
                <tr><td><strong>LEAD SPONSOR:</strong></td><td>Smith</td></tr>
                <tr><td><strong>SPONSORS:</strong></td><td>Queen, Taylor</td></tr>
                <tr>
                  <td><strong>BILL TEXT:</strong></td>
                  <td>
                    Enrolled Committee Substitute -
                    <a href="bills_text.cfm?billdoc=sb1%20sub1%20enr.htm&yr=2026&sesstype=RS&i=1">html</a> |
                    <a href="/Bill_Text_HTML/2026_SESSIONS/RS/bills/sb1 sub1 enr.pdf">pdf</a><br>
                    Introduced Version -
                    <a href="bills_text.cfm?billdoc=sb1%20intr.htm&yr=2026&sesstype=RS&i=1">html</a> |
                    <a href="/Bill_Text_HTML/2026_SESSIONS/RS/bills/sb1 intr.pdf">pdf</a>
                  </td>
                </tr>
                <tr>
                  <td><strong>FLOOR AMENDMENTS:</strong></td>
                  <td><a href="/legisdocs/chamber/2026/RS/floor_amends/sb1 hfa sample.htm">sb1 hfa sample.htm</a></td>
                </tr>
                <tr><td><strong>SUBJECT(S):</strong></td><td>Economic Development</td></tr>
              </table>
              <table id="action-table">
                <tr><th></th><th>Description</th><th>Date</th><th>Journal Page</th></tr>
                <tr><td>S</td><td>Approved by Governor 2/23/2026</td><td>02/23/26</td><td>19</td></tr>
              </table>
            </body></html>
            """,
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.west_virginia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("/Bill_Status/Bills_history.cfm?input=1&year=2026&sessiontype=RS&btype=bill")
    finally:
        api.close()

    assert detail["bill"] == "SB1"
    assert detail["currentVersionPath"] == "https://www.wvlegislature.gov/Bill_Status/bills_text.cfm?billdoc=sb1%20sub1%20enr.htm&yr=2026&sesstype=RS&i=1"
    assert detail["introduced"] == "https://www.wvlegislature.gov/Bill_Status/bills_text.cfm?billdoc=sb1%20intr.htm&yr=2026&sesstype=RS&i=1"
    assert detail["lastActionDate"] == "2026-02-23"
    assert len(detail["amendments"]) == 1
