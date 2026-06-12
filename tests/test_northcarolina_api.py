from __future__ import annotations

import httpx

from app.northcarolina_api import NorthCarolinaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_north_carolina_bill_list() -> None:
    settings = get_settings()
    api = NorthCarolinaApiClient(settings)
    api.site_client.close()
    api.webservices_client.close()

    list_payload = [
        {"chamber": "H", "billNumber": 1},
        {"chamber": "S", "billNumber": 50},
        {"chamber": "H", "billNumber": 1},
    ]

    def webservices_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/AllBills/2025":
            return httpx.Response(200, json=list_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.webservices_client = httpx.Client(
        base_url=settings.north_carolina_webservices_base,
        follow_redirects=True,
        transport=httpx.MockTransport(webservices_handler),
    )
    api.site_client = httpx.Client(base_url=settings.north_carolina_site_base, follow_redirects=True)

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["H1", "S50"]
    assert items[0]["detailPath"] == "https://www.ncleg.gov/BillLookUp/2025/H1"
    assert items[1]["summaryPath"] == "https://www.ncleg.gov/Legislation/Bills/Summaries/2025/S50"


def test_fetch_bill_detail_extracts_north_carolina_bill_metadata() -> None:
    settings = get_settings()
    api = NorthCarolinaApiClient(settings)
    api.site_client.close()
    api.webservices_client.close()

    detail_html = """
    <html><body><main>
      <div class="row"><div class="col-6 col-sm-3 text-right order-sm-3"><a href="/BillLookUp/2025/H318">H318</a></div><div class="col-12 col-sm-6 h2 text-center order-sm-2">House Bill 318</div></div>
      <div class="row"><div class="col-12"><a href="/BillLookUp/2025/H318">The Criminal Illegal Alien Enforcement Act.</a></div><div class="col-12 titleSub">2025-2026 Session</div></div>
      <div class="card">
        <div class="card-header text-center"><a href="https://webservices.ncleg.gov/BillDigests/2025/H318">View Bill Digest</a></div>
      </div>
      <div class="card">
        <div class="card-header text-center"><div class="row"><div class="col-12">View Available Bill Summaries</div></div></div>
        <div class="card-body">
          <div class="row"><div class="col-6"><a href="/Sessions/2025/Bills/House/PDF/H318v1.pdf">Edition 1</a></div></div>
          <div class="row"><div class="col-6"><a href="/Sessions/2025/Bills/House/PDF/H318v4.pdf">Edition 4</a></div></div>
        </div>
      </div>
      <div class="col-4 col-sm-3 col-xl-2 text-right pad-row misc-info-label">Last Action:</div>
      <div class="col-8 col-sm-9 col-xl-10 text-left pad-row">Ratified the bill on 6/24/2025</div>
      <div class="col-4 col-sm-3 col-xl-2 text-right pad-row misc-info-label">Sponsors:</div>
      <div class="col-8 col-sm-9 col-xl-10 text-left pad-row"><a href="/Members/Biography/H/1">Stevens</a> <a href="/Members/Biography/H/2">Lopez</a></div>
      <div class="card">
        <div class="card-header text-center">History</div>
        <div class="card-body">
          <div class="row avoid-break-inside">
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Date:</div>
            <div class="col-7 col-md-2 pr-0">6/24/2025</div>
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Chamber:</div>
            <div class="col-7 col-md-1 col-lg-2 pr-0 text-nowrap">House</div>
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Action:</div>
            <div class="col-7 col-md-4 col-lg-3 pr-0">Ratified</div>
          </div>
          <div class="row avoid-break-inside">
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Date:</div>
            <div class="col-7 col-md-2 pr-0">6/4/2025</div>
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Chamber:</div>
            <div class="col-7 col-md-1 col-lg-2 pr-0 text-nowrap">Senate</div>
            <div class="col-5 d-md-none text-right font-weight-bold pr-0">Action:</div>
            <div class="col-7 col-md-4 col-lg-3 pr-0">Passed Senate</div>
          </div>
        </div>
      </div>
    </main></body></html>
    """
    summary_html = """
    <html><body><main>
      <table>
        <tr><th>Summary</th><th>SortKey</th><th>Description</th><th>Last Updated</th></tr>
        <tr>
          <td class="text-nowrap"><a href="/Legislation/Bills/Summaries/2025/H318-SMCE-63">H318-SMCE-63</a></td>
          <td>1</td>
          <td>Committee substitute adds a new reporting rule.</td>
          <td>06/04/2025</td>
        </tr>
        <tr>
          <td class="text-nowrap"><a href="/Legislation/Bills/Summaries/2025/H318-SMCE-84">H318-SMCE-84</a></td>
          <td>2</td>
          <td>Ratified summary reflects the enacted bill.</td>
          <td>06/24/2025</td>
        </tr>
      </table>
    </main></body></html>
    """
    digest_html = """
    <html><body>
      <div class="view-content">
        <div class="item-list">
          <ul>
            <li class="views-row"><p>AN ACT TO MODIFY ELIGIBILITY FOR RELEASE. SL 2025-85. Enacted July 29, 2025. Effective October 1, 2025.</p></li>
            <li class="views-row"><p>Senate committee substitute makes the following changes.</p></li>
          </ul>
        </div>
      </div>
    </body></html>
    """

    def site_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/BillLookUp/2025/H318":
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path == "/Legislation/Bills/Summaries/2025/H318":
            return httpx.Response(200, text=summary_html, request=request)
        raise AssertionError(f"Unexpected site request: {request.method} {request.url}")

    def webservices_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/BillDigests/2025/H318":
            return httpx.Response(200, text=digest_html, request=request)
        raise AssertionError(f"Unexpected webservices request: {request.method} {request.url}")

    api.site_client = httpx.Client(
        base_url=settings.north_carolina_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(site_handler),
    )
    api.webservices_client = httpx.Client(
        base_url=settings.north_carolina_webservices_base,
        follow_redirects=True,
        transport=httpx.MockTransport(webservices_handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.ncleg.gov/BillLookUp/2025/H318",
            {
                "billNum": "H318",
                "summaryPath": "https://www.ncleg.gov/Legislation/Bills/Summaries/2025/H318",
                "digestPath": "https://webservices.ncleg.gov/BillDigests/2025/H318",
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "H318"
    assert detail["catchTitle"] == "The Criminal Illegal Alien Enforcement Act."
    assert detail["sponsor"] == "Stevens, Lopez"
    assert detail["lastAction"] == "Ratified"
    assert detail["lastActionDate"] == "2025-06-24"
    assert detail["chapter"] == "85"
    assert detail["signedDate"] == "2025-07-29"
    assert detail["effectiveDate"] == "2025-10-01"
    assert detail["introduced"].endswith("/H318v1.pdf")
    assert detail["currentVersionPath"].endswith("/H318v4.pdf")
    assert len(detail["amendments"]) == 2
    assert "Ratified summary reflects the enacted bill." in detail["summaryHTML"]

