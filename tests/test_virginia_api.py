from __future__ import annotations

import httpx

from app.settings import get_settings
from app.virginia_api import VirginiaApiClient


def test_fetch_year_bills_parses_official_csv() -> None:
    settings = get_settings()
    api = VirginiaApiClient(settings)
    api.client.close()

    bills_csv = """\
"Bill_id","Bill_description","Patron_id","Patron_name","Last_house_committee_id","Last_house_action","Last_house_action_date","Last_senate_committee_id","Last_senate_action","Last_senate_action_date","Last_conference_action","Last_conference_action_date","Last_governor_action","Last_governor_action_date","Emergency","Passed_house","Passed_senate","Passed","Failed","Carried_over","Approved","Vetoed","Full_text_doc1","Full_text_date1","Full_text_doc2","Full_text_date2","Full_text_doc3","Full_text_date3","Full_text_doc4","Full_text_date4","Full_text_doc5","Full_text_date5","Full_text_doc6","Full_text_date6","Last_house_actid","Last_senate_actid","Last_conference_actid","Last_governor_actid","Chapter_id","Introduction_date","Last_actid"
"HB1","Minimum wage.","H0173","Ward","","Passed House","2/1/2026","","Passed Senate","2/20/2026","","","Acts of Assembly Chapter text (CHAP0350)","4/8/2026","N","Y","Y","Y","N","N","Y","N","HB1","11/17/2025","HB1ER","3/11/2026","CHAP0350","4/8/2026","","","","","","","","","","","","CHAP0350","11/17/2025","G9998"
"SB2","Health care access.","S0123","Lucas","","Referred to committee","1/12/2026","","","1/12/2026","","","","","N","N","N","N","N","N","N","N","SB2","11/20/2025","","","","","","","","","","","","","","","","","11/20/2025","S1401"
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Session/api/getDefaultSessionAsync"):
            return httpx.Response(200, json={"SessionCode": "20261"}, request=request)
        if request.url.host == "lis.blob.core.windows.net":
            return httpx.Response(200, text=bills_csv, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.virginia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        bills = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in bills] == ["HB1", "SB2"]
    assert bills[0]["billStatus"] == "Acts of Assembly Chapter text (CHAP0350)"
    assert bills[0]["lastActionDate"] == "2026-04-08"
    assert bills[0]["signedDate"] == "2026-04-08"
    assert bills[0]["chapter"] == "CHAP0350"
    assert bills[1]["lastAction"] == "Referred to committee"
    assert bills[1]["enrolledNumber"] == "SB2"


def test_fetch_bill_detail_builds_actions_and_amendments() -> None:
    settings = get_settings()
    api = VirginiaApiClient(settings)
    api.client.close()

    legislation_payload = {
        "Legislations": [
            {
                "LegislationID": 98529,
                "SessionCode": "20261",
                "LegislationStatus": "Governor's Recommendation",
                "LegislationNumber": "HB5",
                "Description": "Employment; paid sick leave, civil penalties.",
                "LegislationTitle": "An Act relating to paid sick leave.",
                "ChamberCode": "H",
                "ChapterNumber": "",
                "Patrons": [
                    {
                        "PatronDisplayName": "Convirs-Fowler",
                    }
                ],
            }
        ]
    }
    summary_payload = {
        "LegislationSummaries": [
            {
                "SummaryVersion": "SUMMARY AS PASSED",
                "SummaryDate": "2026-04-10T00:00:00",
                "Summary": "<p class=\"sumtext\"><b>Paid sick leave.</b> Requires paid sick leave and sets civil penalties.</p>",
                "IsActive": True,
            }
        ]
    }
    texts_payload = {
        "TextsList": [
            {
                "LegislationTextID": 268980,
                "LegislationVersionID": 10,
                "LegislationVersion": "Substitute",
                "Description": "Governor Substitute",
                "DocumentCode": "HB5H2",
                "DraftText": "<p>Governor substitute text.</p>",
                "DraftTitle": "An Act relating to paid sick leave.",
                "VersionDate": "2026-04-14T00:00:00",
                "ChamberCode": "H",
                "Sponsor": "Governor",
                "IsActive": True,
                "HTMLFile": [{"FileURL": "https://lis.blob.core.windows.net/files/HB5H2.HTML"}],
            },
            {
                "LegislationTextID": 268979,
                "LegislationVersionID": 9,
                "LegislationVersion": "Gov Recommendation",
                "Description": "Governor's Recommendation",
                "DocumentCode": "HB5G",
                "DraftText": "<p>Governor recommendation text.</p>",
                "VersionDate": "2026-04-12T00:00:00",
                "ChamberCode": "H",
                "Sponsor": "Governor",
                "IsActive": True,
                "HTMLFile": [{"FileURL": "https://lis.blob.core.windows.net/files/HB5G.HTML"}],
            },
            {
                "LegislationTextID": 266972,
                "LegislationVersionID": 8,
                "LegislationVersion": "Conference Report",
                "Description": "Conference Report",
                "DocumentCode": "HB5AC",
                "DraftText": "<p>Conference report text.</p>",
                "VersionDate": "2026-03-13T00:00:00",
                "ChamberCode": "H",
                "Sponsor": "House",
                "IsActive": False,
                "HTMLFile": [{"FileURL": "https://lis.blob.core.windows.net/files/HB5AC.HTML"}],
            },
            {
                "LegislationTextID": 257724,
                "LegislationVersionID": 1,
                "LegislationVersion": "Introduced",
                "Description": "Introduced",
                "DocumentCode": "HB5",
                "DraftText": "<p>Introduced text.</p>",
                "VersionDate": "2025-11-17T07:28:00",
                "ChamberCode": "H",
                "Sponsor": "House",
                "IsActive": False,
                "HTMLFile": [{"FileURL": "https://lis.blob.core.windows.net/files/HB5.HTML"}],
            },
        ]
    }
    history_payload = {
        "LegislationEvents": [
            {
                "EventDate": "2026-03-30T00:00:00",
                "Sequence": 5,
                "Description": "Enrolled Bill communicated to Governor",
                "ActorType": "House",
            },
            {
                "EventDate": "2026-04-12T00:00:00",
                "Sequence": 10,
                "Description": "Governor's Recommendation received by House",
                "ActorType": "Governor",
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Session/api/getDefaultSessionAsync"):
            return httpx.Response(200, json={"SessionCode": "20261"}, request=request)
        if request.url.path.endswith("/AdvancedLegislationSearch/api/GetLegislationListAsync"):
            return httpx.Response(200, json=legislation_payload, request=request)
        if request.url.path.endswith("/LegislationSummary/api/GetLegislationSummaryListAsync"):
            return httpx.Response(200, json=summary_payload, request=request)
        if request.url.path.endswith("/LegislationText/api/GetLegislationTextByIDAsync"):
            return httpx.Response(200, json=texts_payload, request=request)
        if request.url.path.endswith("/LegislationEvent/api/GetPublicLegislationEventHistoryListAsync"):
            return httpx.Response(200, json=history_payload, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.virginia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(2026, "HB5")
    finally:
        api.close()

    assert detail["bill"] == "HB5"
    assert detail["catchTitle"] == "Employment; paid sick leave, civil penalties."
    assert detail["sponsor"] == "Convirs-Fowler"
    assert detail["billStatus"] == "Governor's Recommendation"
    assert detail["lastAction"] == "Governor's Recommendation received by House"
    assert detail["lastActionDate"] == "2026-04-12"
    assert detail["currentVersionPath"] == "https://lis.blob.core.windows.net/files/HB5H2.HTML"
    assert detail["introduced"].endswith("/HB5.HTML")
    assert len(detail["billActions"]) == 2
    assert [item["amendmentNumber"] for item in detail["amendments"]] == ["HB5AC", "HB5G", "HB5H2"]


def test_fetch_bill_detail_falls_back_to_csv_item_when_detail_api_is_empty() -> None:
    settings = get_settings()
    api = VirginiaApiClient(settings)
    api.client.close()

    item = {
        "billNum": "SR93",
        "billType": "SR",
        "catchTitle": "Commending Karen Grady.",
        "billTitle": "Commending Karen Grady.",
        "sponsor": "Example",
        "billStatus": "Passed",
        "lastAction": "Agreed to by Senate",
        "lastActionDate": "2026-02-20",
        "enrolledNumber": "SR93",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/Session/api/getDefaultSessionAsync"):
            return httpx.Response(200, json={"SessionCode": "20261"}, request=request)
        if request.url.path.endswith("/AdvancedLegislationSearch/api/GetLegislationListAsync"):
            return httpx.Response(200, json={"Legislations": []}, request=request)
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    api.client = httpx.Client(
        base_url=settings.virginia_site_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        detail = api.fetch_bill_detail(2026, "SR93", item)
    finally:
        api.close()

    assert detail["bill"] == "SR93"
    assert detail["billStatus"] == "Passed"
    assert detail["lastAction"] == "Agreed to by Senate"
    assert detail["lastActionDate"] == "2026-02-20"
    assert detail["summaryHTML"] == "<p>Commending Karen Grady.</p>"
