from __future__ import annotations

import httpx

from app.rhodeisland_api import RhodeIslandApiClient
from app.settings import get_settings


def test_fetch_year_bills_groups_variants_under_base_bill() -> None:
    settings = get_settings()
    api = RhodeIslandApiClient(settings)
    api.close()

    house_listing = """
    <html><body>
      <table class="bill_data">
        <tr class="bill_row">
          <td class="bill_col1">H7000</td>
          <td class="bill_col2"><a href="H7000.pdf">PDF</a></td>
          <td class="bill_col3"><a href="H7000.htm">HTML</a></td>
        </tr>
        <tr class="bill_row_alt">
          <td class="bill_col1">H7001</td>
          <td class="bill_col2"><a href="H7001.pdf">PDF</a></td>
          <td class="bill_col3"><a href="H7001.htm">HTML</a></td>
        </tr>
        <tr class="bill_row">
          <td class="bill_col1">H7001A</td>
          <td class="bill_col2"><a href="H7001A.pdf">PDF</a></td>
          <td class="bill_col3"><a href="H7001A.htm">HTML</a></td>
        </tr>
      </table>
    </body></html>
    """
    senate_listing = """
    <html><body>
      <table class="bill_data">
        <tr class="bill_row">
          <td class="bill_col1">S2001</td>
          <td class="bill_col2"><a href="S2001.pdf">PDF</a></td>
          <td class="bill_col3"><a href="S2001.htm">HTML</a></td>
        </tr>
      </table>
    </body></html>
    """

    def text_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/HouseText26/HouseText26.html"):
            return httpx.Response(200, text=house_listing, request=request)
        if request.url.path.endswith("/SenateText26/SenateText26.html"):
            return httpx.Response(200, text=senate_listing, request=request)
        return httpx.Response(404, text="missing", request=request)

    api.text_client = httpx.Client(
        base_url=settings.rhode_island_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(text_handler),
    )
    api.status_client = httpx.Client(
        base_url=settings.rhode_island_status_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(404, request=request)),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["H7000", "H7001", "S2001"]
    assert items[1]["introducedPath"].endswith("H7001.htm")
    assert items[1]["currentVersionPath"].endswith("H7001A.htm")
    assert items[1]["currentVersionFingerprint"].startswith("H7001A|")
    assert len(items[1]["variants"]) == 2


def test_fetch_bill_detail_reads_status_report_and_variant_text() -> None:
    settings = get_settings()
    api = RhodeIslandApiClient(settings)
    api.close()

    form_html = """
    <html><body>
      <form>
        <input type="hidden" id="__VIEWSTATE" value="view" />
        <input type="hidden" id="__VIEWSTATEGENERATOR" value="gen" />
        <input type="hidden" id="__EVENTVALIDATION" value="valid" />
      </form>
    </body></html>
    """
    report_html = """
    <html><body>
      <span id="lblBills">
        <div>Condition: {Session Year: 2026} {Bill Range: 7408-7408}</div>
        <div>House Bill No. 7408 SUB A as amended <a href="http://webserver.rilegislature.gov/BillText/BillText26/HouseText26/H7408Aaa.pdf">7408 SUB A as amended</a></div>
        <div>Chapter 001</div>
        <div>BY Abney, Slater, O'Brien</div>
        <div>ENTITLED, AN ACT RELATING TO HEALTH FACILITIES</div>
        <div>{LC4723/A/1}</div>
        <div>01/29/2026 Introduced, referred to House Finance</div>
        <div>02/10/2026 House passed Sub A as amended</div>
        <div>02/10/2026 Senate passed Sub A as amended in concurrence</div>
        <div>02/10/2026 Transmitted to Governor</div>
        <div>02/11/2026 Signed by Governor</div>
        <div>Total Bills: 1</div>
      </span>
    </body></html>
    """
    current_html = """
    <html><body>
      <p>2026 -- H 7408 SUBSTITUTE A AS AMENDED</p>
      <p>Introduced By:</p>
      <p>Representatives Abney, Slater, and O'Brien</p>
      <p>Date Introduced:</p>
      <p>January 29, 2026</p>
      <p>The amended text goes here.</p>
    </body></html>
    """

    def text_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("H7408Aaa.htm") or request.url.path.endswith("H7408.htm") or request.url.path.endswith("H7408A.htm"):
            return httpx.Response(200, text=current_html, request=request)
        if request.url.path.endswith("H7408Aaa.pdf"):
            return httpx.Response(200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}, request=request)
        return httpx.Response(404, text="missing", request=request)

    def status_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=form_html, request=request)
        return httpx.Response(200, text=report_html, request=request)

    api.text_client = httpx.Client(
        base_url=settings.rhode_island_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(text_handler),
    )
    api.status_client = httpx.Client(
        base_url=settings.rhode_island_status_base,
        follow_redirects=True,
        transport=httpx.MockTransport(status_handler),
    )

    item = {
        "billNum": "H7408",
        "billType": "H",
        "detailPath": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408.htm",
        "introducedPath": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408.htm",
        "currentVersionPath": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408Aaa.htm",
        "currentVersionFingerprint": "H7408Aaa|https://webserver.rilegislature.gov/BillText26/HouseText26/H7408Aaa.htm",
        "variants": [
            {
                "bill_id": "H7408",
                "pdf_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408.pdf",
                "html_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408.htm",
            },
            {
                "bill_id": "H7408A",
                "pdf_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408A.pdf",
                "html_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408A.htm",
            },
            {
                "bill_id": "H7408Aaa",
                "pdf_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408Aaa.pdf",
                "html_url": "https://webserver.rilegislature.gov/BillText26/HouseText26/H7408Aaa.htm",
            },
        ],
    }

    try:
        detail = api.fetch_bill_detail(2026, item)
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
    finally:
        api.close()

    assert detail["bill"] == "H7408"
    assert detail["billTitle"] == "AN ACT RELATING TO HEALTH FACILITIES"
    assert detail["sponsor"] == "Abney, Slater, O'Brien"
    assert detail["lastAction"] == "Signed by Governor"
    assert detail["lastActionDate"] == "2026-02-11"
    assert detail["signedDate"] == "2026-02-11"
    assert detail["chapter"] == "001"
    assert detail["introduced"].endswith("H7408.htm")
    assert detail["summary"].endswith("H7408Aaa.pdf")
    assert detail["currentVersionPath"].endswith("H7408Aaa.htm")
    assert [item["amendmentNumber"] for item in detail["amendments"]] == ["Substitute A", "Substitute A as amended"]
    assert "The amended text goes here." in bill_text
