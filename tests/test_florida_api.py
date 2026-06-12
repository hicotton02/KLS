from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx

from app.florida_api import FloridaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_official_ranges_and_both_chambers() -> None:
    settings = get_settings()
    api = FloridaApiClient(settings)
    api.close()

    landing_html = """
    <html><body>
      <form id="BillTextSearch">
        <select id="bill-range" name="BillRange">
          <option value=""></option>
          <option value="2-98">2-98</option>
          <option value="100-198">100-198</option>
        </select>
      </form>
    </body></html>
    """
    senate_page = """
    <html><body>
      <table class="tbl width100">
        <tbody>
          <tr>
            <th scope="row"><a href="/Session/Bill/2026/2">SB 2</a></th>
            <td>Relief for a state employee.</td>
            <td>Jones</td>
            <td>Last Action: 3/13/2026 S Died in Appropriations</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """
    house_page = """
    <html><body>
      <table class="tbl width100">
        <tbody>
          <tr>
            <th scope="row"><a href="/Session/Bill/2026/105">HB 105</a></th>
            <td>Public records revisions.</td>
            <td>Perez</td>
            <td>Last Action: 2/2/2026 H Filed</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = parse_qs(urlparse(str(request.url)).query)
        if path.endswith("/Session/Bills/2026") and not query:
            return httpx.Response(200, text=landing_html, request=request)
        if path.endswith("/Session/Bills/2026"):
            chamber = query.get("chamber", [""])[0]
            bill_range = query.get("billRange", [""])[0]
            if chamber == "Senate" and bill_range == "2-98":
                return httpx.Response(200, text=senate_page, request=request)
            if chamber == "House" and bill_range == "100-198":
                return httpx.Response(200, text=house_page, request=request)
            return httpx.Response(200, text="<html><body><table class='tbl'></table></body></html>", request=request)
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.florida_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB0105", "SB0002"]
    assert items[0]["sponsor"] == "Perez"
    assert items[1]["lastAction"] == "Died in Appropriations"
    assert items[1]["lastActionDate"] == "2026-03-13"
    assert items[1]["detailPath"].endswith("/Session/Bill/2026/2")


def test_fetch_bill_detail_reads_versions_history_and_amendments() -> None:
    settings = get_settings()
    api = FloridaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h2>SB 118: Land Use and Development Regulations</h2>
      <p>GENERAL BILL <span>by</span> <a href="/Senators/S10">Rodriguez</a></p>
      <p class="width80">
        <span>Land Use and Development Regulations;</span>
        Revising local zoning rules and recreational vehicle park standards.
      </p>
      <div id="snapshot" class="grid-100 top">
        <div class="grid-60 top pad-left0">
          <span class="bold">Effective Date: </span><span>4/21/2026 <br></span>
          <span class="bold">Last Action:</span>
          4/21/2026
          -
          Approved by Governor<br>
          <span class="bold">Bill Text:</span>
          <a href="/Session/Bill/2026/118/BillText/er/HTML">Web Page</a> |
          <a href="/Session/Bill/2026/118/BillText/er/PDF">PDF</a>
          <br>
        </div>
      </div>
      <div class="tabbody" id="tabBodyBillHistory">
        <h4>Bill History</h4>
        <table class="tbl width100">
          <tbody>
            <tr><td class="centertext">10/7/2025</td><td class="centertext">Senate</td><td>Filed</td></tr>
            <tr><td class="centertext">4/21/2026</td><td class="centertext">Senate</td><td>Approved by Governor</td></tr>
          </tbody>
        </table>
      </div>
      <div class="tabbody" id="tabBodyBillText">
        <h4 id="BillText">Bill Text</h4>
        <table class="tbl">
          <tbody>
            <tr>
              <td>S 118 Filed</td>
              <td>10/7/2025 12:01 PM</td>
              <td><a href="/Session/Bill/2026/118/BillText/Filed/HTML">Web Page</a> | <a href="/Session/Bill/2026/118/BillText/Filed/PDF">PDF</a></td>
            </tr>
            <tr>
              <td>S 118 er</td>
              <td>3/13/2026 1:03 PM</td>
              <td><a href="/Session/Bill/2026/118/BillText/er/HTML">Web Page</a> | <a href="/Session/Bill/2026/118/BillText/er/PDF">PDF</a></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="tabbody" id="tabBodyAmendments">
        <div id="CommitteeAmendment">
          <h4 id="Amendments">Committee Amendments</h4>
          <table class="tbl width100 caption">
            <caption>S 118 Filed</caption>
            <tbody>
              <tr>
                <td>829438 - Amendment <br><span>Delete line 24 and insert:</span></td>
                <td>Finance and Tax <br>(Arrington)</td>
                <td>1/26/2026 <br><span>11:03 AM</span></td>
                <td>Replaced by Committee Substitute <br>1/28/2026</td>
                <td><a href="/Session/Bill/2026/118/Amendment/829438/HTML">Web Page</a><br><a href="/Session/Bill/2026/118/Amendment/829438/PDF">PDF</a></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="tabbody" id="tabBodyAnalyses">
        <h4 id="Analysis">Bill Analyses</h4>
        <table class="tbl width100">
          <tbody>
            <tr>
              <td>Committee</td>
              <td>S 118</td>
              <td>Community Affairs</td>
              <td>1/10/2026 9:00 AM</td>
              <td><a href="/Session/Bill/2026/118/Analyses/2026s00118.ca.PDF">PDF</a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </body></html>
    """
    current_version_html = """
    <html><body>
      <main>
        <p>A bill to revise local zoning rules and recreational vehicle park standards.</p>
      </main>
    </body></html>
    """
    amendment_html = """
    <html><body>
      <main>
        <p>Delete line 24 and insert updated zoning language.</p>
      </main>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Session/Bill/2026/118"):
            return httpx.Response(200, text=detail_html, request=request)
        if path.endswith("/Session/Bill/2026/118/BillText/er/HTML"):
            return httpx.Response(200, text=current_version_html, request=request)
        if path.endswith("/Session/Bill/2026/118/Amendment/829438/HTML"):
            return httpx.Response(200, text=amendment_html, request=request)
        return httpx.Response(200, text="<html><body></body></html>", request=request)

    api.client = httpx.Client(
        base_url=settings.florida_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.flsenate.gov/Session/Bill/2026/118")
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
        amendment_text = api.fetch_public_document_text(detail["amendments"][0]["documentUrl"])
    finally:
        api.close()

    assert detail["bill"] == "SB0118"
    assert detail["billTitle"] == "Land Use and Development Regulations"
    assert detail["sponsor"] == "Rodriguez"
    assert detail["lastAction"] == "Approved by Governor"
    assert detail["lastActionDate"] == "2026-04-21"
    assert detail["signedDate"] == "2026-04-21"
    assert detail["effectiveDate"] == "2026-04-21"
    assert detail["introduced"].endswith("/Session/Bill/2026/118/BillText/Filed/HTML")
    assert detail["currentVersionPath"].endswith("/Session/Bill/2026/118/BillText/er/HTML")
    assert detail["digest"].endswith("/Session/Bill/2026/118/Analyses/2026s00118.ca.PDF")
    assert detail["enrolledNumber"] == "S 118 er"
    assert detail["amendments"][0]["amendmentNumber"] == "829438"
    assert detail["amendments"][0]["status"].startswith("Replaced by Committee Substitute")
    assert "revise local zoning rules" in bill_text
    assert "updated zoning language" in amendment_text
