from __future__ import annotations

import httpx

from app.california_api import CaliforniaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_california_search_results() -> None:
    settings = get_settings()
    api = CaliforniaApiClient(settings)
    api.close()

    list_html = """
    <html><body>
      <table class="bill_results">
        <tbody>
          <tr>
            <td><a href="/faces/billNavClient.xhtml?bill_id=202520260AB1">AB-1</a></td>
            <td>Wildfire insurance</td>
            <td>Connolly</td>
            <td>Chaptered</td>
          </tr>
          <tr>
            <td><a href="/faces/billNavClient.xhtml?bill_id=202520260SB2">SB-2</a></td>
            <td>Water storage</td>
            <td>Allen</td>
            <td>In Senate</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/faces/billSearchClient.xhtml"
        assert request.url.params["session_year"] == "20252026"
        return httpx.Response(200, text=list_html, request=request)

    api.client = httpx.Client(
        base_url=settings.california_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["AB-1", "SB-2"]
    assert items[0]["billTitle"] == "Wildfire insurance"
    assert items[1]["detailPath"] == "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260SB2"


def test_fetch_bill_detail_extracts_history_versions_and_digest() -> None:
    settings = get_settings()
    api = CaliforniaApiClient(settings)
    api.close()

    status_html = """
    <html><body>
      <div class="statusRow">
        <div class="statusCell"><label>Lead Authors:</label></div>
        <div class="statusCellData"><span>Connolly (A)</span></div>
      </div>
      <div class="statusRow">
        <div class="statusCell"><label>House Location:</label></div>
        <div class="statusCellData"><span>Chaptered</span></div>
      </div>
    </body></html>
    """
    history_html = """
    <html><body>
      <table>
        <tr><th>Date</th><th>Action</th></tr>
        <tr><td>10/09/25</td><td>Chaptered by Secretary of State - Chapter 472, Statutes of 2025.</td></tr>
        <tr><td>10/09/25</td><td>Approved by the Governor.</td></tr>
        <tr><td>09/23/25</td><td>Enrolled and presented to the Governor at 4 p.m.</td></tr>
      </table>
    </body></html>
    """
    text_html = """
    <html><body>
      <h2>AB-1 Residential property insurance: wildfire risk. (2025-2026)</h2>
      <select id="billVersion">
        <option value="billNumber">Bill Number</option>
        <option value="20250AB197CHP">10/09/25 - Chaptered</option>
        <option value="20250AB198ENR">09/15/25 - Enrolled</option>
        <option value="20250AB199INT">12/02/24 - Introduced</option>
      </select>
      <div id="bill_all">
        <h3>LEGISLATIVE COUNSEL'S DIGEST</h3>
        <p>Existing law regulates wildfire risk mitigation.</p>
        <p>This bill requires updated hardening standards.</p>
        <h3>Digest Key</h3>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/faces/billStatusClient.xhtml":
            return httpx.Response(200, text=status_html, request=request)
        if request.url.path == "/faces/billHistoryClient.xhtml":
            return httpx.Response(200, text=history_html, request=request)
        if request.url.path == "/faces/billTextClient.xhtml":
            return httpx.Response(200, text=text_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.california_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202520260AB1"
        )
    finally:
        api.close()

    assert detail["bill"] == "AB-1"
    assert detail["sponsor"] == "Connolly"
    assert detail["billStatus"] == "Chaptered by Secretary of State - Chapter 472, Statutes of 2025."
    assert detail["lastAction"] == "Chaptered by Secretary of State - Chapter 472, Statutes of 2025."
    assert detail["lastActionDate"] == "2025-10-09"
    assert detail["signedDate"] == "2025-10-09"
    assert detail["chapter"] == "472"
    assert detail["currentVersionPath"] == "https://leginfo.legislature.ca.gov/faces/billPdf.xhtml?bill_id=202520260AB1&version=20250AB197CHP"
    assert detail["introduced"] == "https://leginfo.legislature.ca.gov/faces/billPdf.xhtml?bill_id=202520260AB1&version=20250AB199INT"
    assert "updated hardening standards" in detail["summaryHTML"]
