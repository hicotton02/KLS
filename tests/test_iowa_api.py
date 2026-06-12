from __future__ import annotations

import httpx

from app.iowa_api import IowaApiClient
from app.settings import get_settings


def test_fetch_year_bills_uses_general_assembly_list() -> None:
    settings = get_settings()
    api = IowaApiClient(settings)
    api.close()

    ga_html = """
    <html><body>
      <select name="gaList">
        <option value="90">90 (01/09/2023 - 01/12/2025)</option>
        <option value="91" selected>91 (01/13/2025 - 01/11/2027)</option>
      </select>
    </body></html>
    """
    all_bills_html = """
    <html><body>
      <table>
        <tr>
          <th>Bill_prefix</th><th>Bill</th><th>Bill Title</th><th>Companion</th><th>Similar</th><th>Sponsor</th>
        </tr>
        <tr>
          <td>HF</td>
          <td><a href="/legislation/BillBook?ga=91&amp;ba=HF%201">HF 1</a></td>
          <td>Education updates</td>
          <td></td><td></td>
          <td>Rep. Smith</td>
        </tr>
        <tr>
          <td>SF</td>
          <td><a href="/legislation/BillBook?ga=91&amp;ba=SF%202">SF 2</a></td>
          <td>Farm grants</td>
          <td></td><td></td>
          <td>Sen. Brown</td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/legislation/BillBook"):
            return httpx.Response(200, text=ga_html, request=request)
        if request.url.path.endswith("/legislation/findLegislation/allbills"):
            return httpx.Response(200, text=all_bills_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.iowa_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HF1", "SF2"]
    assert items[0]["detailPath"] == "https://www.legis.iowa.gov/legislation/BillBook?ga=91&ba=HF%201"
    assert items[1]["sponsor"] == "Sen. Brown"


def test_fetch_bill_detail_reads_current_text_and_actions() -> None:
    settings = get_settings()
    api = IowaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <input name="selectedBill" value="HF 1" />
      <input name="ga" value="91" />
      <iframe id="bbContextDoc" src="/docs/publications/LGI/91/attachments/HF1.html?layout=false"></iframe>
      <ul>
        <li class="doc pdf"><a href="/docs/publications/LGI/91/HF1.pdf">HF1 PDF</a></li>
      </ul>
      <select id="billVersions">
        <option selected>Introduced</option>
      </select>
    </body></html>
    """
    actions_html = """
    <html><body>
      <table class="billActionTable">
        <tr><td>04/10/2026</td><td>Signed by Governor, Chapter 12.</td></tr>
        <tr><td>01/13/2026</td><td>Introduced.</td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/legislation/BillBook"):
            return httpx.Response(200, text=actions_html, request=request)
        if request.method == "GET" and request.url.path.endswith("/legislation/BillBook"):
            return httpx.Response(200, text=detail_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.iowa_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.legis.iowa.gov/legislation/BillBook?ga=91&ba=HF1",
            {
                "billNum": "HF1",
                "billTitle": "Education updates",
                "catchTitle": "Education updates",
                "sponsor": "Rep. Smith",
                "generalAssembly": 91,
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HF1"
    assert detail["catchTitle"] == "Education updates"
    assert detail["sponsor"] == "Rep. Smith"
    assert detail["lastAction"] == "Signed by Governor, Chapter 12."
    assert detail["lastActionDate"] == "2026-04-10"
    assert detail["signedDate"] == "2026-04-10"
    assert detail["chapter"] == "12"
    assert detail["introduced"] == "https://www.legis.iowa.gov/docs/publications/LGI/91/HF1.pdf"
    assert detail["currentVersionPath"] == "https://www.legis.iowa.gov/docs/publications/LGI/91/attachments/HF1.html?layout=false"
    assert len(detail["billActions"]) == 2
