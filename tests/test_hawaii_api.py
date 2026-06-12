from __future__ import annotations

import httpx

from app.hawaii_api import HawaiiApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_directory_listing() -> None:
    settings = get_settings()
    api = HawaiiApiClient(settings)
    api.close()

    directory_html = """
    <html><body><pre>
      <a href="/sessions/session2026/Bills/HB1_.HTM">HB1_.HTM</a>
      <a href="/sessions/session2026/Bills/HB1_.PDF">HB1_.PDF</a>
      <a href="/sessions/session2026/Bills/HB1_HD1_.HTM">HB1_HD1_.HTM</a>
      <a href="/sessions/session2026/Bills/HB1_HD1_HFA2_.PDF">HB1_HD1_HFA2_.PDF</a>
      <a href="/sessions/session2026/Bills/SB2_.PDF">SB2_.PDF</a>
      <a href="/sessions/session2026/Bills/SB9999_.PDF">SB9999_.PDF</a>
      <a href="/sessions/session2026/Bills/DC10_.PDF">DC10_.PDF</a>
    </pre></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions/session2026/Bills/"):
            return httpx.Response(200, text=directory_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.hawaii_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "SB2"]
    assert items[0]["detailPath"].endswith("billtype=HB&billnumber=1&year=2026")
    assert items[0]["currentVersionPath"].endswith("/sessions/session2026/Bills/HB1_.HTM")
    assert "HD1" in items[0]["currentVersionFingerprint"]
    assert "HFA2" in items[0]["currentVersionFingerprint"]


def test_fetch_bill_detail_parses_measure_page() -> None:
    settings = get_settings()
    api = HawaiiApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <table id="measure-info" class="table-striped">
        <tr><th scope="row">Measure Title:</th><td>RELATING TO SAMPLE RULES.</td></tr>
        <tr><th scope="row">Report Title:</th><td>Sample Rules</td></tr>
        <tr><th scope="row">Description:</th><td>Makes a sample update.</td></tr>
        <tr><th scope="row">Companion:</th><td><a href="/session/measure_indiv.aspx?billtype=SB&billnumber=5&year=2026">SB5</a></td></tr>
        <tr><th scope="row">Current Referral:</th><td>JDC, FIN</td></tr>
        <tr><th scope="row">Introducer(s):</th><td>LEE, KAI</td></tr>
        <tr><th scope="row">Act:</th><td>12</td></tr>
      </table>
      <table id="MainContent_GridViewStatus">
        <tr><th>Sort by Date</th><th></th><th>Status Text</th></tr>
        <tr><td>2/12/2026</td><td>H</td><td>Passed Final Reading, as amended (HD1).</td></tr>
        <tr><td>1/15/2026</td><td>H</td><td>Introduced and Pass First Reading.</td></tr>
      </table>
      <a href="/sessions/session2026/Bills/HB1_.pdf">PDF</a>
      <a href="/sessions/session2026/Bills/HB1_.HTM">HTML</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/session/measure_indiv.aspx"):
            return httpx.Response(200, text=detail_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.hawaii_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://data.capitol.hawaii.gov/session/measure_indiv.aspx?billtype=HB&billnumber=1&year=2026",
            {
                "billNum": "HB1",
                "_fileEntries": [
                    {
                        "label": "",
                        "documentUrl": "https://data.capitol.hawaii.gov/sessions/session2026/Bills/HB1_.HTM",
                        "extension": "htm",
                    },
                    {
                        "label": "HD1",
                        "documentUrl": "https://data.capitol.hawaii.gov/sessions/session2026/Bills/HB1_HD1_.HTM",
                        "extension": "htm",
                    },
                    {
                        "label": "HD1_HFA2",
                        "documentUrl": "https://data.capitol.hawaii.gov/sessions/session2026/Bills/HB1_HD1_HFA2_.PDF",
                        "extension": "pdf",
                    },
                ],
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1"
    assert detail["billStatus"] == "Passed Final Reading, as amended (HD1)."
    assert detail["lastActionDate"] == "2026-02-12"
    assert detail["sponsor"] == "LEE, KAI"
    assert detail["chapter"] == "12"
    assert detail["currentVersionPath"].endswith("/sessions/session2026/Bills/HB1_.HTM")
    assert "Sample Rules" in detail["summaryHTML"]
    assert detail["billActions"][0]["location"] == "H"
    assert detail["amendments"][0]["amendmentNumber"] == "HD1"
    assert detail["amendments"][1]["amendmentNumber"] == "HD1_HFA2"
