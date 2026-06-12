from __future__ import annotations

import httpx

from app.missouri_api import MissouriApiClient
from app.settings import get_settings


def test_fetch_year_bills_combines_house_and_senate_for_requested_year() -> None:
    settings = get_settings()
    api = MissouriApiClient(settings)
    api.close()

    house_listing = """
    <html><body>
      <table id="reportgrid">
        <tr class="reportbillinfo">
          <td><a href="Bill.aspx?bill=HB1&amp;year=2026&amp;code=R ">HB1</a></td>
          <td><a href="/member/1">Smith</a></td>
          <td></td>
          <td>HB</td>
          <td>Hearing scheduled</td>
        </tr>
        <tr class="reportlongtitle"><td></td><td colspan="4">First house bill</td></tr>
        <tr class="reportbillinfo">
          <td><a href="Bill.aspx?bill=HB99&amp;year=2025&amp;code=R ">HB99</a></td>
          <td><a href="/member/2">Jones</a></td>
          <td></td>
          <td>HB</td>
          <td>Old session</td>
        </tr>
        <tr class="reportlongtitle"><td></td><td colspan="4">Should be skipped</td></tr>
        <tr class="reportbillinfo">
          <td><a href="Bill.aspx?bill=HJR2&amp;year=2026&amp;code=R ">HJR2</a></td>
          <td><a href="/member/3">Taylor</a></td>
          <td></td>
          <td>HJR</td>
          <td>In committee</td>
        </tr>
        <tr class="reportlongtitle"><td></td><td colspan="4">Joint resolution title</td></tr>
        <tr class="reportbillinfo">
          <td><a href="Bill.aspx?bill=HC1&amp;year=2026&amp;code=R ">HC1</a></td>
          <td><a href="/member/4">Parker</a></td>
          <td></td>
          <td>HC</td>
          <td>Referred</td>
        </tr>
        <tr class="reportlongtitle"><td></td><td colspan="4">House concurrent resolution title</td></tr>
      </table>
    </body></html>
    """

    senate_listing = """
    <html><body>
      <div class="card">
        <div class="card__body">
          <a href="https://www.senate.mo.gov/BillTracking/Bills/BillInformation?year=2026&amp;billid=7" class="fs-6">SB 5</a>
          <div class="bill-title">Senate bill title</div>
          <div><strong>Sponsor:</strong> <a href="/member/7">Brown</a></div>
        </div>
      </div>
      <div class="card">
        <div class="card__body">
          <a href="https://www.senate.mo.gov/BillTracking/Bills/BillInformation?year=2025&amp;billid=8" class="fs-6">SB 9</a>
          <div class="bill-title">Old senate bill</div>
          <div><strong>Sponsor:</strong> <a href="/member/8">Green</a></div>
        </div>
      </div>
    </body></html>
    """

    api.house_client = httpx.Client(
        base_url=settings.missouri_house_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=house_listing, request=request)),
    )
    api.senate_client = httpx.Client(
        base_url=settings.missouri_senate_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=senate_listing, request=request)),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "HC1", "HJR2", "SB5"]
    assert items[0]["catchTitle"] == "First house bill"
    assert items[0]["billStatus"] == "Hearing scheduled"
    assert items[0]["detailPath"].endswith("BillContentMobile.aspx?bill=HB1&code=R+&year=2026")
    assert items[1]["catchTitle"] == "House concurrent resolution title"
    assert items[3]["sponsor"] == "Brown"


def test_fetch_house_year_bills_raises_when_requested_year_is_missing() -> None:
    settings = get_settings()
    api = MissouriApiClient(settings)
    api.close()

    house_listing = """
    <html><body>
      <table id="reportgrid">
        <tr class="reportbillinfo">
          <td><a href="Bill.aspx?bill=HB1&amp;year=2025&amp;code=R ">HB1</a></td>
          <td><a href="/member/1">Smith</a></td>
          <td></td>
          <td>HB</td>
          <td>Old session</td>
        </tr>
        <tr class="reportlongtitle"><td></td><td colspan="4">Old bill</td></tr>
      </table>
    </body></html>
    """

    api.house_client = httpx.Client(
        base_url=settings.missouri_house_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=house_listing, request=request)),
    )

    try:
        try:
            api._fetch_house_year_bills(2026)
        except ValueError as exc:
            assert "requested year 2026" in str(exc)
        else:
            raise AssertionError("Expected Missouri House year mismatch to raise")
    finally:
        api.close()


