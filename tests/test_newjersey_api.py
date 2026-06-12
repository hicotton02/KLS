from __future__ import annotations

import httpx

from app.newjersey_api import NewJerseyApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_all_bills_endpoint() -> None:
    settings = get_settings()
    api = NewJerseyApiClient(settings)
    api.close()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/billSearch/allBills/2026"
        return httpx.Response(
            200,
            json=[
                [
                    {
                        "BillType": "A  ",
                        "BillNumber": 101,
                        "Bill": "A101   ",
                        "GovernorAction": None,
                        "Synopsis": "Requires NJT to equip trains with defibrillators.",
                    },
                    {
                        "BillType": "SJR",
                        "BillNumber": 12,
                        "Bill": "SJR12",
                        "GovernorAction": "Approved P.L.2026, c.55",
                        "Synopsis": "Designates a State observance day.",
                    },
                ],
                [{"BillCount": 2}],
            ],
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.new_jersey_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["A101", "SJR12"]
    assert items[0]["catchTitle"] == "Requires NJT to equip trains with defibrillators."
    assert items[1]["chapter"] == "55"
    assert items[1]["detailPath"] == "https://www.njleg.state.nj.us/bill-search/2026/SJR12"


def test_fetch_bill_detail_reads_description_history_sponsors_and_text() -> None:
    settings = get_settings()
    api = NewJerseyApiClient(settings)
    api.close()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/billSearch/sessions":
            return httpx.Response(200, json=[{"display": "2026-2027 Session", "value": 2026}], request=request)
        if request.url.path == "/api/billDetail/billDescription/A101/2026":
            return httpx.Response(
                200,
                json=[
                    {
                        "Synopsis": "Requires NJT to equip trains with defibrillators.",
                        "Code_Description": "Transportation and Independent Authorities",
                        "FiscalNote": "This bill has been certified by OLS for a fiscal note.",
                        "CurrentStatus": "ATR",
                        "ActualBillNumber": "A101",
                    }
                ],
                request=request,
            )
        if request.url.path == "/api/billDetail/billHistory/A101/2026":
            return httpx.Response(
                200,
                json=[
                    {"ActionDate": "1/13/2026", "HistoryAction": "Introduced, Referred to Assembly Transportation and Independent Authorities Committee"},
                    {"ActionDate": "6/30/2026", "HistoryAction": "Approved P.L.2026, c.55."},
                ],
                request=request,
            )
        if request.url.path == "/api/billDetail/billSponsors/A101/2026":
            return httpx.Response(
                200,
                json=[
                    [{"Full_Name": "Barlas, Al", "BioLink": "/legislative-roster/494/assemblyman-barlas"}],
                    [{"Full_Name": "Dunn, Aura K.", "BioLink": "/legislative-roster/428/assemblywoman-dunn"}],
                ],
                request=request,
            )
        if request.url.path == "/api/billDetail/billText/A101/2026":
            return httpx.Response(
                200,
                json=[
                    {"Description": "Introduced", "HTML_Link": "/Bills/2026/A0500/101_I1.HTM", "PDFLink": "/Bills/2026/A0500/101_I1.PDF"},
                    {"Description": "Chaptered", "HTML_Link": "/Bills/2026/A0500/101_CH1.HTM", "PDFLink": "/Bills/2026/A0500/101_CH1.PDF"},
                ],
                request=request,
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.new_jersey_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(
            "https://www.njleg.state.nj.us/bill-search/2026/A101",
            {"billNum": "A101", "lastAction": "Approved P.L.2026, c.55."},
        )
    finally:
        api.close()

    assert detail["bill"] == "A101"
    assert detail["sponsor"] == "Barlas, Al"
    assert detail["billStatus"] == "Approved P.L.2026, c.55."
    assert detail["lastActionDate"] == "2026-06-30"
    assert detail["signedDate"] == "2026-06-30"
    assert detail["chapter"] == "55"
    assert detail["introduced"] == "https://www.njleg.state.nj.us/Bills/2026/A0500/101_I1.HTM"
    assert detail["currentVersionPath"] == "https://www.njleg.state.nj.us/Bills/2026/A0500/101_CH1.HTM"
    assert detail["digest"] == "https://www.njleg.state.nj.us/Bills/2026/A0500/101_CH1.PDF"
    assert "Transportation and Independent Authorities" in detail["summaryHTML"]
    assert len(detail["billActions"]) == 2
