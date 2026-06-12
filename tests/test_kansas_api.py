from __future__ import annotations

import httpx

from app.kansas_api import KansasApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_official_bill_links() -> None:
    settings = get_settings()
    api = KansasApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><body>
            <a class="module-title" href="/li/b2025_26/measures/sb1/">SB1 - First Kansas bill.</a>
            <a class="module-title" href="/li/b2025_26/measures/hb2001/">HB2001 - Second Kansas bill.</a>
            <a class="module-title" href="/li/b2025_26/measures/hcr5001/">HCR5001 - Not a bill.</a>
            </body></html>
            """,
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.kansas_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB2001", "SB1"]
    assert items[0]["catchTitle"] == "Second Kansas bill."


def test_fetch_bill_detail_reads_versions_and_actions() -> None:
    settings = get_settings()
    api = KansasApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="""
            <html><head><title>SB 36 | Bills and Resolutions | Kansas State Legislature</title></head><body>
              <h3>Short Title</h3>
              <p>Sample Kansas bill title.</p>
              <div>Current Sponsor</div><div>Committee on Agriculture</div>
              <table>
                <tr><th>Version</th><th>Documents</th><th>SN</th><th>FN</th></tr>
                <tr>
                  <td>Enrolled</td>
                  <td><a href="/li/b2025_26/measures/documents/sb36_enrolled.pdf">pdf</a></td>
                  <td></td>
                  <td></td>
                </tr>
                <tr>
                  <td>As introduced</td>
                  <td><a href="/li/b2025_26/measures/documents/sb36_00_0000.pdf">pdf</a></td>
                  <td><a href="/li/b2025_26/measures/documents/supp_note_sb36_00_0000.pdf">pdf</a></td>
                  <td></td>
                </tr>
              </table>
              <table>
                <tr><th>Date</th><th>Chamber</th><th>Status</th><th>JPN</th></tr>
                <tr><td>Thu, Apr 10, 2025</td><td>Senate</td><td>Approved by Governor on Wednesday, March 26, 2025</td><td>1074</td></tr>
              </table>
            </body></html>
            """,
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.kansas_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("/li/b2025_26/measures/sb36/")
    finally:
        api.close()

    assert detail["bill"] == "SB36"
    assert detail["currentVersionPath"] == "https://www.kslegislature.gov/li/b2025_26/measures/documents/sb36_enrolled.pdf"
    assert detail["introduced"] == "https://www.kslegislature.gov/li/b2025_26/measures/documents/sb36_00_0000.pdf"
    assert detail["digest"] == "https://www.kslegislature.gov/li/b2025_26/measures/documents/supp_note_sb36_00_0000.pdf"
    assert detail["lastActionDate"] == "2025-04-10"
