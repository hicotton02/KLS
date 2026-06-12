from __future__ import annotations

import httpx

from app.oklahoma_api import OklahomaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_current_status_report() -> None:
    settings = get_settings()
    api = OklahomaApiClient(settings)
    api.close()

    form_html = """
    <html><body>
      <form action="WebForm1.aspx" method="post">
        <input type="hidden" name="__VIEWSTATE" value="state-token" />
        <input type="hidden" name="__VIEWSTATEGENERATOR" value="generator-token" />
        <input type="hidden" name="__EVENTVALIDATION" value="event-token" />
      </form>
    </body></html>
    """
    report_html = """
    <html><body>
      <table border="0">
        <tr>
          <td width="75"><b>Measure</b></td>
          <td width="60"><b>Flags</b></td>
          <td><b>Chamber</b></td>
          <td width="160"><b>Status</b></td>
          <td width="100"><b>Date</b></td>
          <td><b>Title</b></td>
        </tr>
        <tr>
          <td><a href="http://www.oklegislature.gov/BillInfo.aspx?Bill=HB1001&session=2600">HB1001</a></td>
          <td>d</td>
          <td>H</td>
          <td>GENERAL ORDER</td>
          <td>03/12/2025</td>
          <td>Public safety cleanup.</td>
        </tr>
        <tr>
          <td><a href="http://www.oklegislature.gov/BillInfo.aspx?Bill=SB2&session=2600">SB2</a></td>
          <td>c</td>
          <td>S</td>
          <td>REFERRED TO COMMITTEE</td>
          <td>02/04/2025</td>
          <td>Tax relief.</td>
        </tr>
      </table>
    </body></html>
    """

    def report_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=form_html, request=request)
        if request.method == "POST":
            return httpx.Response(200, text=report_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.report_client = httpx.Client(
        base_url=settings.oklahoma_reports_base,
        follow_redirects=True,
        transport=httpx.MockTransport(report_handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1001", "SB2"]
    assert items[0]["billStatus"] == "GENERAL ORDER"
    assert items[0]["lastActionDate"] == "2025-03-12"
    assert items[1]["detailPath"] == "https://www.oklegislature.gov/BillInfo.aspx?Bill=SB2&session=2600"


def test_fetch_bill_detail_extracts_history_versions_and_amendments() -> None:
    settings = get_settings()
    api = OklahomaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <span id="ctl00_ContentPlaceHolder1_lblBillDisplay">HB 1001</span>
      <a id="ctl00_ContentPlaceHolder1_lnkAuth" href="/members/bashore">Bashore</a>
      <a id="ctl00_ContentPlaceHolder1_lnkOtherAuth" href="/members/thompson">Thompson (Kristen)</a>
      <a id="ctl00_ContentPlaceHolder1_lnkIntroduced" href="https://www.oklegislature.gov/cf_pdf/2025-26%20int/hb/HB1001%20int.pdf">HB 1001</a>
      <span id="ctl00_ContentPlaceHolder1_txtST">Crimes and punishments; minimum prison sentences; effective date.</span>

      <table>
        <tr><td><h1 class="house">History For HB 1001</h1></td></tr>
        <tr><td>Action</td><td>Journal Page</td><td>Date</td><td>Chamber</td></tr>
        <tr><td>First Reading</td><td>82</td><td>02/03/2025</td><td>H</td></tr>
        <tr><td>Approved by Governor</td><td>1182</td><td>05/05/2025</td><td>H</td></tr>
      </table>

      <table id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel2_tblAmendments">
        <tr><td colspan="2"><h1 class="house">Floor Amendments (Senate)</h1></td></tr>
        <tr>
          <td><a href="https://www.oklegislature.gov/cf_pdf/2025-26 FLOOR AMENDMENTS/Senate/HB1001 FA1.PDF">HB1001 FA1.PDF</a></td>
          <td>4/29/2025</td>
        </tr>
      </table>

      <table id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel3_tblBillSum">
        <tr><td colspan="2"><h1 class="house">Bill Summaries/Fiscal Impact for HB 1001 (House)</h1></td></tr>
        <tr><td><a href="https://www.oklegislature.gov/cf_pdf/2025-26 SUPPORT DOCUMENTS/BILLSUM/House/HB1001 INT BILLSUM.PDF">Introduced</a></td><td>2/6/2025</td></tr>
        <tr><td colspan="2"><h1 class="house">Fiscal Impact Statements For HB 1001 (Senate)</h1></td></tr>
        <tr><td><a href="https://www.oklegislature.gov/cf_pdf/2025-26 SUPPORT DOCUMENTS/impact statements/fiscal/Senate/HB1001 ENG FI.PDF">HB1001 ENG FI.PDF</a></td><td>Fiscal (Senate)</td></tr>
      </table>

      <table id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel4_tblVersions">
        <tr><td colspan="2"><h1 class="house">Versions of HB 1001</h1></td></tr>
        <tr><td><a href="https://www.oklegislature.gov/cf_pdf/2025-26 INT/hB/HB1001 INT.PDF">Introduced</a></td><td>11/15/2024</td></tr>
        <tr><td><a href="https://www.oklegislature.gov/cf_pdf/2025-26 ENR/hB/HB1001 ENR.PDF">Enrolled (final version)</a></td><td>4/30/2025</td></tr>
        <tr><td colspan="2"><h1 class="house">Committee Reports for HB 1001</h1></td></tr>
      </table>

      <table id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel5_tblVotes">
        <tr><td colspan="2"><h1 class="house">Votes on HB 1001</h1></td></tr>
        <tr><td><a href="https://www.oklegislature.gov/cf/2025-26 SUPPORT DOCUMENTS/votes/House/HB1001_VOTES.HTM">HB1001_VOTES.HTM</a></td><td>All House Votes</td></tr>
      </table>

      <table id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel6_tblCoAuth">
        <tr><td><h1 class="house">Authors/Co Authors for HB 1001</h1></td></tr>
        <tr><td>West (Josh) (H)</td></tr>
        <tr><td>Murdock (S)</td></tr>
      </table>
    </body></html>
    """

    def site_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=detail_html, request=request)

    api.site_client = httpx.Client(
        base_url=settings.oklahoma_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(site_handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.oklegislature.gov/BillInfo.aspx?Bill=HB1001&Session=2600")
    finally:
        api.close()

    assert detail["bill"] == "HB1001"
    assert detail["catchTitle"] == "Crimes and punishments; minimum prison sentences; effective date."
    assert detail["sponsor"] == "Bashore"
    assert detail["sponsorStringHouse"] == "Bashore"
    assert detail["sponsorStringSenate"] == "Thompson (Kristen)"
    assert detail["billStatus"] == "Approved by Governor"
    assert detail["lastActionDate"] == "2025-05-05"
    assert detail["signedDate"] == "2025-05-05"
    assert detail["introduced"] == "https://www.oklegislature.gov/cf_pdf/2025-26 INT/hB/HB1001 INT.PDF"
    assert detail["summary"] == "https://www.oklegislature.gov/cf_pdf/2025-26 SUPPORT DOCUMENTS/BILLSUM/House/HB1001 INT BILLSUM.PDF"
    assert detail["digest"] == "https://www.oklegislature.gov/cf_pdf/2025-26 SUPPORT DOCUMENTS/impact statements/fiscal/Senate/HB1001 ENG FI.PDF"
    assert detail["currentVersionPath"] == "https://www.oklegislature.gov/cf_pdf/2025-26 ENR/hB/HB1001 ENR.PDF"
    assert len(detail["billActions"]) == 2
    assert len(detail["amendments"]) == 1
    assert detail["amendments"][0]["documentUrl"].endswith("HB1001 FA1.PDF")
    assert "Official vote files" in detail["digestHTML"]
