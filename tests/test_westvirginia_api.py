from __future__ import annotations

import httpx
import pytest

from app.settings import get_settings
from app.westvirginia_api import WestVirginiaApiClient


def test_detail_retries_temporary_page_without_bill_identity(monkeypatch) -> None:
    api = WestVirginiaApiClient(get_settings())
    api.client.close()
    calls = []
    sleeps = []
    monkeypatch.setattr("app.westvirginia_api.time.sleep", sleeps.append)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        page = "<h1>Temporarily unavailable</h1>" if len(calls) == 1 else "<h3>Senate Bill 991</h3>"
        return httpx.Response(200, text=page)

    api.client = httpx.Client(base_url=api.settings.west_virginia_site_base, transport=httpx.MockTransport(handler))
    try:
        detail = api.fetch_bill_detail("/Bill_Status/Bills_history.cfm?input=991&year=2026&sessiontype=RS&btype=bill")
    finally:
        api.close()
    assert detail["bill"] == "SB991"
    assert len(calls) == 2
    assert sleeps == [1]
    assert calls[0].url == calls[1].url


def test_detail_still_rejects_persistently_unreadable_source(monkeypatch) -> None:
    api = WestVirginiaApiClient(get_settings())
    api.client.close()
    calls = []
    monkeypatch.setattr("app.westvirginia_api.time.sleep", lambda seconds: None)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<h1>Service unavailable</h1>")

    api.client = httpx.Client(base_url=api.settings.west_virginia_site_base, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="bill number could not be parsed"):
            api.fetch_bill_detail("/Bill_Status/Bills_history.cfm?input=991&year=2026")
    finally:
        api.close()
    assert len(calls) == 2


@pytest.mark.parametrize("status", [403, 404])
def test_detail_does_not_retry_access_denied_or_missing_bill(status) -> None:
    api = WestVirginiaApiClient(get_settings())
    api.client.close()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    api.client = httpx.Client(base_url=api.settings.west_virginia_site_base, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(httpx.HTTPStatusError):
            api.fetch_bill_detail("/Bill_Status/Bills_history.cfm?input=991&year=2026")
    finally:
        api.close()
    assert len(calls) == 1


def test_detail_retries_temporary_http_failure() -> None:
    api = WestVirginiaApiClient(get_settings())
    api.client.close()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, text="<h3>Senate Bill 991</h3>")

    api.client = httpx.Client(base_url=api.settings.west_virginia_site_base, transport=httpx.MockTransport(handler))
    try:
        assert api.fetch_bill_detail("/Bill_Status/Bills_history.cfm?input=991&year=2026")["bill"] == "SB991"
    finally:
        api.close()
    assert len(calls) == 2


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
                <tr><td><a href="Bills_history.cfm?input=3&year=2026&sessiontype=RS&btype=bill">Incorporated into Com. Sub. for SB 251</a></td><td>Status note</td><td></td><td></td></tr>
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
