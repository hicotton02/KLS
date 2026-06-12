from __future__ import annotations

import httpx

from app.newhampshire_api import NewHampshireApiClient
from app.settings import get_settings


def test_fetch_year_bills_parses_results_page() -> None:
    settings = get_settings()
    api = NewHampshireApiClient(settings)
    api.close()

    results_html = """
    <html><body>
      <table>
        <tr>
          <td valign="top" width="180px">
            <big>HB1356-FN</big>
            <a href="bill_docket.aspx?lsr=3064&amp;sy=2026&amp;txtsessionyear=2026&amp;sortoption=">Bill Docket</a>
            <a href="bill_status.aspx?lsr=3064&amp;sy=2026&amp;txtsessionyear=2026&amp;sortoption=">Bill Status</a>
            Bill Text
            <a href="billText.aspx?sy=2026&amp;id=2276&amp;txtFormat=html">[HTML]</a>
            <a href="billText.aspx?sy=2026&amp;id=2276&amp;txtFormat=pdf&amp;v=current">[PDF]</a>
          </td>
          <td width="580px">
            <b>Title:</b>
            relative to sample fiscal-note language.
            <table width="100%">
              <tr><td width="21%"><i>G-Status:</i></td><td width="79%">SENATE</td></tr>
              <tr><td width="21%"><i>House Status:</i></td><td width="79%">PASSED/ADOPTED</td></tr>
              <tr><td width="21%"><i>Senate Status:</i></td><td width="79%">REPORT FILED</td></tr>
              <tr><td width="21%"><i>Next/Last Comm:</i></td><td>Senate Judiciary</td></tr>
              <tr><td width="21%"><i>Next/Last Hearing:</i></td><td>04/02/2026 at 01:20 PM SH Room 100</td></tr>
            </table>
          </td>
        </tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/results.aspx"):
            return httpx.Response(200, text=results_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.new_hampshire_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert len(items) == 1
    assert items[0]["billNum"] == "HB1356"
    assert items[0]["billStatus"] == "SENATE"
    assert items[0]["lastAction"] == "PASSED/ADOPTED"
    assert items[0]["currentVersionPath"].endswith("txtFormat=html")
    assert items[0]["detailPath"].endswith("lsr=3064&sy=2026&txtsessionyear=2026&sortoption=")


def test_fetch_bill_detail_parses_status_and_docket() -> None:
    settings = get_settings()
    api = NewHampshireApiClient(settings)
    api.close()

    status_html = """
    <html><body>
      <a href="bill_docket.aspx?lsr=3064&amp;sy=2026&amp;txtsessionyear=2026&amp;sortoption=">Bill Docket</a>
      Bill Text <a href="billText.aspx?sy=2026&amp;id=2276&amp;txtFormat=html">[HTML]</a>
      <table id="Table1">
        <tr><td>HB1356-FN</td></tr>
        <tr><td>Bill Title: relative to sample fiscal-note language.</td></tr>
        <tr><td></td></tr>
        <tr><td>General Status:</td><td></td></tr>
        <tr>
          <td>LSR#: 3064</td>
          <td>Body: H</td>
          <td>Local Govt: N</td>
          <td>Chapter#: None</td>
          <td>Gen Status: SENATE</td>
        </tr>
        <tr><td></td></tr>
        <tr><td>House Status</td></tr>
        <tr><td></td><td>Status</td><td>PASSED/ADOPTED</td></tr>
        <tr><td></td><td>Current Committee</td><td>House Judiciary</td></tr>
        <tr><td></td><td>Date Introduced</td><td>1/8/2025</td></tr>
        <tr><td></td></tr>
        <tr><td>Senate Status</td></tr>
        <tr><td></td><td>Status</td><td>REPORT FILED</td></tr>
        <tr><td></td><td>Current Committee</td><td>Senate Judiciary</td></tr>
        <tr><td></td><td>Date Introduced</td><td>3/10/2026</td></tr>
        <tr><td></td></tr>
        <tr><td>Sponsors</td></tr>
        <tr><td>Alice Example (R)</td><td>Bob Example (R)</td><td></td></tr>
        <tr><td></td></tr>
        <tr><td>Next/Last Hearing: SENATE Judiciary</td></tr>
        <tr><td>Date:</td><td>Time:</td><td>Place:</td></tr>
        <tr><td>04/02/2026</td><td>01:20 PM</td><td>SH Room 100</td></tr>
      </table>
    </body></html>
    """
    docket_html = """
    <html><body>
      <table id="Table1">
        <tr><td></td><td>Docket of HB1356</td><td>Docket Abbreviations</td></tr>
        <tr><td>Bill Title: relative to sample fiscal-note language.</td></tr>
        <tr><td>Official Docket of HB1356.</td></tr>
        <tr><td>Date</td><td>Body</td><td>Description</td></tr>
        <tr><td>4/2/2026</td><td>S</td><td>Committee Report: Ought to Pass</td></tr>
        <tr><td>4/8/2026</td><td>S</td><td>Amendment #2026-1111s : AA</td></tr>
        <tr><td>4/9/2026</td><td>S</td><td>Passed/Adopted with Amendment</td></tr>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bill_status.aspx"):
            return httpx.Response(200, text=status_html, request=request)
        if request.url.path.endswith("/bill_docket.aspx"):
            return httpx.Response(200, text=docket_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.new_hampshire_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://gc.nh.gov/bill_status/legacy/bs2016/bill_status.aspx?lsr=3064&sy=2026&txtsessionyear=2026&sortoption=",
            {"billNum": "HB1356", "catchTitle": "relative to sample fiscal-note language."},
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1356"
    assert detail["billStatus"] == "SENATE"
    assert detail["lastAction"] == "Passed/Adopted with Amendment"
    assert detail["lastActionDate"] == "2026-04-09"
    assert detail["sponsor"] == "Alice Example (R), Bob Example (R)"
    assert detail["currentVersionPath"].endswith("txtFormat=html")
    assert detail["amendments"][0]["amendmentNumber"] == "2026-1111S"
    assert detail["amendments"][0]["status"] == "Amendment #2026-1111s : AA"
