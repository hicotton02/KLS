from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from app.alaska_api import AlaskaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_bill_table() -> None:
    settings = get_settings()
    api = AlaskaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><body>
            <table>
              <tr><th>Bill</th><th>Short Title</th><th>Prime Sponsor(s)</th><th></th><th>Current Status</th><th>Status Date</th></tr>
              <tr>
                <td><a href="/basis/Bill/Detail/34?Root=hb1">HB   1</a></td>
                <td>First bill</td>
                <td>REP. SMITH</td>
                <td></td>
                <td>(H) FIN</td>
                <td>05/18/2025</td>
              </tr>
              <tr>
                <td><a href="/basis/Bill/Detail/34?Root=sb2">SB   2</a></td>
                <td>Second bill</td>
                <td>SEN. JONES</td>
                <td></td>
                <td>(S) JUD</td>
                <td>02/04/2025</td>
              </tr>
            </table>
            </body></html>
            """,
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.alaska_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SB2"]
    assert items[0]["lastActionDate"] == "2025-05-18"


def test_fetch_bill_detail_deduplicates_repeated_amendment_numbers() -> None:
    settings = get_settings()
    api = AlaskaApiClient(settings)

    soup = BeautifulSoup(
        """
        <div id="tab3_4">
          <table>
            <tr><th>Amendment</th><th>Chamber</th><th>Action</th><th>Date</th><th>Page</th><th>PDF</th></tr>
            <tr>
              <td>AM 1</td><td>H</td><td>ADOPTED</td><td>2025-05-05</td><td>1017</td>
              <td><a class="pdf" href="/PDF/34/A/HB0010-H001.PDF">pdf</a></td>
            </tr>
            <tr>
              <td>AM 1</td><td>H</td><td>READ AGAIN</td><td>2025-05-06</td><td>1020</td>
              <td><a class="pdf" href="/PDF/34/A/HB0010-H001.PDF">pdf</a></td>
            </tr>
          </table>
        </div>
        """,
        "html.parser",
    )

    try:
        amendments = api._amendment_rows(soup)
    finally:
        api.close()

    assert len(amendments) == 1
    assert amendments[0]["amendmentNumber"] == "AM 1"
