from __future__ import annotations

import httpx

from app.nebraska_api import NebraskaApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_official_csv() -> None:
    settings = get_settings()
    api = NebraskaApiClient(settings)
    api.close()

    csv_text = """
"Document","Primary Introducer","Status","Description","Document ID"
"LB365A","Quick","Passed","Appropriation Bill","64471"
"LB716","Executive Board: Hansen, Chairperson","Passed","Revisor's bill","63242"
""".strip()

    api.client = httpx.Client(
        base_url=settings.nebraska_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=csv_text, request=request)),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["LB365A", "LB716"]
    assert items[0]["detailPath"] == "https://nebraskalegislature.gov/bills/view_bill.php?DocumentID=64471"


def test_fetch_bill_detail_reads_actions_and_document_links() -> None:
    settings = get_settings()
    api = NebraskaApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h2>LB365A - Appropriation Bill</h2>
      <a href="/bills/search_by_introducer.php?Introducer=153">Quick</a>
      <a href="/bills/search_by_date.php?SessionDay=2026-02-04">February 04, 2026</a>
      <a href="../FloorDocs/109/PDF/Intro/LB365A.pdf">Introduced</a>
      <a href="../FloorDocs/109/PDF/Final/LB365A.pdf">Final Reading</a>
      <a href="../FloorDocs/109/PDF/Slip/LB365A.pdf">Slip Law</a>
      <a href="../FloorDocs/109/PDF/Engrossed/LB365A.pdf">Engrossed</a>
      <a href="/reports/fiscal/fn-lb365a.pdf">Fiscal Note</a>
      <a href="/bills/view_actions.php?DocumentID=64471">View Details</a>
    </body></html>
    """
    actions_html = """
    <html><body>
      <table>
        <tr><th>Date</th><th>Description</th><th>Journal</th><th>Vote</th></tr>
        <tr><td>Apr 17, 2026</td><td>Approved by Governor on April 14, 2026</td><td>1700</td><td></td></tr>
        <tr><td>Apr 09, 2026</td><td>Passed on Final Reading 44-5*-0</td><td>1612</td><td>Vote</td></tr>
        <tr><td>Feb 04, 2026</td><td>Date of introduction</td><td>607</td><td></td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bills/view_bill.php"):
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path.endswith("/bills/view_actions.php"):
            return httpx.Response(200, text=actions_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.nebraska_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://nebraskalegislature.gov/bills/view_bill.php?DocumentID=64471",
            {"billNum": "LB365A", "billTitle": "Appropriation Bill", "documentId": "64471"},
        )
    finally:
        api.close()

    assert detail["bill"] == "LB365A"
    assert detail["catchTitle"] == "Appropriation Bill"
    assert detail["sponsor"] == "Quick"
    assert detail["lastAction"] == "Approved by Governor on April 14, 2026"
    assert detail["lastActionDate"] == "2026-04-17"
    assert detail["signedDate"] == "2026-04-17"
    assert detail["introduced"].endswith("/FloorDocs/109/PDF/Intro/LB365A.pdf")
    assert detail["digest"].endswith("/reports/fiscal/fn-lb365a.pdf")
    assert detail["currentVersionPath"].endswith("/FloorDocs/109/PDF/Slip/LB365A.pdf")
    assert len(detail["billActions"]) == 3
