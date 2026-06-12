from __future__ import annotations

import httpx

from app.settings import get_settings
from app.vermont_api import VermontApiClient


def test_fetch_year_bills_filters_to_selected_year() -> None:
    settings = get_settings()
    api = VermontApiClient(settings)
    api.close()

    payload = {
        "data": [
            {"BillNumber": "H.1", "Title": "Ethics updates", "year": "2025", "SortMeetingDate": "1/9/2025", "ActNo": "44"},
            {"BillNumber": "H.488", "Title": "Transportation program", "year": "2026", "SortMeetingDate": "3/19/2025", "ActNo": "43"},
            {"BillNumber": "S.126", "Title": "Health reform", "year": "2026", "SortMeetingDate": "1/15/2026", "ActNo": ""},
        ]
    }

    api.client = httpx.Client(
        base_url=settings.vermont_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload, request=request)),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["H.488", "S.126"]
    assert items[0]["detailPath"] == "https://legislature.vermont.gov/bill/status/2026/H.488"


def test_fetch_bill_detail_reads_text_links_and_status_rows() -> None:
    settings = get_settings()
    api = VermontApiClient(settings)
    api.close()

    detail_html = """
    <html><body>
      <div class="bill-title">H.488 (Act 43) An act relating to transportation planning</div>
      <dl class="summary-table">
        <dt>Sponsor(s)</dt>
        <dd><ul class="item-list"><li>House Committee on Transportation</li></ul></dd>
        <dt>Last Recorded Action</dt>
        <dd>Senate 6/3/2025 - House message: Governor approved bill on June 2, 2025</dd>
      </dl>
      <h5>Bill/Resolution Text</h5>
      <ul class="bill-path">
        <li><div><a href="/Documents/2026/Docs/BILLS/H-0488/H-0488%20As%20Introduced.pdf">As Introduced</a></div></li>
        <li><div>As Passed by Both House and Senate<br><a href="/Documents/2026/Docs/BILLS/H-0488/H-0488%20As%20Passed%20Official.pdf">Official</a> | <a href="/Documents/2026/Docs/BILLS/H-0488/H-0488%20As%20Passed%20Unofficial.pdf">Unofficial</a></div></li>
        <li><div><a href="/Documents/2026/Docs/ACTS/ACT043/ACT043%20As%20Enacted.pdf">As Enacted</a></div></li>
      </ul>
      <script>
        $(function() {
          var detailed_status_table = $('#bill-detailed-status-table').DataTable({
            "ajax": {
              "url": "bill/loadBillDetailedStatus/2026/657"
            }
          });
        });
      </script>
      <h1>H.488 (Act 43)</h1>
    </body></html>
    """
    detailed_status = {
        "data": [
            {"Sequence": 1, "StatusDate": "3/19/2025", "FullStatus": "Referred to <strong>Ways and Means</strong>", "Location": "In Committee"},
            {"Sequence": 2, "StatusDate": "6/3/2025", "FullStatus": "House message: Governor approved bill on <strong>June 2, 2025</strong>", "Location": "Governor"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bill/status/2026/H.488"):
            return httpx.Response(200, text=detail_html, request=request)
        if request.url.path.endswith("/bill/loadBillDetailedStatus/2026/657"):
            return httpx.Response(200, json=detailed_status, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.vermont_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://legislature.vermont.gov/bill/status/2026/H.488", {"actNo": "43"})
    finally:
        api.close()

    assert detail["bill"] == "H.488"
    assert detail["catchTitle"] == "An act relating to transportation planning"
    assert detail["sponsor"] == "House Committee on Transportation"
    assert detail["lastAction"] == "House message: Governor approved bill on June 2, 2025"
    assert detail["lastActionDate"] == "2025-06-03"
    assert detail["signedDate"] == "2025-06-03"
    assert detail["chapter"] == "43"
    assert detail["introduced"].endswith("H-0488%20As%20Introduced.pdf")
    assert detail["currentVersionPath"].endswith("ACT043%20As%20Enacted.pdf")
    assert len(detail["billActions"]) == 2
