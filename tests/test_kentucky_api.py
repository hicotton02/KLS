from __future__ import annotations

import httpx

from app.kentucky_api import KentuckyApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_session_record_listing() -> None:
    settings = get_settings()
    api = KentuckyApiClient(settings)
    api.close()

    listing_html = """
    <html><body>
      <table class="table table-hover table-bordered">
        <tr><th>Bill</th><th>Prime Sponsor</th><th>Title</th></tr>
        <tr>
          <td><a href="hb1.html">House Bill 1</a></td>
          <td>K. Moser</td>
          <td>AN ACT implementing the federal education opportunity program in Kentucky.</td>
        </tr>
        <tr>
          <td><a href="sr301.html">Senate Resolution 301</a></td>
          <td>A. West</td>
          <td>A RESOLUTION honoring Kentucky agriculture.</td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/record/26rs/all_bills_resolutions_title.html"
        return httpx.Response(200, text=listing_html, request=request)

    api.client = httpx.Client(
        base_url=settings.kentucky_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SR301"]
    assert items[0]["sponsor"] == "K. Moser"
    assert items[0]["detailPath"] == "https://apps.legislature.ky.gov/record/26rs/hb1.html"
    assert items[1]["detailPath"] == "https://apps.legislature.ky.gov/record/26rs/sr301.html"


def test_fetch_bill_detail_extracts_history_documents_and_amendments() -> None:
    settings = get_settings()
    api = KentuckyApiClient(settings)
    api.close()

    detail_html = """
    <html><head><title>26RS HB 1</title></head><body>
      <table class="table table-striped table-bordered">
        <tr><td>Last Action</td><td>03/17/26: delivered to Secretary of State (Acts Ch. 4)</td></tr>
        <tr><td>Title</td><td>AN ACT implementing the federal education opportunity program in Kentucky.</td></tr>
        <tr>
          <td>Bill Documents</td>
          <td>
            <a href="https://apps.legislature.ky.gov/recorddocuments/bill/26RS/hb1/bill.pdf">Current/Final</a>
            <a href="https://apps.legislature.ky.gov/recorddocuments/bill/26RS/hb1/orig_bill.pdf">Introduced</a>
          </td>
        </tr>
        <tr><td>Fiscal Impact Statement</td><td><a href="#amendments">Additional Fiscal Impact Statements Exist</a></td></tr>
        <tr><td>Bill Request Number</td><td>928</td></tr>
      </table>

      <table class="table table-striped table-bordered">
        <tr><th>Date</th><th>History</th></tr>
        <tr><td>02/19/26</td><td>introduced in House to Committee on Committees (H) to Appropriations &amp; Revenue (H)</td></tr>
        <tr><td>03/17/26</td><td>delivered to Secretary of State (Acts Ch. 4)</td></tr>
      </table>

      <table class="table table-striped table-bordered">
        <tr><td>Amendment</td><td><a href="https://apps.legislature.ky.gov/recorddocuments/bill/26RS/HB1/HCS1.pdf">House Committee Substitute 1</a></td></tr>
        <tr><td>Fiscal Impact Statement</td><td><a href="https://apps.legislature.ky.gov/recorddocuments/note/26RS/hb1/HCS1FN.pdf">Fiscal Note to House Committee Substitute 1</a></td></tr>
        <tr><td>Summary</td><td>Retain original provisions and add state tax election language.</td></tr>
        <tr><td>Index Headings</td><td>Education; Fiscal Note</td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/record/26rs/hb1.html"
        return httpx.Response(200, text=detail_html, request=request)

    api.client = httpx.Client(
        base_url=settings.kentucky_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://apps.legislature.ky.gov/record/26rs/hb1.html",
            {"billNum": "HB1", "sponsor": "K. Moser", "catchTitle": "AN ACT implementing the federal education opportunity program in Kentucky."},
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1"
    assert detail["sponsor"] == "K. Moser"
    assert detail["billStatus"] == "03/17/26: delivered to Secretary of State (Acts Ch. 4)"
    assert detail["lastActionDate"] == "2026-03-17"
    assert detail["signedDate"] == "2026-03-17"
    assert detail["chapter"] == "4"
    assert detail["introduced"] == "https://apps.legislature.ky.gov/recorddocuments/bill/26RS/hb1/orig_bill.pdf"
    assert detail["digest"] == "https://apps.legislature.ky.gov/record/26rs/hb1.html#amendments"
    assert detail["currentVersionPath"] == "https://apps.legislature.ky.gov/recorddocuments/bill/26RS/hb1/bill.pdf"
    assert len(detail["billActions"]) == 2
    assert len(detail["amendments"]) == 1
    assert detail["amendments"][0]["amendmentNumber"] == "HCS1"
    assert detail["amendments"][0]["documentUrl"] == "https://apps.legislature.ky.gov/recorddocuments/bill/26RS/HB1/HCS1.pdf"
