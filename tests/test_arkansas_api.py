from __future__ import annotations

import httpx

from app.arkansas_api import ArkansasApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_all_types_and_pages() -> None:
    settings = get_settings()
    api = ArkansasApiClient(settings)
    api.close()

    search_html = """
    <html><body>
      <a href="/Bills/ViewBills?type=HB&ddBienniumSession=2025%2F2026F">House Bills</a>
      <a href="/Bills/ViewBills?type=SB&ddBienniumSession=2025%2F2026F">Senate Bills</a>
    </body></html>
    """
    view_pages = {
        ("HB", "0"): """
        <html><body>
          <div id="tableDataWrapper">
            <div class="row tableRow">
              <a href="/Bills/Detail?id=HB1001&amp;ddBienniumSession=2025%2F2026F">HB1001</a>
              <div class="col-md-7">Clean air updates.</div>
              <a href="/Legislators/Detail?member=1">Rep. Smith</a>
              <a href="/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FHB1001.pdf">PDF</a>
            </div>
          </div>
          <div>Page 1 of 2</div>
        </body></html>
        """,
        ("HB", "20"): """
        <html><body>
          <div id="tableDataWrapper">
            <div class="row tableRowAlt">
              <a href="/Bills/Detail?id=HB1002&amp;ddBienniumSession=2025%2F2026F">HB1002</a>
              <div class="col-md-7">Water permit changes.</div>
              <a href="/Legislators/Detail?member=2">Rep. Jones</a>
              <a href="/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FHB1002.pdf">PDF</a>
            </div>
          </div>
          <div>Page 2 of 2</div>
        </body></html>
        """,
        ("SB", "0"): """
        <html><body>
          <div id="tableDataWrapper">
            <div class="row tableRow">
              <a href="/Bills/Detail?id=SB1&amp;ddBienniumSession=2025%2F2026F">SB1</a>
              <div class="col-md-7">Farm tax credit.</div>
              <a href="/Legislators/Detail?member=3">Sen. Brown</a>
              <a href="/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FSB1.pdf">PDF</a>
            </div>
          </div>
          <div>Page 1 of 1</div>
        </body></html>
        """,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Bills/SearchByRange"):
            return httpx.Response(200, text=search_html, request=request)
        if request.url.path.endswith("/Bills/ViewBills"):
            key = (request.url.params.get("type", ""), request.url.params.get("start", "0"))
            return httpx.Response(200, text=view_pages[key], request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.arkansas_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1001", "HB1002", "SB1"]
    assert items[0]["sponsor"] == "Rep. Smith"
    assert items[1]["catchTitle"] == "Water permit changes."
    assert "SB1.pdf" in items[2]["currentVersionPath"]


def test_fetch_bill_detail_extracts_metadata_and_history() -> None:
    settings = get_settings()
    api = ArkansasApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <h1>HB1001 - Clean air updates.</h1>
      <div id="tableDataWrapper" role="grid">
        <div class="tableRow"><div>Status:</div><div>Act 45</div></div>
        <div class="tableRowAlt"><div>Lead Sponsor:</div><div>Rep. Smith</div></div>
        <div class="tableRow"><div>Introduction Date:</div><div>01/10/2026</div></div>
        <div class="tableRowAlt"><div>Act Date:</div><div>02/15/2026</div></div>
        <div class="tableRow"><div>Act Number:</div><div>45</div></div>
      </div>
      <a href="/Home/FTPDocument?path=%2FBills%2FVetoBook.pdf&amp;ddBienniumSession=2025%2F2026F">Governor's Veto List</a>
      <a href="/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FHB1001.pdf">Bill PDF</a>
      <a href="/Acts/FTPDocument?path=%2FActs%2F2026%2FPublic%2FACT45.pdf">Act PDF</a>
      <div><h3>Bill Status History</h3></div>
      <div id="tableDataWrapper" role="grid">
        <div class="tableRow">
          <div>House</div>
          <div>02/15/2026</div>
          <div>Act 45 signed by Governor</div>
          <a href="/Bills/Votes?id=123">Vote</a>
        </div>
        <div class="tableRowAlt">
          <div>House</div>
          <div>01/10/2026</div>
          <div>Filed</div>
        </div>
      </div>
    </body></html>
    """

    api.client = httpx.Client(
        base_url=settings.arkansas_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=detail_html, request=request)),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.arkleg.state.ar.us/Bills/Detail?id=HB1001&ddBienniumSession=2025%2F2026F",
            {
                "currentVersionPath": "https://www.arkleg.state.ar.us/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FHB1001.pdf"
            },
        )
    finally:
        api.close()

    assert detail["bill"] == "HB1001"
    assert detail["catchTitle"] == "Clean air updates."
    assert detail["sponsor"] == "Rep. Smith"
    assert detail["billStatus"] == "Act 45"
    assert detail["lastAction"] == "Act 45 signed by Governor"
    assert detail["lastActionDate"] == "2026-02-15"
    assert detail["signedDate"] == "2026-02-15"
    assert detail["chapter"] == "45"
    assert detail["currentVersionPath"] == "https://www.arkleg.state.ar.us/Home/FTPDocument?path=%2FBills%2F2026F%2FPublic%2FHB1001.pdf"
    assert detail["digest"] == "https://www.arkleg.state.ar.us/Acts/FTPDocument?path=%2FActs%2F2026%2FPublic%2FACT45.pdf"
    assert len(detail["billActions"]) == 2
