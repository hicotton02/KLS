from __future__ import annotations

import httpx

from app.maryland_api import MarylandApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_official_master_list() -> None:
    settings = get_settings()
    api = MarylandApiClient(settings)
    api.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/2026RS/misc/billsmasterlist/legislation.json"):
            return httpx.Response(
                200,
                json=[
                    {
                        "BillNumber": "SB0002",
                        "Title": "Senate energy measure.",
                        "SponsorPrimary": "Senator Ellis",
                        "Synopsis": "Creates a statewide energy program.",
                        "Status": "In the House - First Reading",
                        "ChapterNumber": "",
                        "CrossfileBillNumber": "HB0001",
                    },
                    {
                        "BillNumber": "HB0001",
                        "Title": "House energy measure.",
                        "SponsorPrimary": "Delegate Carter",
                        "Synopsis": "Creates a statewide energy program.",
                        "Status": "In the Senate - First Reading",
                        "ChapterNumber": "",
                        "CrossfileBillNumber": "SB0002",
                    },
                ],
                request=request,
            )
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.maryland_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB0001", "SB0002"]
    assert items[0]["sponsor"] == "Delegate Carter"
    assert items[0]["detailPath"].endswith("/mgawebsite/Legislation/Details/HB0001?ys=2026RS")


def test_fetch_bill_detail_reads_summary_history_and_amendments() -> None:
    settings = get_settings()
    api = MarylandApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <a href="/bill-text/current.html">HB0001</a>
      <h2>HB0001</h2>
      <dl class="row top-box">
        <dt class="col-sm-2 top-box-title">Title</dt>
        <dd class="col-sm-10 top-box-title">Clean energy tax credit.</dd>
        <dt class="col-sm-2">Sponsored by</dt>
        <dd class="col-sm-10">Delegate Carter and Delegate Mills</dd>
        <dt class="col-sm-2">Status</dt>
        <dd class="col-sm-10">In the Senate - First Reading Budget and Taxation</dd>
        <dt class="col-sm-2">Analysis</dt>
        <dd class="col-sm-10"><a href="/analysis/hb0001.html">Fiscal and Policy Note</a></dd>
      </dl>
      <div id="divSummary" class="tab-pane fade show active">
        <div class="row">
          <div class="col-sm-12 details-content-area">
            <div class="row">
              <div class="col-sm-2 details-section-name">Synopsis</div>
              <div class="col-sm-10">Creates a clean energy tax credit for home upgrades.</div>
            </div>
            <div class="row">
              <div class="col-sm-2 details-section-name">Details</div>
              <div class="col-sm-10">
                <div class="container-fluid pl-0">
                  <div class="row pb-2">
                    <div class="col-sm-12">Cross-filed with: <a href="/mgawebsite/Legislation/Details/SB0002?ys=2026RS">SB0002</a></div>
                  </div>
                </div>
                <div class="container-fluid pl-0">
                  <div class="row pb-2">
                    <div class="col-sm-12">Bill File Type: Pre-Filed</div>
                  </div>
                </div>
                <div class="container-fluid pl-0">
                  <div class="row pb-2">
                    <div class="col-sm-12">Effective Date(s): June 1, 2026</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <table class="table table-striped" id="detailsHistory">
        <tr>
          <th>Chamber</th><th>Calendar Date</th><th>Legislative Date</th><th>Action</th><th>Proceedings</th>
        </tr>
        <tr>
          <td>House</td><td>1/14/2026</td><td>1/14/2026</td>
          <td>First Reading Ways and Means</td><td></td>
        </tr>
        <tr>
          <td></td><td></td><td></td>
          <td><a href="/bill-text/introduced.html">Text - First - Clean energy tax credit.</a></td><td></td>
        </tr>
        <tr>
          <td>House</td><td>2/03/2026</td><td>2/03/2026</td>
          <td>Favorable with Amendments { <a href="/2026RS/amds/bil_0001/HB0001_12352201.html">123522/1</a> (Delegate Chisholm) Adopted</td>
          <td></td>
        </tr>
        <tr>
          <td>Senate</td><td>2/10/2026</td><td>2/07/2026</td>
          <td>Referred Budget and Taxation</td><td></td>
        </tr>
      </table>
    </body></html>
    """
    current_text_html = """
    <html><body><main><p>This bill creates a clean energy tax credit for home upgrades.</p></main></body></html>
    """
    amendment_html = """
    <html><body><main><p>The amendment adds insulation projects to the credit.</p></main></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/mgawebsite/Legislation/Details/HB0001"):
            return httpx.Response(200, text=detail_html, request=request)
        if path.endswith("/bill-text/current.html"):
            return httpx.Response(200, text=current_text_html, request=request)
        if path.endswith("/bill-text/introduced.html"):
            return httpx.Response(200, text=current_text_html, request=request)
        if path.endswith("/2026RS/amds/bil_0001/HB0001_12352201.html"):
            return httpx.Response(200, text=amendment_html, request=request)
        if path.endswith("/analysis/hb0001.html"):
            return httpx.Response(200, text="<html><body><p>Fiscal note.</p></body></html>", request=request)
        return httpx.Response(404, text="missing", request=request)

    api.client = httpx.Client(
        base_url=settings.maryland_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://mgaleg.maryland.gov/mgawebsite/Legislation/Details/HB0001?ys=2026RS")
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
        amendment_text = api.fetch_public_document_text(detail["amendments"][0]["documentUrl"])
    finally:
        api.close()

    assert detail["bill"] == "HB0001"
    assert detail["billTitle"] == "Clean energy tax credit."
    assert detail["sponsor"] == "Delegate Carter and Delegate Mills"
    assert detail["billStatus"] == "In the Senate - First Reading Budget and Taxation"
    assert detail["lastAction"] == "Referred Budget and Taxation"
    assert detail["lastActionDate"] == "2026-02-07"
    assert detail["introduced"].endswith("/bill-text/introduced.html")
    assert detail["currentVersionPath"].endswith("/bill-text/current.html")
    assert detail["digest"].endswith("/analysis/hb0001.html")
    assert detail["effectiveDate"] == "2026-06-01"
    assert detail["crossfileBillNumber"] == "SB0002"
    assert detail["amendments"][0]["amendmentNumber"] == "123522/1"
    assert detail["amendments"][0]["sponsor"] == "Delegate Chisholm"
    assert "clean energy tax credit" in bill_text.lower()
    assert "insulation projects" in amendment_text.lower()
