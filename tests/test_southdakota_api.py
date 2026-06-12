from __future__ import annotations

import httpx

from app.settings import get_settings
from app.southdakota_api import SouthDakotaApiClient


def test_fetch_year_bills_reads_bill_status_report() -> None:
    settings = get_settings()
    api = SouthDakotaApiClient(settings)
    api.close()

    sessions_json = [
        {"SessionId": 71, "Year": "2026", "YearString": "2026", "SpecialSession": False},
    ]
    bill_status_json = [
        {
            "BillId": 26699,
            "BillType": "HB",
            "BillNumberOnly": 1001,
            "Title": "provide for prescribed burning of state-owned land.",
            "ActionLogs": [
                {
                    "StatusText": "First read in House and referred to",
                    "AssignedCommittee": {"FullName": "House Agriculture and Natural Resources"},
                    "ActionCommittee": {"FullName": "House of Representatives"},
                    "ActionDate": "2026-01-13T12:00:00-06:00",
                }
            ],
        },
        {
            "BillId": 26722,
            "BillType": "HB",
            "BillNumberOnly": 1004,
            "Title": "authorize the recall of county commissioners.",
            "ActionLogs": [
                {
                    "StatusText": "Signed by the Governor",
                    "AssignedCommittee": None,
                    "ActionCommittee": {"FullName": "House of Representatives"},
                    "ActionDate": "2026-03-09T14:00:00-05:00",
                }
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/Sessions":
            return httpx.Response(200, json=sessions_json, request=request)
        if request.url.path == "/api/Bills/BillStatus/71":
            return httpx.Response(200, json=bill_status_json, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.site_client = httpx.Client(
        base_url=settings.south_dakota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1001", "HB1004"]
    assert items[0]["lastActionDate"] == "2026-01-13"
    assert items[1]["billStatus"] == "Signed by the Governor"
    assert items[1]["detailPath"] == "https://sdlegislature.gov/Session/Bill/26722"


def test_fetch_bill_detail_extracts_actions_versions_and_amendments() -> None:
    settings = get_settings()
    api = SouthDakotaApiClient(settings)
    api.close()

    sessions_json = [
        {"SessionId": 71, "Year": "2026", "YearString": "2026", "SpecialSession": False},
    ]
    detail_json = {
        "BillId": 26722,
        "BillType": "HB",
        "BillNumber": 1004,
        "Title": "authorize the recall of county commissioners.",
        "SessionId": 71,
        "BillSponsor": [
            {
                "SessionMemberId": 4767,
                "MemberType": "H",
                "SponsorType": "P",
                "Member": {"UniqueName": "Ismay"},
            },
            {
                "SessionMemberId": 4756,
                "MemberType": "S",
                "SponsorType": "C",
                "Member": {"UniqueName": "Grove"},
            },
        ],
        "BillCommitteeSponsor": "",
        "Keywords": [{"Keyword": "Counties"}, {"Keyword": "Recall"}],
    }
    action_json = [
        {
            "StatusText": "First read in House and referred to",
            "AssignedCommittee": {"FullName": "House Local Government"},
            "ActionCommittee": {"FullName": "House of Representatives"},
            "ActionDate": "2026-01-13T12:00:00-06:00",
        },
        {
            "StatusText": "Signed by the Governor",
            "AssignedCommittee": None,
            "ActionCommittee": {"FullName": "House of Representatives"},
            "ActionDate": "2026-03-09T14:00:00-05:00",
        },
    ]
    versions_json = [
        {"DocumentId": 291307, "BillVersion": "Introduced", "DocumentDate": "2025-12-19T16:31:05.527-06:00"},
        {"DocumentId": 305073, "BillVersion": "Enrolled", "DocumentDate": "2026-02-26T12:08:40.447-06:00"},
    ]
    amendments_json = [
        {
            "DocumentId": 299104,
            "Filename": "1004G",
            "BillVersion": "Introduced",
            "Result": "Adopted",
        }
    ]
    fiscal_json = [
        {
            "BillType": "HB",
            "BillNumber": 1004,
            "DocumentId": 302832,
            "Version": "A",
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/Sessions":
            return httpx.Response(200, json=sessions_json, request=request)
        if request.url.path == "/api/Bills/26722":
            return httpx.Response(200, json=detail_json, request=request)
        if request.url.path == "/api/Bills/ActionLog/26722":
            return httpx.Response(200, json=action_json, request=request)
        if request.url.path == "/api/Bills/Versions/26722":
            return httpx.Response(200, json=versions_json, request=request)
        if request.url.path == "/api/Bills/Amendments/26722":
            return httpx.Response(200, json=amendments_json, request=request)
        if request.url.path == "/api/Bills/FiscalNotes/26722":
            return httpx.Response(200, json=fiscal_json, request=request)
        if request.url.path == "/api/Bills/PrisonJail/26722":
            return httpx.Response(200, json=[], request=request)
        if request.url.path == "/api/ConferenceCommittees/Bill/26722":
            return httpx.Response(200, json=[], request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.site_client = httpx.Client(
        base_url=settings.south_dakota_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail("https://sdlegislature.gov/Session/Bill/26722")
    finally:
        api.close()

    assert detail["bill"] == "HB1004"
    assert detail["sponsor"] == "Ismay"
    assert detail["sponsorStringHouse"] == "Ismay"
    assert detail["sponsorStringSenate"] == "Grove"
    assert detail["billStatus"] == "Signed by the Governor"
    assert detail["lastActionDate"] == "2026-03-09"
    assert detail["signedDate"] == "2026-03-09"
    assert detail["introduced"] == "https://mylrc.sdlegislature.gov/api/Documents/291307.pdf?Year=2026"
    assert detail["digest"] == "https://mylrc.sdlegislature.gov/api/Documents/302832.pdf?Year=2026"
    assert detail["currentVersionPath"] == "https://mylrc.sdlegislature.gov/api/Documents/305073.pdf?Year=2026"
    assert len(detail["billActions"]) == 2
    assert len(detail["amendments"]) == 1
    assert detail["amendments"][0]["documentUrl"] == "https://mylrc.sdlegislature.gov/api/Documents/299104.pdf?Year=2026"
    assert "Official fiscal and corrections notes" in detail["digestHTML"]
