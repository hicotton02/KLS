from __future__ import annotations

import httpx

from app.settings import get_settings
from app.wisconsin_api import WisconsinApiClient


def test_fetch_year_bills_reads_wisconsin_proposal_index() -> None:
    settings = get_settings()
    api = WisconsinApiClient(settings)
    api.client.close()

    proposals_html = """
    <html><body>
      <a href="/document/proposaltext/2025/REG/AB1">AB1: Bill Text</a>
      <a href="/document/proposaltext/2025/REG/SB18">SB18: Bill Text</a>
      <a href="/document/proposaltext/2025/REG/AB1">AB1: Bill Text</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/2025/related/proposals":
            return httpx.Response(200, text=proposals_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.wisconsin_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["AB1", "SB18"]
    assert items[0]["detailPath"] == "https://docs.legis.wisconsin.gov/document/session/2025/REG/AB1"


def test_fetch_bill_detail_extracts_wisconsin_status_and_links() -> None:
    settings = get_settings()
    api = WisconsinApiClient(settings)
    api.client.close()

    session_html = """
    <html><head><title>2025 Assembly Bill 1</title></head><body>
      <div class="proposalTitle"><h1>Assembly Bill 1</h1></div>
      <h2>Status: A - Vetoed</h2>
      <h2>Important Actions (newest first)</h2>
      <table class="history">
        <tr><th>Date / House</th><th>Action</th><th>Journal</th></tr>
        <tr><td class="date">3/28/2025 Asm.</td><td class="entry">Report vetoed by the Governor on 3-28-2025</td><td class="journal">86</td></tr>
        <tr><td class="date">3/18/2025 Sen.</td><td class="entry">Read a third time and concurred in, Ayes 18, Noes 14</td><td class="journal">127</td></tr>
      </table>
      <h2>Links</h2>
      <ul>
        <li><a href="/document/vetomessages/2025/AB1.pdf">Veto Message</a></li>
        <li><a href="/document/vetoedenrolledbills/2025/REG/AB1">Text as Enrolled</a></li>
        <li><a href="/document/proposaltext/2025/REG/AB1">Bill Text</a></li>
        <li><a href="/document/fiscalestimates/2025/REG/AB1">Fiscal Estimates and Reports</a></li>
      </ul>
      <h2>History</h2>
      <table class="history">
        <tr><th>Date / House</th><th>Action</th><th>Journal</th></tr>
        <tr><td class="date">1/31/2025 Asm.</td><td class="entry">Introduced by Representatives Wittke and Novak. Referred to Committee on Education</td><td class="journal">24</td></tr>
        <tr><td class="date">2/19/2025 Asm.</td><td class="entry">Read a third time and passed, Ayes 54, Noes 44</td><td class="journal">37</td></tr>
        <tr><td class="date">3/28/2025 Asm.</td><td class="entry">Report vetoed by the Governor on 3-28-2025</td><td class="journal">86</td></tr>
      </table>
    </body></html>
    """
    proposal_html = """
    <html>
      <head>
        <meta name="Description" content="Relating to: changes to the educational assessment program and the school and school district accountability report." />
      </head>
      <body>
        <div>2025 ASSEMBLY BILL 1 January 31, 2025 - Introduced by Representatives Wittke and Novak. Referred to Committee on Education.</div>
      </body>
    </html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/document/session/2025/REG/AB1":
            return httpx.Response(200, text=session_html, request=request)
        if request.url.path == "/document/proposaltext/2025/REG/AB1":
            return httpx.Response(200, text=proposal_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.wisconsin_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://docs.legis.wisconsin.gov/document/session/2025/REG/AB1",
            {
                "billNum": "AB1",
                "currentVersionPath": "https://docs.legis.wisconsin.gov/document/proposaltext/2025/REG/AB1",
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "AB1"
    assert detail["catchTitle"] == "changes to the educational assessment program and the school and school district accountability report"
    assert detail["sponsor"] == "Representatives Wittke and Novak"
    assert detail["billStatus"] == "A - Vetoed"
    assert detail["lastAction"] == "Report vetoed by the Governor on 3-28-2025"
    assert detail["lastActionDate"] == "2025-03-28"
    assert detail["introduced"].endswith("/document/proposaltext/2025/REG/AB1")
    assert detail["currentVersionPath"].endswith("/document/vetoedenrolledbills/2025/REG/AB1")
    assert "Status: A - Vetoed" in detail["summaryHTML"]
    assert "Report vetoed by the Governor" in detail["digestHTML"]
