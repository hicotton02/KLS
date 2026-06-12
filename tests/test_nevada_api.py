from __future__ import annotations

from urllib.parse import parse_qs

import httpx

from app.nevada_api import NevadaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_nevada_bill_tabs() -> None:
    settings = get_settings()
    api = NevadaApiClient(settings)
    api.close()

    sessions_html = """
    <html><body>
      <a href="/App/NELIS/REL/83rd2025">83rd (2025) Session</a>
      <a href="/App/NELIS/REL/36th2025Special">36th (2025) Special Session</a>
    </body></html>
    """
    ab_fragment = """
    <div>
      <input name="ListItems[0].ContentKey" value="11742" />
      <div class="row">
        <div class="col-md-1 text-center"><a id="AB1" href="/App/NELIS/REL/83rd2025/Bill/11742/Overview">AB1</a></div>
        <div class="col-md-10">Voids certain regulations.</div>
      </div>
    </div>
    """
    sb_fragment = """
    <div>
      <input name="ListItems[0].ContentKey" value="21742" />
      <div class="row">
        <div class="col-md-1 text-center"><a id="SB2" href="/App/NELIS/REL/83rd2025/Bill/21742/Overview">SB2</a></div>
        <div class="col-md-10">Creates a sample water program.</div>
      </div>
    </div>
    """
    empty_fragment = "<div></div>"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/App/NELIS/REL":
            return httpx.Response(200, text=sessions_html, request=request)
        if request.url.path == "/App/NELIS/REL/83rd2025/HomeBill/BillsTab":
            bill_type = request.url.params.get("selectedBillTypes")
            if bill_type == "AB":
                return httpx.Response(200, text=ab_fragment, request=request)
            if bill_type == "SB":
                return httpx.Response(200, text=sb_fragment, request=request)
            return httpx.Response(200, text=empty_fragment, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.nevada_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["AB1", "SB2"]
    assert items[0]["billTitle"] == "Voids certain regulations."
    assert items[1]["detailPath"] == "https://www.leg.state.nv.us/App/NELIS/REL/83rd2025/Bill/21742/Overview"


def test_fetch_bill_detail_extracts_nevada_tabs() -> None:
    settings = get_settings()
    api = NevadaApiClient(settings)
    api.close()

    overview_page = "<html><head><title>AB3 Overview</title></head><body></body></html>"
    overview_fragment = """
    <div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Summary</div><div class="col">Revises alternative dispute rules.</div></div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Introduction Date</div><div class="col">Friday, September 27, 2024</div></div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Primary Sponsor</div><div class="col">Assembly Judiciary Committee</div></div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Title</div><div class="col">AN ACT relating to alternative dispute resolution. Close title AN ACT relating to alternative dispute resolution.</div></div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Digest</div><div class="col">Makes technical changes. Close digest Makes technical changes.</div></div>
      <div class="row mt-2"><div class="col-md-2 font-weight-bold">Most Recent History Action</div><div class="col"></div><div class="col">Approved by the Governor. Chapter 77. (See full list below)</div></div>
    </div>
    """
    text_fragment = """
    <input id="billName" name="BillName" type="hidden" value="AB3" />
    <div>
      <a href="https://www.leg.state.nv.us/Session/83rd2025/Bills/AB/AB3.pdf">As Introduced</a>
      <a href="https://www.leg.state.nv.us/Session/83rd2025/Bills/AB/AB3_EN.pdf">As Enrolled</a>
    </div>
    """
    amendments_fragment = """
    <div>
      <h2 class="h3">AB3 Adopted Amendments</h2>
      <a href="https://www.leg.state.nv.us/Session/83rd2025/Bills/Amendments/A_AB3_285.pdf">Amendment 285</a>
    </div>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/App/NELIS/REL/83rd2025/Bill/11748/Overview":
            return httpx.Response(200, text=overview_page, request=request)
        if request.url.path == "/App/NELIS/REL/83rd2025/Bill/FillSelectedBillTab":
            form = parse_qs(request.content.decode())
            selected = form.get("selectedTab", [""])[0]
            if selected == "Overview":
                return httpx.Response(200, text=overview_fragment, request=request)
            if selected == "Text":
                return httpx.Response(200, text=text_fragment, request=request)
            if selected == "Amendments":
                return httpx.Response(200, text=amendments_fragment, request=request)
            return httpx.Response(200, text="<div></div>", request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.nevada_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://www.leg.state.nv.us/App/NELIS/REL/83rd2025/Bill/11748/Overview")
    finally:
        api.close()

    assert detail["bill"] == "AB3"
    assert detail["sponsor"] == "Assembly Judiciary Committee"
    assert detail["chapter"] == "77"
    assert detail["billStatus"] == "Approved by the Governor. Chapter 77. (See full list below)"
    assert detail["introduced"] == "https://www.leg.state.nv.us/Session/83rd2025/Bills/AB/AB3.pdf"
    assert detail["currentVersionPath"] == "https://www.leg.state.nv.us/Session/83rd2025/Bills/AB/AB3_EN.pdf"
    assert detail["amendments"][0]["amendmentNumber"] == "Amendment 285"
