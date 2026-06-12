from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from app.louisiana_api import LouisianaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_range_search_results() -> None:
    settings = get_settings()
    api = LouisianaApiClient(settings)
    api.close()

    initial_html = """
    <html><body>
      <form>
        <input type="hidden" name="__VIEWSTATE" value="initial-state" />
        <input type="hidden" name="__EVENTVALIDATION" value="initial-validation" />
        <input type="button" name="ctl00$ctl00$PageBody$PageContent$btnHeadRange" value="Search by Instrument Range" />
      </form>
    </body></html>
    """
    range_html = """
    <html><body>
      <form>
        <input type="hidden" name="__VIEWSTATE" value="range-state" />
        <input type="hidden" name="__EVENTVALIDATION" value="range-validation" />
        <input type="hidden" name="__PREVIOUSPAGE" value="range-previous" />
        <select name="ctl00$ctl00$PageBody$PageContent$ddlInstTypes2">
          <option value="HB-2" selected="selected">HB</option>
          <option value="SB-1">SB</option>
        </select>
      </form>
    </body></html>
    """
    hb_results = """
    <html><body>
      <span id="ctl00_ctl00_PageBody_PageContent_LabelTotalInstruments">There are 2 Instruments in this List</span>
      <table>
        <tr class="ResultsListDark">
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_HyperLink1" href="BillInfo.aspx?i=1001">HB1</a></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LinkAuthor" href="/member/1">MCFARLAND</a></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelStatus">Pending Senate Finance</span></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelConsidered"></span></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_HyperLink2" href="BillInfo.aspx?i=1001">more...</a></td>
        </tr>
        <tr class="ResultsListDark">
          <td></td>
          <td colspan="4"><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelKWordAndSTitle">APPROPRIATIONS:&nbsp;&nbsp;Funds state operations.</span></td>
        </tr>
        <tr>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_HyperLink1" href="BillInfo.aspx?i=1002">HB2</a></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_LinkAuthor" href="/member/2">BACALA</a></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_LabelStatus">Pending House final passage</span></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_LabelConsidered"></span></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_HyperLink2" href="BillInfo.aspx?i=1002">more...</a></td>
        </tr>
        <tr>
          <td></td>
          <td colspan="4"><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl1_LabelKWordAndSTitle">ENERGY:&nbsp;&nbsp;Changes carbon pipeline rules.</span></td>
        </tr>
      </table>
    </body></html>
    """
    sb_results = """
    <html><body>
      <span id="ctl00_ctl00_PageBody_PageContent_LabelTotalInstruments">There are 1 Instruments in this List</span>
      <table>
        <tr class="ResultsListDark">
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_HyperLink1" href="BillInfo.aspx?i=2001">SB1</a></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LinkAuthor" href="/member/3">ALLAIN</a></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelStatus">Pending Senate Finance</span></td>
          <td><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelConsidered"></span></td>
          <td><a id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_HyperLink2" href="BillInfo.aspx?i=2001">more...</a></td>
        </tr>
        <tr class="ResultsListDark">
          <td></td>
          <td colspan="4"><span id="ctl00_ctl00_PageBody_PageContent_ListViewSearchResults_ctrl0_LabelKWordAndSTitle">TAXATION:&nbsp;&nbsp;Changes sales tax credits.</span></td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/BillSearch.aspx"):
            return httpx.Response(200, text=initial_html, request=request)
        if request.method == "POST" and request.url.path.endswith("/BillSearch.aspx"):
            body = parse_qs(request.content.decode("utf-8"))
            assert body.get("__EVENTTARGET", [""])[0] == "ctl00$ctl00$PageBody$PageContent$btnHeadRange"
            return httpx.Response(200, text=range_html, request=request)
        if request.method == "POST" and request.url.path.endswith("/BillSearchList.aspx"):
            body = parse_qs(request.content.decode("utf-8"))
            selected = body.get("ctl00$ctl00$PageBody$PageContent$ddlInstTypes2", [""])[0]
            if selected == "HB-2":
                return httpx.Response(200, text=hb_results, request=request)
            if selected == "SB-1":
                return httpx.Response(200, text=sb_results, request=request)
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.louisiana_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "HB2", "SB1"]
    assert items[0]["detailPath"] == "https://legis.la.gov/legis/BillInfo.aspx?i=1001"
    assert items[2]["sponsor"] == "ALLAIN"
    assert items[2]["catchTitle"] == "TAXATION: Changes sales tax credits."


def test_fetch_bill_detail_extracts_documents_actions_and_amendments() -> None:
    settings = get_settings()
    api = LouisianaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <span>2026 Regular Session</span>
      <span id="ctl00_PageBody_LabelBillID">HB1</span>
      <span id="ctl00_PageBody_LabelAuthor">&nbsp;&nbsp;by Representative </span>
      <a id="ctl00_PageBody_LinkAuthor" href="https://house.louisiana.gov/H_Reps/members.aspx?ID=13">Jack McFarland</a>
      <span id="ctl00_PageBody_LabelShortTitle">APPROPRIATIONS:&nbsp;&nbsp;Funds state operations.</span>
      <span id="ctl00_PageBody_LabelCurrentStatus">Current Status:&nbsp;&nbsp;<noBR>Pending Senate Finance</noBR></span>

      <table id="ctl00_PageBody_MenuDocuments">
        <tr><td><a href="BillDocs.aspx?i=1001&amp;t=text">Text</a></td></tr>
        <tr><td><a href="ViewDocument.aspx?d=5002">HB1 Engrossed</a></td></tr>
        <tr><td><a href="ViewDocument.aspx?d=5001">HB1 Original</a></td></tr>
        <tr><td><a href="BillDocs.aspx?i=1001&amp;t=amendments">Amendments</a></td></tr>
        <tr><td><a href="ViewDocument.aspx?d=6001">House Committee Amendment #42 Adopted</a></td></tr>
        <tr><td><a href="BillDocs.aspx?i=1001&amp;t=digests">Digests</a></td></tr>
        <tr><td><a href="ViewDocument.aspx?d=7001">Digest of HB1 Engrossed</a></td></tr>
      </table>

      <table border="0" cellpadding="4" cellspacing="0">
        <tr valign="top" class="ResultsListDark">
          <td>04/21</td><td align="center">S</td><td align="right">7&nbsp;&nbsp;</td><td align="left" colspan="2">Signed by the governor. Act No. 12.</td>
        </tr>
        <tr valign="top">
          <td>04/20</td><td align="center">S</td><td align="right">4&nbsp;&nbsp;</td><td align="left" colspan="2">Received in the Senate.</td>
        </tr>
      </table>
    </body></html>
    """
    bill_text_html = """
    <html><body>
      <p>Section 1. This bill funds state operations.</p>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/BillInfo.aspx"):
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path.endswith("/ViewDocument.aspx"):
            document_id = request.url.params.get("d")
            if document_id == "5002":
                return httpx.Response(200, text=bill_text_html, request=request)
            return httpx.Response(200, text="<html><body>Document text.</body></html>", request=request)
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.louisiana_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://legis.la.gov/legis/BillInfo.aspx?i=1001",
            {"billNum": "HB1", "sponsor": "MCFARLAND", "billTitle": "APPROPRIATIONS: Funds state operations."},
        )
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
    finally:
        api.close()

    assert detail["bill"] == "HB1"
    assert detail["sponsor"] == "Jack McFarland"
    assert detail["billTitle"] == "APPROPRIATIONS: Funds state operations."
    assert detail["lastAction"] == "Signed by the governor. Act No. 12."
    assert detail["lastActionDate"] == "2026-04-21"
    assert detail["signedDate"] == "2026-04-21"
    assert detail["chapter"] == "12"
    assert detail["introduced"].endswith("d=5001")
    assert detail["currentVersionPath"].endswith("d=5002")
    assert detail["digest"].endswith("d=7001")
    assert detail["amendments"][0]["amendmentNumber"] == "House Committee Amendment #42 Adopted"
    assert detail["amendments"][0]["status"] == "Adopted"
    assert "Section 1. This bill funds state operations." in bill_text