def test_fetch_house_bill_detail_extracts_versions_actions_and_amendments() -> None:
    settings = get_settings()
    api = MissouriApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h1>HB 10</h1>
      <div class="BillDescription">Changes farm air permits.</div>
      <table>
        <tr><th>Sponsor:</th><td>Smith</td></tr>
        <tr><th>Proposed Effective Date:</th><td>August 28, 2026</td></tr>
        <tr><th>LR Number:</th><td>1234H.01I</td></tr>
        <tr><th>Last Action:</th><td>Approved by Governor, Chapter 12</td></tr>
        <tr><th>Bill String:</th><td>HB 10</td></tr>
      </table>
    </body></html>
    """

    document_html = """
    <html><body>
      <div class="DocHeaderRow"><h2>Bill Text</h2></div>
      <div class="DocRow"><div class="DocInfoCell">
        <div class="textLR">1234H.01I</div>
        <div class="textType">Introduced</div>
        <a href="https://documents.house.mo.gov/intro.pdf">Introduced PDF</a>
      </div></div>
      <div class="DocRow"><div class="DocInfoCell">
        <div class="textLR">1234H.02P</div>
        <div class="textType">Perfected</div>
        <a href="https://documents.house.mo.gov/perfected.pdf">Perfected PDF</a>
      </div></div>

      <div class="DocHeaderRow"><h2>Bill Summary</h2></div>
      <div class="DocRow"><div class="DocInfoCell">
        <div class="textLR">HB10P</div>
        <div class="textType">Summary</div>
        <a href="https://documents.house.mo.gov/summary.pdf">Summary PDF</a>
      </div></div>

      <div class="DocHeaderRow"><h2>Fiscal Note</h2></div>
      <div class="DocRow"><div class="DocInfoCell">
        <div class="textLR">FN1</div>
        <div class="textType">Fiscal note</div>
        <a href="https://documents.house.mo.gov/fiscal.pdf">Fiscal PDF</a>
      </div></div>

      <div class="DocHeaderRow"><h2>Amendments</h2></div>
      <div class="DocRow"><div class="DocInfoCell">
        <a href="https://documents.house.mo.gov/amend1.pdf">Amendment PDF</a>
        <a href="/member/77">Jones</a>
        <span>HA 1</span>
        <img alt="Adopted" />
      </div></div>
    </body></html>
    """

    actions_html = """
    <html><body>
      <table id="actionTable">
        <tr><td>03/10/2026</td><td>Governor</td><td>Approved by Governor, Chapter 12</td></tr>
        <tr><td>03/01/2026</td><td>House</td><td>Truly Agreed To and Finally Passed</td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/BillDocumentMobile.aspx"):
            return httpx.Response(200, text=document_html, request=request)
        if path.endswith("/BillActions.aspx"):
            return httpx.Response(200, text=actions_html, request=request)
        return httpx.Response(200, text=detail_html, request=request)

    api.house_client = httpx.Client(
        base_url=settings.missouri_house_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://house.mo.gov/BillContentMobile.aspx?bill=HB10&code=R+&year=2026")
    finally:
        api.close()

    assert detail["bill"] == "HB10"
    assert detail["catchTitle"] == "Changes farm air permits."
    assert detail["sponsor"] == "Smith"
    assert detail["lastAction"] == "Approved by Governor, Chapter 12"
    assert detail["lastActionDate"] == "2026-03-10"
    assert detail["signedDate"] == "2026-03-10"
    assert detail["chapter"] == "12"
    assert detail["effectiveDate"] == "2026-08-28"
    assert detail["introduced"] == "https://documents.house.mo.gov/intro.pdf"
    assert detail["currentVersionPath"] == "https://documents.house.mo.gov/perfected.pdf"
    assert detail["summary"] == "https://documents.house.mo.gov/summary.pdf"
    assert detail["digest"] == "https://documents.house.mo.gov/fiscal.pdf"
    assert detail["amendments"][0]["amendmentNumber"] == "HA 1"
    assert detail["amendments"][0]["sponsor"] == "Jones"


def test_fetch_senate_bill_detail_extracts_summary_versions_and_amendments() -> None:
    settings = get_settings()
    api = MissouriApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <div class="main-header-text">SB 5</div>
      <div class="main-header-description">Creates a statewide farm grant program.</div>
      <div class="detail-grid__item"><span class="detail-grid__label">Sponsor</span><div class="detail-grid__value">Brown</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">House Handler</span><div class="detail-grid__value">Jones</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">Effective Date</span><div class="detail-grid__value">August 28, 2026</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">LR Number</span><div class="detail-grid__value">4894S.01I</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">Title</span><div class="detail-grid__value">Farm grant program</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">Committee</span><div class="detail-grid__value">Agriculture, Food Production and Outdoor Resources</div></div>
      <div class="detail-grid__item"><span class="detail-grid__label">Current Status</span><div class="detail-grid__value">Approved by Governor</div></div>
    </body></html>
    """

    actions_html = """
    <div class="actions-container">
      <table class="table">
        <tbody>
          <tr><td>04/15/2026</td><td>Approved by Governor, Chapter 9</td><td>J 999</td></tr>
          <tr><td>02/01/2026</td><td>Introduced and First Read</td><td>J 101</td></tr>
        </tbody>
      </table>
    </div>
    """

    bill_text_html = """
    <div class="bill-text-container">
      <a href="https://www.senate.mo.gov/26info/pdf-bill/perf/SB5.pdf" title="SB 5 - 4894S.02P">4894S.02P - Perfected</a>
      <a href="https://www.senate.mo.gov/26info/pdf-bill/intro/SB5.pdf" title="SB 5 - 4894S.01I">4894S.01I - Introduced</a>
    </div>
    """

    summaries_html = """
    <div class="summaries-container">
      <div class="card__body"><p>This bill creates a statewide farm grant program.</p></div>
    </div>
    """

    amendments_html = """
    <div class="amendments-container">
      <a href="https://www.senate.mo.gov/BillTracking/Bills/BillInformation?handler=AmendmentPdf&amp;year=2026&amp;amendmentId=236"
         class="link-card-item-with-chip"
         title="4894S.02F - SA 1">
        <span>3/23/2026 - SA 1 offered (Crawford)--(4894S.02F)</span>
        <span>3/23/2026 - Adopted</span>
      </a>
    </div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        handler_name = params.get("handler")
        if handler_name == "Actions":
            return httpx.Response(200, text=actions_html, request=request)
        if handler_name == "BillText":
            return httpx.Response(200, text=bill_text_html, request=request)
        if handler_name == "Summaries":
            return httpx.Response(200, text=summaries_html, request=request)
        if handler_name == "Amendments":
            return httpx.Response(200, text=amendments_html, request=request)
        if handler_name == "FiscalNotes":
            return httpx.Response(200, text="<div></div>", request=request)
        return httpx.Response(200, text=detail_html, request=request)

    api.senate_client = httpx.Client(
        base_url=settings.missouri_senate_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.senate.mo.gov/BillTracking/Bills/BillInformation?year=2026&billid=7")
    finally:
        api.close()

    assert detail["bill"] == "SB5"
    assert detail["catchTitle"] == "Creates a statewide farm grant program."
    assert detail["sponsor"] == "Brown; House handler: Jones"
    assert detail["billStatus"] == "Approved by Governor"
    assert detail["lastAction"] == "Approved by Governor, Chapter 9"
    assert detail["lastActionDate"] == "2026-04-15"
    assert detail["signedDate"] == "2026-04-15"
    assert detail["chapter"] == "9"
    assert detail["effectiveDate"] == "2026-08-28"
    assert detail["introduced"] == "https://www.senate.mo.gov/26info/pdf-bill/intro/SB5.pdf"
    assert detail["currentVersionPath"] == "https://www.senate.mo.gov/26info/pdf-bill/perf/SB5.pdf"
    assert detail["summaryHTML"] == summaries_html
    assert detail["digestHTML"] == "<p>Agriculture, Food Production and Outdoor Resources</p>"
    assert detail["amendments"][0]["amendmentNumber"] == "SA 1"
    assert detail["amendments"][0]["sponsor"] == "Crawford"
