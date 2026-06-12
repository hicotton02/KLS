from __future__ import annotations

import httpx

from app.newmexico_api import NewMexicoApiClient
from app.settings import get_settings


def test_fetch_year_bills_uses_official_session_code() -> None:
    settings = get_settings()
    api = NewMexicoApiClient(settings)
    api.close()

    sessions_html = """
    <html><body>
      <select>
        <option value="70">2025 Regular</option>
        <option value="72">2026 Regular</option>
      </select>
    </body></html>
    """
    list_html = """
    <html><body>
      <table>
        <tr>
          <th>Bill ID</th><th>Title</th><th>Sponsor</th><th>Actions</th><th>Session</th>
        </tr>
        <tr>
          <td><a href="/Legislation/Legislation?chamber=H&amp;legType=B&amp;legNo=1&amp;year=26">HB 1</a></td>
          <td>Budget update</td>
          <td>Rep. Chavez</td>
          <td>Passed House</td>
          <td>2026 Regular</td>
        </tr>
        <tr>
          <td><a href="/Legislation/Legislation?chamber=S&amp;legType=JM&amp;legNo=2&amp;year=26">SJM 2</a></td>
          <td>Water study memorial</td>
          <td>Sen. Lopez</td>
          <td>Introduced</td>
          <td>2026 Regular</td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Legislation/Legislation_List") and "Session" not in request.url.params:
            return httpx.Response(200, text=sessions_html, request=request)
        if request.url.path.endswith("/Legislation/Legislation_List") and request.url.params.get("Session") == "72":
            return httpx.Response(200, text=list_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.new_mexico_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SJM2"]
    assert items[0]["lastAction"] == "Passed House"
    assert items[1]["detailPath"] == "https://www.nmlegis.gov/Legislation/Legislation?chamber=S&legType=JM&legNo=2&year=26"


def test_fetch_bill_detail_extracts_links_actions_and_sponsors() -> None:
    settings = get_settings()
    api = NewMexicoApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <table id="MainContent_formViewLegislation">
        <tr><td>Title</td><td>Clean air update</td></tr>
        <tr><td>Current Location</td><td>Signed by Governor</td></tr>
      </table>
      <div id="MainContent_panelLegislationInformation">
        <a href="/Sessions/26%20Regular/bills/house/HB0001.HTML">Introduced (HTML)</a>
        <a href="/Sessions/26%20Regular/bills/house/HB0001.pdf">Introduced (PDF)</a>
        <a href="/Sessions/26%20Regular/bills/house/HB0001final.pdf">Final Version</a>
      </div>
      <a id="MainContent_tabContainerLegislation_tabPanelSponsors_dataListSponsors_linkSponsor_0">Rep. Chavez</a>
      <a href="/Sessions/26%20Regular/firs/HB0001.PDF">Fiscal Impact Report</a>
      <table id="MainContent_tabContainerLegislation_tabPanelActions_formViewActionText">
        <tr><td>ActionText: Signed by Governor - Ch. 1 - Jan. 27 Key to Abbreviations</td></tr>
      </table>
      <table id="MainContent_tabContainerLegislation_tabPanelActions_dataListActions">
        <tr><td><span class="list-group-item">Legislative Day: 1 Calendar Day: 01/20/2026 Introduced</span></td></tr>
        <tr><td><span class="list-group-item">Legislative Day: 7 Calendar Day: 01/27/2026 Signed by Governor - Ch. 1 - Jan. 27</span></td></tr>
      </table>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.new_mexico_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=detail_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.nmlegis.gov/Legislation/Legislation?chamber=H&legType=B&legNo=1&year=26"
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1"
    assert detail["catchTitle"] == "Clean air update"
    assert detail["sponsor"] == "Rep. Chavez"
    assert detail["billStatus"] == "Signed by Governor"
    assert detail["lastAction"] == "Signed by Governor - Ch. 1 - Jan. 27"
    assert detail["lastActionDate"] == "2026-01-27"
    assert detail["signedDate"] == "2026-01-27"
    assert detail["chapter"] == "1"
    assert detail["introduced"] == "https://www.nmlegis.gov/Sessions/26%20Regular/bills/house/HB0001.HTML"
    assert detail["digest"] == "https://www.nmlegis.gov/Sessions/26%20Regular/firs/HB0001.PDF"
    assert detail["currentVersionPath"] == "https://www.nmlegis.gov/Sessions/26%20Regular/bills/house/HB0001final.pdf"
    assert len(detail["billActions"]) == 2
