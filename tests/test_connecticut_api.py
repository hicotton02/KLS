from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from app.connecticut_api import ConnecticutApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_house_and_senate_ranges() -> None:
    settings = get_settings()
    api = ConnecticutApiClient(settings)
    api.close()

    house_results = """
    <html><body>
      <table>
        <tbody>
          <tr>
            <td><a href='/asp/cgabillstatus/cgabillstatus.asp?selBillType=Bill&bill_num=HB05001&which_year=2026'>HB05001</a></td>
            <td>AN ACT CONCERNING ELECTION ADMINISTRATION.&nbsp</td>
            <td>&nbsp&nbsp&nbsp</td>
          </tr>
          <tr>
            <td><a href='/asp/cgabillstatus/cgabillstatus.asp?selBillType=Bill&bill_num=HB05002&which_year=2026'>HB05002</a></td>
            <td>AN ACT CONCERNING SCHOOL FUNDING.&nbsp</td>
            <td>&nbsp&nbsp&nbsp</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """
    senate_results = """
    <html><body>
      <table>
        <tbody>
          <tr>
            <td><a href='/asp/cgabillstatus/cgabillstatus.asp?selBillType=Bill&bill_num=SB00001&which_year=2026'>SB00001</a></td>
            <td>AN ACT CONCERNING AFFORDABILITY.&nbsp</td>
            <td>&nbsp&nbsp&nbsp</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/asp/CGABillInfo/CGABillInfoDisplay.asp"):
            body = parse_qs(request.content.decode("utf-8"))
            low = body.get("txtLowBill", [""])[0]
            if low == "5001":
                return httpx.Response(200, text=house_results, request=request)
            if low == "1":
                return httpx.Response(200, text=senate_results, request=request)
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.connecticut_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB05001", "HB05002", "SB00001"]
    assert items[0]["catchTitle"] == "AN ACT CONCERNING ELECTION ADMINISTRATION."
    assert items[2]["detailPath"].endswith("bill_num=SB00001&which_year=2026")


def test_fetch_bill_detail_reads_status_page_and_bill_history() -> None:
    settings = get_settings()
    api = ConnecticutApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h3 class="text-center"><strong>Substitute for Raised H.B. No. 5001</strong><br>Session Year 2026</h3>
      <div class="large-12 columns">
        <h4>AN ACT CONCERNING ELECTION ADMINISTRATION.</h4>
        <p class="text-justify">This bill changes absentee voting and election procedures.</p>
        <h5>Introduced by:</h5>
        Government Administration and Elections Committee
      </div>
      <table summary="Status of bills">
        <thead><tr><td>&nbsp;</td><td>&nbsp;&nbsp;Text of Bill</td></tr></thead>
        <tbody>
          <tr><td bgcolor="#6699CC">&nbsp;</td><td><a href="/docs/current.html">APP Joint Favorable</a></td></tr>
          <tr><td bgcolor="black">&nbsp;</td><td><a href="/docs/file-copy.pdf">File No. 528</a></td></tr>
          <tr><td bgcolor="black">&nbsp;</td><td><a href="/docs/introduced.html">Raised Bill</a></td></tr>
        </tbody>
      </table>
      <table summary="Status of bills">
        <thead><tr><td>&nbsp;</td><td>&nbsp;&nbsp;Committee Actions</td></tr></thead>
        <tbody>
          <tr><td bgcolor="black">&nbsp;</td><td><a href="/docs/committee-amendment.html">GAE Vote Tally Sheet-A (Amendment #7: Language on File in GAE Committee)</a></td></tr>
        </tbody>
      </table>
      <table summary="Status of bills">
        <thead><tr><td>&nbsp;</td><td>&nbsp;&nbsp;Fiscal Notes</td></tr></thead>
        <tbody>
          <tr><td bgcolor="black">&nbsp;</td><td><a href="/docs/fiscal-note.pdf">Fiscal Note For File Copy 528</a></td></tr>
        </tbody>
      </table>
      <table summary="Status of bills">
        <thead><tr><td>&nbsp;</td><td>&nbsp;&nbsp;Bill Analyses</td></tr></thead>
        <tbody>
          <tr><td bgcolor="black">&nbsp;</td><td><a href="/docs/bill-analysis.pdf">Bill Analysis For File Copy 528</a></td></tr>
        </tbody>
      </table>
      <div class="row" style="margin: 0 1px">
        <h4 style="padding-left:10px">Bill History</h4>
        <div class="large-12 columns">
          <table summary="Bill history" class="footable table">
            <thead>
              <tr><th>&nbsp;</th><th>Date</th><th>&nbsp;</th><th>Action Taken</th></tr>
            </thead>
            <tbody>
              <tr><td width="1%" bgcolor="#6699CC">&nbsp;</td><td width="10%" data-value="1776816000">4/22/2026</td><td width="8%"></td><td>Signed by Governor; Public Act No. 26-3</td></tr>
              <tr><td width="1%" bgcolor="black">&nbsp;</td><td width="10%" data-value="1776643200">4/20/2026</td><td width="8%">(LCO)</td><td>Filed with Legislative Commissioners' Office</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </body></html>
    """
    current_version_html = """
    <html><body>
      <main>
        <p>Section 1. This bill changes absentee voting and election procedures.</p>
      </main>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/asp/cgabillstatus/cgabillstatus.asp"):
            return httpx.Response(200, text=detail_html, request=request)
        if path.endswith("/docs/current.html"):
            return httpx.Response(200, text=current_version_html, request=request)
        if path.endswith("/docs/committee-amendment.html"):
            return httpx.Response(200, text="<html><body><p>Amendment text.</p></body></html>", request=request)
        return httpx.Response(200, text="<html><body></body></html>", request=request)

    api.client = httpx.Client(
        base_url=settings.connecticut_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.cga.ct.gov/asp/cgabillstatus/cgabillstatus.asp?selBillType=Bill&bill_num=HB05001&which_year=2026"
        )
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
    finally:
        api.close()

    assert detail["bill"] == "HB05001"
    assert detail["billTitle"] == "AN ACT CONCERNING ELECTION ADMINISTRATION."
    assert detail["sponsor"] == "Government Administration and Elections Committee"
    assert detail["lastAction"] == "Signed by Governor; Public Act No. 26-3"
    assert detail["lastActionDate"] == "2026-04-22"
    assert detail["signedDate"] == "2026-04-22"
    assert detail["chapter"] == "26-3"
    assert detail["introduced"].endswith("/docs/introduced.html")
    assert detail["currentVersionPath"].endswith("/docs/current.html")
    assert detail["digest"].endswith("/docs/bill-analysis.pdf")
    assert detail["enrolledNumber"] == "APP Joint Favorable"
    assert detail["amendments"][0]["amendmentNumber"] == "Committee Amendment #7"
    assert "Section 1. This bill changes absentee voting and election procedures." in bill_text
