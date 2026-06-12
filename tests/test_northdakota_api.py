from __future__ import annotations

import httpx

from app.northdakota_api import NorthDakotaApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_measure_directory_cards() -> None:
    settings = get_settings()
    api = NorthDakotaApiClient(settings)
    api.close()

    listing_html = """
    <html><body>
      <ul class="list-group list-group-flush">
        <a class="bold" href="/assembly/69-2025/regular/bill-overview/bo1001.html">HB 1001</a>
        <li class="list-group-item" title="INTRODUCED">
          <a href="../documents/25-0145-01000.odt">25.0145.01000 <span>I</span></a>
        </li>
        <li class="list-group-item" title="Prepared by the Legislative Council staff for House Appropriations">
          <a href="../documents/25-0145-01001m.odt">25.0145.01001 <span>A</span></a>
        </li>
        <li class="list-group-item" title="FIRST ENGROSSMENT">
          <a href="../documents/25-0145-02000.odt">25.0145.02000 <span>E</span></a>
        </li>
      </ul>
      <ul class="list-group list-group-flush">
        <a class="bold" href="/assembly/69-2025/regular/bill-overview/bo2001.html">SB 2001</a>
        <li class="list-group-item" title="INTRODUCED">
          <a href="../documents/25-0168-01000.odt">25.0168.01000 <span>I</span></a>
        </li>
      </ul>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.north_dakota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=listing_html, request=request)),
    )

    try:
        bills = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["HB1001", "SB2001"]
    assert bills[0]["detailPath"] == "https://ndlegis.gov/assembly/69-2025/regular/bill-overview/bo1001.html"
    assert bills[0]["currentVersionPath"].endswith("/documents/25-0145-02000.odt")
    assert len(bills[0]["versionEntries"]) == 3


def test_fetch_bill_detail_parses_overview_actions_and_amendments() -> None:
    settings = get_settings()
    api = NorthDakotaApiClient(settings)
    api.close()

    overview_html = """
    <html><body>
      <h1>HB 1001 - Overview</h1>
      <div class="tab-content">
        <h5>Sponsors</h5>
        <p>Introduced by House Appropriations</p>
        <h5>Title</h5>
        <p class="show-more">AN ACT to provide an appropriation for defraying expenses.</p>
        <h5>Measure Status</h5>
        <div class="line_box">
          <div class="text_circle passed">First Reading House 01/07</div>
          <div class="text_circle passed">Governor Signed on 04/11</div>
        </div>
        <h5>Legislative History</h5>
        <a href="/files/resource/69-2025/library/hb1001.pdf">View History</a>
      </div>
      <a href="../bill-actions/ba1001.html">Actions</a>
    </body></html>
    """
    actions_html = """
    <html><body>
      <table><tr><th>A</th></tr></table>
      <table>
        <tr><th>Date</th><th>Chamber</th><th>Description</th><th>Version</th></tr>
        <tr><td>01/07</td><td>House</td><td>Introduced, first reading, referred to Appropriations</td><td>25.0145.01000</td></tr>
        <tr><td>04/11</td><td>House</td><td>Signed by Governor</td><td>25.0145.04000</td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/assembly/69-2025/regular/bill-overview/bo1001.html"):
            return httpx.Response(200, text=overview_html, request=request)
        if request.url.path.endswith("/assembly/69-2025/regular/bill-actions/ba1001.html"):
            return httpx.Response(200, text=actions_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.north_dakota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://ndlegis.gov/assembly/69-2025/regular/bill-overview/bo1001.html",
            {
                "billNum": "HB1001",
                "sourceYear": 2025,
                "currentVersionFingerprint": "25.0145.01000|25.0145.01001|25.0145.02000",
                "versionEntries": [
                    {"versionCode": "25.0145.01000", "kind": "I", "documentUrl": "https://ndlegis.gov/documents/25-0145-01000.odt"},
                    {"versionCode": "25.0145.01001", "kind": "A", "documentUrl": "https://ndlegis.gov/documents/25-0145-01001m.odt", "title": "Committee amendment"},
                    {"versionCode": "25.0145.02000", "kind": "E", "documentUrl": "https://ndlegis.gov/documents/25-0145-02000.odt"},
                ],
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1001"
    assert detail["sponsor"] == "House Appropriations"
    assert detail["billTitle"] == "AN ACT to provide an appropriation for defraying expenses."
    assert detail["lastAction"] == "Signed by Governor"
    assert detail["lastActionDate"] == "2025-04-11"
    assert detail["signedDate"] == "2025-04-11"
    assert detail["introduced"].endswith("/documents/25-0145-01000.odt")
    assert detail["digest"].endswith("/files/resource/69-2025/library/hb1001.pdf")
    assert detail["currentVersionPath"].endswith("/documents/25-0145-02000.odt")
    assert [item["amendmentNumber"] for item in detail["amendments"]] == ["25.0145.01001"]
