from __future__ import annotations

import httpx

from app.michigan_api import MichiganApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_search_tables() -> None:
    settings = get_settings()
    api = MichiganApiClient(settings)
    api.close()

    house_html = """
    <html><body>
      <table>
        <tr><th>Document</th><th>Type</th><th>Description</th></tr>
        <tr>
          <td><a href="/Home/GetObject?objectName=2025-HB-4001&queryID=1">HB 4001 of 2025</a></td>
          <td>House Bill</td>
          <td>Labor: hours and wages; modify.<br/>Last Action: REFERRED TO COMMITTEE ON REGULATORY AFFAIRS</td>
        </tr>
        <tr>
          <td><a href="/Home/GetObject?objectName=2025-HB-4002&queryID=1">HB 4002 of 2025<br/>(PA 2 of 2025)</a></td>
          <td>House Bill</td>
          <td>Benefits; revise requirements.<br/>Last Action: assigned PA 2'25 with immediate effect</td>
        </tr>
      </table>
    </body></html>
    """
    senate_html = """
    <html><body>
      <table>
        <tr><th>Document</th><th>Type</th><th>Description</th></tr>
        <tr>
          <td><a href="/Home/GetObject?objectName=2025-SB-0001&queryID=1">SB 1 of 2025</a></td>
          <td>Senate Bill</td>
          <td>Education; update standards.<br/>Last Action: REFERRED TO COMMITTEE ON EDUCATION</td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Search/ExecuteSearch"):
            doc_type = request.url.params.get("docTypes")
            if doc_type == "House Bill":
                return httpx.Response(200, text=house_html, request=request)
            if doc_type == "Senate Bill":
                return httpx.Response(200, text=senate_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.michigan_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        bills = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["HB4001", "HB4002", "SB1"]
    assert bills[0]["detailPath"] == "https://www.legislature.mi.gov/Bills/Bill?ObjectName=2025-HB-4001"
    assert bills[1]["chapter"] == "PA 2 of 2025"
    assert bills[2]["lastAction"] == "REFERRED TO COMMITTEE ON EDUCATION"


def test_fetch_bill_detail_parses_documents_history_and_amendments() -> None:
    settings = get_settings()
    api = MichiganApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <main>
        <h1>House Bill 4001 of 2025</h1>
        <h2>Sponsors</h2>
        <div>John Roth (District 104) Jay DeBoyer (District 63)</div>
        <h2>Categories</h2>
        <div>Labor: hours and wages</div>
        <div>Labor: hours and wages; minimum hourly wage rate; modify.</div>
        <h2>Documents</h2>
        <div>Bill Document Formatting Information</div>
        <div class="billDocuments">
          <div class="billDocRow">
            <div class="pdf"><a href="/documents/2025-2026/billintroduced/House/pdf/2025-HIB-4001.pdf">PDF</a></div>
            <div class="text"><strong>House Introduced Bill</strong></div>
          </div>
          <div class="billDocRow">
            <div class="pdf"><a href="/documents/2025-2026/billengrossed/House/pdf/2025-HEBH-4001.pdf">PDF</a></div>
            <div class="text"><strong>As Passed by the House</strong></div>
          </div>
        </div>
        <h2>Analysis</h2>
        <div><a href="/documents/2025-2026/billanalysis/House/pdf/2025-HLA-4001-ABC123.pdf">Summary as Introduced</a></div>
        <h2>History</h2>
        <div id="History">
          <table>
            <tr><th>Date</th><th>Journal</th><th>Action</th></tr>
            <tr><td>1/16/2025</td><td>HJ 5 Pg. 43</td><td><a href="/Home/GetObject?objectName=2025-HCVBH-4001-0N295.pdf">reported with recommendation with substitute (H-2)</a></td></tr>
            <tr><td>2/04/2025</td><td>SJ 10 Pg. 85</td><td>REFERRED TO COMMITTEE ON REGULATORY AFFAIRS</td></tr>
          </table>
        </div>
      </main>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.michigan_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=detail_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.legislature.mi.gov/Bills/Bill?ObjectName=2025-HB-4001",
            {"billNum": "HB4001", "catchTitle": "Labor: hours and wages; minimum hourly wage rate; modify."},
        )
    finally:
        api.close()

    assert detail["bill"] == "HB4001"
    assert detail["sponsor"] == "John Roth (District 104), Jay DeBoyer (District 63)"
    assert detail["catchTitle"] == "Labor: hours and wages; minimum hourly wage rate; modify."
    assert detail["currentVersionPath"].endswith("/documents/2025-2026/billengrossed/House/pdf/2025-HEBH-4001.pdf")
    assert detail["introduced"].endswith("/documents/2025-2026/billintroduced/House/pdf/2025-HIB-4001.pdf")
    assert detail["digest"].endswith("/documents/2025-2026/billanalysis/House/pdf/2025-HLA-4001-ABC123.pdf")
    assert detail["lastAction"] == "REFERRED TO COMMITTEE ON REGULATORY AFFAIRS"
    assert detail["lastActionDate"] == "2025-02-04"
    assert [item["amendmentNumber"] for item in detail["amendments"]] == ["H-2"]
