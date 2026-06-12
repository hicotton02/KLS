from __future__ import annotations

import httpx

from app.maine_api import MaineApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_maine_bill_directory_ranges() -> None:
    settings = get_settings()
    api = MaineApiClient(settings)
    api.close()

    first_directory_html = """
    <html><body>
      <ul class="paperList">
        <li><a href="//legislature.maine.gov/bills/billdirectory_ps.asp?snum=132&amp;ldFrom=0">LD 0</a></li>
        <li class="current">LD 1 - LD 200</li>
        <li><a href="//legislature.maine.gov/bills/billdirectory_ps.asp?snum=132&amp;ldFrom=201">LD 201 - LD 400</a></li>
      </ul>
      <table id="search-results">
        <tr>
          <td class="RecordIndex">1.</td>
          <td class="RecordNumbers">LD 1, SP 29, <span class="legisText">132nd Legislature</span></td>
          <td class="RecordTitle">An Act to improve storm readiness.</td>
        </tr>
        <tr class="final_row">
          <td class="LeftResultsPadding">&nbsp;</td>
          <td class="RecordLinks" colspan="2">
            <a href="//legislature.maine.gov/bills/display_ps.asp?snum=132&amp;paper=SP0029&amp;PID=1456">Bill &amp; Fiscal Information</a>
          </td>
        </tr>
        <tr>
          <td class="RecordIndex">2.</td>
          <td class="RecordNumbers">LD 2, HP 11, <span class="legisText">132nd Legislature</span></td>
          <td class="RecordTitle">An Act to widen road access.</td>
        </tr>
        <tr class="final_row">
          <td class="LeftResultsPadding">&nbsp;</td>
          <td class="RecordLinks" colspan="2">
            <a href="//legislature.maine.gov/bills/display_ps.asp?snum=132&amp;paper=HP0011&amp;PID=1456">Bill &amp; Fiscal Information</a>
          </td>
        </tr>
      </table>
    </body></html>
    """
    later_directory_html = """
    <html><body>
      <table id="search-results">
        <tr>
          <td class="RecordIndex">1.</td>
          <td class="RecordNumbers">LD 201, SP 99, <span class="legisText">132nd Legislature</span></td>
          <td class="RecordTitle">An Act to change committee procedures.</td>
        </tr>
        <tr class="final_row">
          <td class="LeftResultsPadding">&nbsp;</td>
          <td class="RecordLinks" colspan="2">
            <a href="//legislature.maine.gov/bills/display_ps.asp?snum=132&amp;paper=SP0099&amp;PID=1456">Bill &amp; Fiscal Information</a>
          </td>
        </tr>
      </table>
    </body></html>
    """
    empty_directory_html = "<html><body><table id='search-results'></table></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/billdirectory_ps.asp"):
            ld_from = request.url.params.get("ldFrom")
            if ld_from == "1":
                return httpx.Response(200, text=first_directory_html, request=request)
            if ld_from == "201":
                return httpx.Response(200, text=later_directory_html, request=request)
            if ld_from == "0":
                return httpx.Response(200, text=empty_directory_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.maine_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["LD1", "LD2", "LD201"]
    assert items[0]["detailPath"] == "https://legislature.maine.gov/bills/display_ps.asp?snum=132&paper=SP0029&PID=1456"
    assert items[0]["paper"] == "SP0029"
    assert items[2]["catchTitle"] == "An Act to change committee procedures."


def test_fetch_bill_detail_extracts_maine_summary_documents_and_amendments() -> None:
    settings = get_settings()
    api = MaineApiClient(settings)
    api.close()

    display_html = """
    <html><body>
      <div id="breadCrumb">
        <a href="//legislature.maine.gov/LawMakerWeb/summary.asp?paper=SP0029&amp;SessionID=16">Chamber Status, SP 29</a>
      </div>
      <h2 class="ldTitle">An Act to improve storm readiness.</h2>
      <div id="sec0">
        <a href="getPDF.asp?paper=SP0029&amp;item=1&amp;snum=132">Printed Document PDF</a>
        <a href="/legis/bills/bills_132nd/fiscalnotes/FN000101.htm">Fiscal Note</a>
        <span class="story_heading">Adopted Amendments</span>
        <span class="tlnk-amdblk even_row">
          <span class="tlnk-amd">Adopted by House &amp; Senate</span>
          <span class="story_subhead">C-A (S-9)</span>
          <span class="infoText">Emergency</span>
          <a href="getPDF.asp?paper=SP0029&amp;item=2&amp;snum=132">Printed Document PDF</a>
        </span>
        <span class="story_heading">Final Disposition</span>
        <span class="tlnk-final odd_row">Emergency Enacted, Apr 22, 2025<br/><span class="inlineHeading">Governor's Action:</span> Emergency Signed, Apr 22, 2025</span>
        <span class="story_heading">Chaptered Law</span>
        <span class="tlnk-bill even_row">
          <span class="inlineHeading">ACTPUB<br/>Chapter 33</span>
          <a href="getPDF.asp?paper=SP0029&amp;item=3&amp;snum=132">Printed Chapter PDF</a>
        </span>
      </div>
      <div id="sec3">
        <span class="story_heading">Status In Committee</span>
        Referred to <span class="inlineData">Committee on Housing and Economic Development</span> on <span class="inlineData">Jan 28, 2025.</span>
        <br/>
        <span class="inlineHeading">Latest Committee Action:</span>
        <span class="inlineData">Reported Out, Apr 7, 2025, OTP-AM</span>
        <br/>
        <span class="inlineHeading">Latest Committee Report:</span>
        <span class="inlineData">Apr 7, 2025; Ought To Pass As Amended</span>
        <table name="CDtab">
          <tr><th>Date</th><th>Action</th><th>Result</th></tr>
          <tr><td>Jan 15, 2025</td><td>Work Session Held</td><td></td></tr>
          <tr><td>Apr 7, 2025</td><td>Reported Out</td><td>OTP-AM</td></tr>
        </table>
      </div>
    </body></html>
    """
    summary_html = """
    <html><body>
      <table>
        <tr><td class="sectionheading">Bill Info</td></tr>
        <tr><td class="sectionbody"><b>LD 1</b> (SP 29)</td></tr>
        <tr><td align="center" class="sectionbody">Sponsored by <b>President Matthea Daughtry</b></td></tr>
      </table>
      <table class="sectionbody">
        <tr><td class="sectionheading">Status Summary</td><td class="sectionheading"></td></tr>
        <tr><td>Reference Committee</td><td><b>Housing and Economic Development</b></td></tr>
        <tr><td>Last House Action</td><td><b>4/15/2025 -&nbsp;</b>PASSED TO BE ENACTED.</td></tr>
        <tr><td>Last Senate Action</td><td><b>4/22/2025 -&nbsp;</b>PASSED TO BE ENACTED - Emergency - 2/3 Elected Required, in concurrence.</td></tr>
        <tr><td>Governor Action</td><td><b>Signed by the Governor (Emergency Measure)</b></td></tr>
        <tr><td>Chapter</td><td><b>33&nbsp;</b></td></tr>
        <tr><td>Final Law Type</td><td><b>Public Law</b></td></tr>
        <tr><td>Date</td><td><b>4/22/2025</b></td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/display_ps.asp"):
            return httpx.Response(200, text=display_html, request=request)
        if request.url.path.endswith("/summary.asp"):
            return httpx.Response(200, text=summary_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.maine_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://legislature.maine.gov/bills/display_ps.asp?snum=132&paper=SP0029&PID=1456",
            {"billNum": "LD1", "paper": "SP0029", "year": 2025},
        )
    finally:
        api.close()

    assert detail["bill"] == "LD1"
    assert detail["sponsor"] == "President Matthea Daughtry"
    assert detail["billStatus"] == "Signed by the Governor (Emergency Measure)"
    assert detail["lastAction"] == "Signed by the Governor (Emergency Measure)"
    assert detail["lastActionDate"] == "2025-04-22"
    assert detail["signedDate"] == "2025-04-22"
    assert detail["chapter"] == "33"
    assert detail["introduced"].endswith("item=1&snum=132")
    assert detail["currentVersionPath"].endswith("item=3&snum=132")
    assert detail["digest"].endswith("FN000101.htm")
    assert detail["amendments"][0]["amendmentNumber"] == "C-A (S-9)"
    assert detail["amendments"][0]["status"] == "Adopted by House & Senate"
    assert detail["billActions"][-1]["statusMessage"] == "Signed by the Governor (Emergency Measure)"
