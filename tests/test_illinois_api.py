from __future__ import annotations

import httpx

from app.illinois_api import IllinoisApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_range_pages() -> None:
    settings = get_settings()
    api = IllinoisApiClient(settings)
    api.close()

    legislation_html = """
    <html><body>
      <a href="/Legislation/RegularSession/HB?num1=0001&amp;num2=0100&amp;DocTypeID=HB&amp;GaId=18&amp;SessionId=114">HB 1-100</a>
      <a href="/Legislation/RegularSession/SB?num1=0001&amp;num2=0100&amp;DocTypeID=SB&amp;GaId=18&amp;SessionId=114">SB 1-100</a>
    </body></html>
    """
    house_page = """
    <html><body>
      <table class="table table-striped border">
        <tr>
          <td><a href="/Legislation/BillStatus?DocNum=1&amp;GAID=18&amp;DocTypeID=HB&amp;LegId=157001&amp;SessionID=114">HB0001</a></td>
          <td><a href="/Legislation/BillStatus?DocNum=1&amp;GAID=18&amp;DocTypeID=HB&amp;LegId=157001&amp;SessionID=114">State budget</a></td>
        </tr>
      </table>
    </body></html>
    """
    senate_page = """
    <html><body>
      <table class="table table-striped border">
        <tr>
          <td><a href="/Legislation/BillStatus?DocNum=2&amp;GAID=18&amp;DocTypeID=SB&amp;LegId=157002&amp;SessionID=114">SB0002</a></td>
          <td><a href="/Legislation/BillStatus?DocNum=2&amp;GAID=18&amp;DocTypeID=SB&amp;LegId=157002&amp;SessionID=114">Clean water</a></td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/legislation":
            return httpx.Response(200, text=legislation_html, request=request)
        if request.url.path == "/Legislation/RegularSession/HB":
            return httpx.Response(200, text=house_page, request=request)
        if request.url.path == "/Legislation/RegularSession/SB":
            return httpx.Response(200, text=senate_page, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.illinois_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB0001", "SB0002"]
    assert items[0]["billTitle"] == "State budget"
    assert items[1]["detailPath"] == "https://www.ilga.gov/Legislation/BillStatus?DocNum=2&GAID=18&DocTypeID=SB&LegId=157002&SessionID=114"


def test_fetch_bill_detail_extracts_synopsis_actions_and_amendments() -> None:
    settings = get_settings()
    api = IllinoisApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h2>Bill Status of HB1607</h2>
      <div class="row p-3"><div class="col-sm">ELIMINATE FOOD DESERTS</div></div>
      <div class="row p-3">
        <div class="col-sm">Last Action 12/12/2025 - House: Public Act . . . . . . . . . 104-0447</div>
      </div>
      <div class="row p-3">
        <div class="col-sm">
          <div id="sponsorDiv">
            <h5>House Sponsors</h5>
            <a class="notranslate" href="/House/Members/Details/3314">Sonya M. Harper</a>
            <a class="notranslate" href="/House/Members/Details/3374">Edgar Gonzalez</a>
            <h5>Senate Sponsors</h5>
            <a class="notranslate" href="/Senate/Members/Details/3269">Mattie Hunter</a>
          </div>
        </div>
      </div>
      <div class="row p-3">
        <div class="col-sm">
          <h5>Statutes Amended In Order of Appearance</h5>
          <div class="row ml-4 mb-1 p-1"><div class="col-sm">New Act</div></div>
          <h5>Synopsis As Introduced</h5>
          <div class="list-group"><span class="list-group-item">Creates a food access commission.</span></div>
          <span class="content fw-bold pb-3 mb-3 border-bottom">
            <a href="/legislation/billstatus/fulltext?LegDocId=203091&amp;DocName=10400HB1607ham001&amp;GA=104&amp;LegID=157766&amp;SessionId=114&amp;SpecSess=00&amp;DocTypeId=HB&amp;DocNum=1607&amp;GAID=18&amp;Session=">House Committee Amendment No. 1</a>
          </span>
          <div class="list-group"><span class="list-group-item">Adds a labor representative.</span></div>
        </div>
      </div>
      <table class="table table-striped border text-start">
        <tr><th>Date</th><th>Chamber</th><th>Action</th></tr>
        <tr><td>1/23/2025</td><td>House</td><td>Filed with the Clerk</td></tr>
        <tr><td>12/12/2025</td><td>House</td><td>Effective Date June 1, 2026</td></tr>
      </table>
      <a href="/Legislation/BillStatus/FullText?GAID=18&amp;DocNum=1607&amp;DocTypeID=HB&amp;LegId=157766&amp;SessionID=114">Full Text</a>
    </body></html>
    """
    full_text_html = """
    <html><body>
      <a href="/Legislation/BillStatus/FullText?LegDocId=197556&amp;DocName=10400HB1607&amp;DocNum=1607&amp;DocTypeID=HB&amp;LegID=157766&amp;GAID=18&amp;SessionID=114&amp;SpecSess=&amp;Session=">Introduced</a>
      <a href="/Legislation/BillStatus/FullText?LegDocId=197556&amp;DocName=10400HB1607enr&amp;DocNum=1607&amp;DocTypeID=HB&amp;LegID=157766&amp;GAID=18&amp;SessionID=114&amp;SpecSess=&amp;Session=">Enrolled</a>
      <a href="../../documents/legislation/104/HB/PDF/10400HB1607lv.pdf">Open PDF</a>
      <div>AN ACT concerning food access.</div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Legislation/BillStatus":
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path.lower() == "/legislation/billstatus/fulltext":
            return httpx.Response(200, text=full_text_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.illinois_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.ilga.gov/Legislation/BillStatus?DocNum=1607&GAID=18&DocTypeID=HB&LegId=157766&SessionID=114"
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1607"
    assert detail["catchTitle"] == "ELIMINATE FOOD DESERTS"
    assert detail["sponsor"] == "Sonya M. Harper"
    assert detail["billStatus"] == "Public Act . . . . . . . . . 104-0447"
    assert detail["lastActionDate"] == "2025-12-12"
    assert detail["signedDate"] == "2025-12-12"
    assert detail["effectiveDate"] == "2026-06-01"
    assert detail["chapter"] == "104-0447"
    assert detail["introduced"] == "https://www.ilga.gov/Legislation/BillStatus/FullText?LegDocId=197556&DocName=10400HB1607&DocNum=1607&DocTypeID=HB&LegID=157766&GAID=18&SessionID=114&SpecSess=&Session="
    assert detail["currentVersionPath"] == "https://www.ilga.gov/Legislation/BillStatus/FullText?LegDocId=197556&DocName=10400HB1607enr&DocNum=1607&DocTypeID=HB&LegID=157766&GAID=18&SessionID=114&SpecSess=&Session="
    assert detail["digest"] == "https://www.ilga.gov/documents/legislation/104/HB/PDF/10400HB1607lv.pdf"
    assert detail["summaryHTML"] == "<p>Creates a food access commission.</p>"
    assert "House Committee Amendment No. 1" in detail["digestHTML"]
    assert len(detail["billActions"]) == 2
    assert detail["amendments"][0]["documentUrl"].startswith("https://www.ilga.gov/legislation/billstatus/fulltext")
