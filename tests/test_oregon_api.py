from __future__ import annotations

import httpx

from app.oregon_api import OregonApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_oregon_grouped_measure_list() -> None:
    settings = get_settings()
    api = OregonApiClient(settings)
    api.client.close()

    list_html = """
    <html><body>
      <ul id="HB20_search" class="measure-item" data-load-action="/liz/2025R1/Measures/MeasureGroupedListing?prefix=HB&amp;measureGroup=20"></ul>
      <ul id="SB0_search" class="measure-item" data-load-action="/liz/2025R1/Measures/MeasureGroupedListing?prefix=SB&amp;measureGroup=0"></ul>
    </body></html>
    """
    hb_group_html = """
    <li class="measure-desc row">
      <span class="col-md-4"><a href="/liz/2025R1/Measures/Overview/HB2001">HB 2001</a></span>
      <span class="col-md-8">Creates a water task force.</span>
    </li>
    <li class="measure-desc row">
      <span class="col-md-4"><a href="/liz/2025R1/Measures/Overview/HB2001">HB 2001</a></span>
      <span class="col-md-8">Creates a water task force.</span>
    </li>
    """
    sb_group_html = """
    <li class="measure-desc row">
      <span class="col-md-4"><a href="/liz/2025R1/Measures/Overview/SB18">SB 18</a></span>
      <span class="col-md-8">Updates wildfire response rules.</span>
    </li>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/liz/2025R1/Measures/list":
            return httpx.Response(200, text=list_html, request=request)
        if request.url.path == "/liz/2025R1/Measures/MeasureGroupedListing":
            if request.url.params.get("prefix") == "HB":
                return httpx.Response(200, text=hb_group_html, request=request)
            if request.url.params.get("prefix") == "SB":
                return httpx.Response(200, text=sb_group_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.oregon_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB2001", "SB18"]
    assert items[0]["catchTitle"] == "Creates a water task force."
    assert items[1]["detailPath"] == "https://olis.oregonlegislature.gov/liz/2025R1/Measures/Overview/SB18"


def test_fetch_bill_detail_extracts_oregon_measure_metadata() -> None:
    settings = get_settings()
    api = OregonApiClient(settings)
    api.client.close()

    detail_html = """
    <html><body>
      <script>
        var Srv = {"ChiefSponsors":[{"DisplayName":"Representative Smith","SponsorType":"Member"}],"RegularSponsors":[{"DisplayName":"Senator Jones","SponsorType":"Member"},{"DisplayName":" (Presession filed.)","SponsorType":"Presession"}]};
        Srv.Measure = {"MeasureId":12345,"SessionId":266,"MeasurePrefix":"HB","MeasureNumber":2335,"CatchLine":"Creates a water task force.","MeasureSummary":"Digest: Creates a water task force.\\nRequires a public report by December 1, 2025.","RelatingTo":"Relating to water; declaring an emergency.","AtRequestOf":null,"EffectiveDate":"July 1, 2025","ChapterNumber":"12","CurrentLocation":"Chaptered","CurrentCommittee":null};
      </script>
    </body></html>
    """
    history_html = """
    <table class="table">
      <tr><td class="row-title">1-13 (H)</td><td>First reading. Referred to Speaker's desk.</td><td></td><td></td></tr>
      <tr><td class="row-title">2-14 (H)</td><td>Third reading. Passed.</td><td></td><td></td></tr>
      <tr><td class="row-title">3-05 (H)</td><td>Governor signed.</td><td></td><td></td></tr>
    </table>
    """
    version_html = """
    <li><a href="/liz/2025R1/Downloads/MeasureDocument/HB2335/Introduced">Introduced</a></li>
    <li><a href="/liz/2025R1/Downloads/MeasureDocument/HB2335/Enrolled">Enrolled</a></li>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/liz/2025R1/Measures/Overview/HB2335":
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path == "/liz/2025R1/Measures/Overview/GetHistory/HB2335":
            return httpx.Response(200, text=history_html, request=request)
        if request.url.path == "/liz/2025R1/Measures/MeasureVersionList/HB2335":
            return httpx.Response(200, text=version_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.oregon_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://olis.oregonlegislature.gov/liz/2025R1/Measures/Overview/HB2335")
    finally:
        api.close()

    assert detail["bill"] == "HB2335"
    assert detail["catchTitle"] == "Creates a water task force."
    assert detail["sponsor"] == "Representative Smith, Senator Jones"
    assert detail["billStatus"] == "Chaptered"
    assert detail["lastAction"] == "Governor signed."
    assert detail["lastActionDate"] == "2025-03-05"
    assert detail["signedDate"] == "2025-03-05"
    assert detail["effectiveDate"] == "2025-07-01"
    assert detail["chapter"] == "12"
    assert detail["introduced"].endswith("/Introduced")
    assert detail["currentVersionPath"].endswith("/Enrolled")
    assert "Creates a water task force." in detail["summaryHTML"]
    assert "Governor signed." in detail["digestHTML"]
