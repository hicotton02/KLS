from __future__ import annotations

import httpx

from app.massachusetts_api import MassachusettsApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_refined_search_pages() -> None:
    settings = get_settings()
    api = MassachusettsApiClient(settings)
    api.close()

    initial_search_html = """
    <html><body>
      <div data-refinername="lawsgeneralcourt">
        <div class="checkbox">
          <label>
            <input type="checkbox" data-refinertoken="3139347468202843757272656e7429" />
            194th (Current) (26)
          </label>
        </div>
      </div>
    </body></html>
    """
    page_one_html = """
    <html><body>
      <div>Showing results 1 to 25 of <em>about 26 results.</em></div>
      <table id="searchTable"><tbody>
        <tr>
          <td></td>
          <td><a href="/Bills/194/H101">H.101</a></td>
          <td>Danillo A. Sena</td>
          <td><a href="/Bills/194/H101">An Act establishing free broadband internet access in public housing</a></td>
        </tr>
        <tr>
          <td></td>
          <td><a href="/Bills/194/S12">S.12</a></td>
          <td>Jane Example</td>
          <td><a href="/Bills/194/S12">An Act relative to water quality</a></td>
        </tr>
      </tbody></table>
    </body></html>
    """
    page_two_html = """
    <html><body>
      <div>Showing results 26 to 26 of <em>about 26 results.</em></div>
      <table id="searchTable"><tbody>
        <tr>
          <td></td>
          <td><a href="/Bills/194/H102">H.102</a></td>
          <td>Mary Smith</td>
          <td><a href="/Bills/194/H102">An Act relative to internet affordability</a></td>
        </tr>
      </tbody></table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Bills/Search" and request.url.params.get("Page") is None:
            return httpx.Response(200, text=initial_search_html, request=request)
        if request.url.path == "/Bills/Search" and request.url.params.get("Page") == "1":
            return httpx.Response(200, text=page_one_html, request=request)
        if request.url.path == "/Bills/Search" and request.url.params.get("Page") == "2":
            return httpx.Response(200, text=page_two_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.massachusetts_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2025)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["H101", "H102", "S12"]
    assert items[0]["detailPath"] == "https://malegislature.gov/Bills/194/H101"
    assert items[2]["sponsor"] == "Jane Example"


def test_fetch_bill_detail_extracts_title_presenter_status_and_history() -> None:
    settings = get_settings()
    api = MassachusettsApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <div id="contentContainer">
        <div class="content">
          <h1>Bill H.101 194th (Current)</h1>
          <h2>Search the Legislature</h2>
          <h2>An Act establishing free broadband internet access in public housing</h2>
          <p id="pinslip">By Representative Sena of Acton, a petition (accompanied by bill, House, No. 101) of Danillo A. Sena and others for an investigation by the Department of Public Health of broadband internet access and its relation to the public health objectives.</p>
          <dl class="list-unstyled billInfo">
            <dt>Presenter:</dt>
            <dd><a href="/Legislators/Profile/DAS1">Danillo A. Sena</a></dd>
            <dt>Status:</dt>
            <dd>Referred to <a href="/Committees/Detail/H34">House Committee on Ways and Means</a></dd>
          </dl>
        </div>
      </div>
    </body></html>
    """
    history_html = """
    <html><body>
      <table>
        <tbody>
          <tr><td>2/27/2025</td><td>House</td><td>Referred to the committee on Advanced Information Technology, the Internet and Cybersecurity</td></tr>
          <tr><td>7/10/2025</td><td>Governor</td><td>Signed by the Governor, Chapter 55 of the Acts of 2025</td></tr>
        </tbody>
      </table>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/Bills/194/H101":
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path == "/Bills/194/H101/BillHistory":
            return httpx.Response(200, text=history_html, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.massachusetts_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://malegislature.gov/Bills/194/H101", {"billNum": "H101", "year": 2025})
    finally:
        api.close()

    assert detail["bill"] == "H101"
    assert detail["catchTitle"] == "An Act establishing free broadband internet access in public housing"
    assert detail["sponsor"] == "Danillo A. Sena"
    assert detail["billStatus"] == "Referred to House Committee on Ways and Means"
    assert detail["lastActionDate"] == "2025-07-10"
    assert detail["signedDate"] == "2025-07-10"
    assert detail["chapter"] == "55"
    assert detail["introduced"] == "https://malegislature.gov/Bills/194/H101/House/Bill/Text"
    assert detail["digest"] == "https://malegislature.gov/Bills/194/H101.pdf"
    assert len(detail["billActions"]) == 2
