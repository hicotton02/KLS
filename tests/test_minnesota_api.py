from __future__ import annotations

import httpx

from app.minnesota_api import MinnesotaApiClient
from app.settings import get_settings


def test_fetch_year_bills_filters_selected_year_and_sessions() -> None:
    settings = get_settings()
    api = MinnesotaApiClient(settings)
    api.client.close()

    listing_html = """
    <html><body>
      <table class="table table-sm table-hover mt-2">
        <tbody>
          <tr>
            <td>2025 Regular Session</td>
            <td><a href="/bills/94/2025/0/HF/100/">HF100</a></td>
            <td><a href="/bills/94/2025/0/SF/100/">SF100</a></td>
            <td>25-00100</td>
          </tr>
          <tr>
            <td>2026 Regular Session</td>
            <td><a href="/bills/94/2026/0/HF/5022/">HF5022</a></td>
            <td><a href="/bills/94/2026/0/SF/1109/">SF1109</a></td>
            <td>25-00098</td>
          </tr>
          <tr>
            <td>2026 First Special Session</td>
            <td><a href="/bills/94/2026/1/HF/6001/">HF6001</a></td>
            <td></td>
            <td>26-00111</td>
          </tr>
        </tbody>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=listing_html, request=request)

    api.client = httpx.Client(
        base_url=settings.minnesota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HF5022", "SF1109", "HF6001"]
    assert [item["specialSessionValue"] for item in items] == [0, 0, 1]


def test_fetch_bill_detail_and_text_extract_bill_content() -> None:
    settings = get_settings()
    api = MinnesotaApiClient(settings)
    api.client.close()

    detail_html = """
    <html><body>
      <h1>HF 5022</h1>
      <div class="row" style="border-top: 1px solid rgba(0, 0, 0, 0.125)">
        <div class="col">
          <a href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/long-description/?body=house">Long Description</a>
        </div>
      </div>
      <div id="versions" class="py-3">
        <table class="w-100">
          <thead><tr class="d-flex"><th>Engrossments</th></tr></thead>
          <tbody class="container p-0">
            <tr class="row py-2 w-100 m-0 border-top text-break">
              <td class="col-md-4 p-0">
                <a href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/versions/0/">Introduction</a>
                <a href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/versions/0/pdf/">PDF</a>
              </td>
              <td class="col-md-4 p-0">Posted on 04/17/2026</td>
              <td class="col-md-4 p-0"></td>
            </tr>
            <tr class="row py-2 w-100 m-0 border-top text-break">
              <td class="col-md-4 p-0">
                <a href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/versions/2/">2nd Engrossment</a>
                <a href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/versions/2/pdf/">PDF</a>
              </td>
              <td class="col-md-4 p-0">Posted on 04/22/2026</td>
              <td class="col-md-4 p-0"></td>
            </tr>
          </tbody>
        </table>
      </div>
      <h2>Description</h2>
      <p>Insurance companies required to accept an individual taxpayer identification number on insurance coverage applications.</p>
      <h2>Authors <span class="author_count">(2)</span></h2>
      <div class="author">
        <ul>
          <li><a href="https://www.house.mn.gov/members/profile/15547">Feist</a>;</li>
          <li><a href="https://www.house.mn.gov/members/profile/15551">Agbaje</a></li>
        </ul>
      </div>
      <h2>Actions</h2>
      <div class="tab-content" id="myTabContent">
        <div class="tab-pane fade" id="chronological-tab-pane">
          <div>
            <table class="table">
              <tbody>
                <tr><th colspan="2">04/20/2026</th></tr>
                <tr>
                  <td class="house">
                    <div class="row">
                      <div class="col">Introduction and first reading, referred to Commerce Finance and Policy</div>
                      <div class="action_item col"><span><a class="text" href="https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/versions/0/">Intro</a></span></div>
                    </div>
                  </td>
                  <td class="senate"></td>
                </tr>
                <tr><th colspan="2">05/01/2026</th></tr>
                <tr>
                  <td class="house">
                    <div class="row">
                      <div class="col">Governor signed, Chapter 12</div>
                      <div class="action_item col"><span>pg. 999</span></div>
                    </div>
                  </td>
                  <td class="senate"></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </body></html>
    """

    version_html = """
    <html><body>
      <div id="document" class="col">
        <div class="bill_title">
          <p>A bill for an act relating to insurance.</p>
        </div>
        <p>BE IT ENACTED BY THE LEGISLATURE OF THE STATE OF MINNESOTA:</p>
        <p>Section 1. This bill changes insurance applications.</p>
      </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/versions/2/"):
            return httpx.Response(200, text=version_html, request=request)
        return httpx.Response(200, text=detail_html, request=request)

    api.client = httpx.Client(
        base_url=settings.minnesota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.revisor.mn.gov/bills/94/2026/0/HF/5022/")
        bill_text = api.fetch_public_document_text(detail["currentVersionPath"])
    finally:
        api.close()

    assert detail["bill"] == "HF5022"
    assert detail["catchTitle"].startswith("Insurance companies required")
    assert detail["sponsor"] == "Feist, Agbaje"
    assert detail["lastAction"] == "Governor signed, Chapter 12"
    assert detail["lastActionDate"] == "2026-05-01"
    assert detail["signedDate"] == "2026-05-01"
    assert detail["chapter"] == "12"
    assert detail["introduced"].endswith("/versions/0/")
    assert detail["currentVersionPath"].endswith("/versions/2/")
    assert "Section 1. This bill changes insurance applications." in bill_text
