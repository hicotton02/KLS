from __future__ import annotations

import httpx

from app.colorado_api import ColoradoApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_all_pages() -> None:
    settings = get_settings()
    api = ColoradoApiClient(settings)
    api.client.close()

    responses = {
        "1": """
        <html><body>
        <div>Showing 1 - 25 of 50</div>
        <div class="bill-result">
          <span class="sponsor-bill-or-resolution-tag">HB25-1001</span>
          <a class="all-bills-data-heading" href="/bills/hb25-1001">First bill</a>
          <div class="sponsors"><a>Alice Smith</a></div>
        </div>
        </body></html>
        """,
        "2": """
        <html><body>
        <div>Showing 26 - 50 of 50</div>
        <div class="bill-result">
          <span class="sponsor-bill-or-resolution-tag">HB25-1002</span>
          <a class="all-bills-data-heading" href="/bills/hb25-1002">Second bill</a>
          <div class="sponsors"><a>Bob Jones</a></div>
        </div>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        body = responses.get(page, "<html><body></body></html>")
        return httpx.Response(200, text=body, request=request)

    api.client = httpx.Client(
        base_url=settings.colorado_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB25-1001", "HB25-1002"]
    assert items[1]["catchTitle"] == "Second bill"


def test_fetch_year_bills_deduplicates_duplicate_bill_rows() -> None:
    settings = get_settings()
    api = ColoradoApiClient(settings)
    api.client.close()

    duplicate_page = """
    <html><body>
    <div>Showing 1 - 25 of 2</div>
    <div class="bill-result">
      <span class="sponsor-bill-or-resolution-tag">HB26-1001</span>
      <a class="all-bills-data-heading" href="/bills/hb26-1001">First copy</a>
      <div class="sponsors"><a>Alice Smith</a></div>
    </div>
    <div class="bill-result">
      <span class="sponsor-bill-or-resolution-tag">HB26-1001</span>
      <a class="all-bills-data-heading" href="/bills/hb26-1001-alt">Second copy</a>
      <div class="sponsors"><a>Bob Jones</a></div>
    </div>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=duplicate_page, request=request)

    api.client = httpx.Client(
        base_url=settings.colorado_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB26-1001"]
    assert items[0]["catchTitle"] == "First copy"
