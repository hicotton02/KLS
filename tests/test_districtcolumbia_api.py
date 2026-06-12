from __future__ import annotations

import httpx

from app.districtcolumbia_api import (
    DistrictOfColumbiaApiClient,
    district_of_columbia_council_period,
)
from app.settings import get_settings


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        json_data: dict | None = None,
        text: str = "",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self._json_data = json_data or {}
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/json"}
        self.content = text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} for {self.url}",
                request=httpx.Request("GET", self.url),
                response=httpx.Response(self.status_code, request=httpx.Request("GET", self.url)),
            )

    def json(self) -> dict:
        return self._json_data


class FakeClient:
    def __init__(self, responses: dict[tuple[str, str], FakeResponse]) -> None:
        self._responses = responses

    def get(self, url: str, *args, **kwargs) -> FakeResponse:
        key = ("GET", str(url))
        if key not in self._responses:
            raise AssertionError(f"Unexpected GET request: {url}")
        return self._responses[key]

    def post(self, url: str, *args, **kwargs) -> FakeResponse:
        key = ("POST", str(url))
        if key not in self._responses:
            raise AssertionError(f"Unexpected POST request: {url}")
        return self._responses[key]

    def close(self) -> None:
        return None


def test_district_of_columbia_council_period_uses_session_start_year() -> None:
    assert district_of_columbia_council_period(2025) == 26
    assert district_of_columbia_council_period(2026) == 26


def test_fetch_year_bills_parses_district_of_columbia_search_results() -> None:
    settings = get_settings()
    api = DistrictOfColumbiaApiClient(settings)
    api.close()

    api.client = FakeClient(
        {
            ("POST", "/api/Search/LegislationSearch"): FakeResponse(
                "/api/Search/LegislationSearch",
                json_data={
                    "pagination": {"totalCount": 1},
                    "searchResults": {
                        "results": [
                            {
                                "legislationId": 56820,
                                "legislationNumber": "B26-0001",
                                "title": "Rent Stabilized Housing Inflation Protection Continuation Emergency Amendment Act of 2025",
                                "status": "Enacted",
                                "tag": "Enacted",
                                "introducers": [
                                    {
                                        "formalName": "R. White",
                                        "name": "White, Robert C. Jr.",
                                    }
                                ],
                                "introductionDate": "2025-01-06T00:00:00",
                                "legislationTextUrl": "/downloads/LIMS/56820/B26-0001.html",
                            }
                        ]
                    },
                },
            )
        }
    )

    items = api.fetch_year_bills(2025)

    assert [item["billNum"] for item in items] == ["B26-0001"]
    assert items[0]["detailPath"] == "https://lims.dccouncil.gov/Legislation/B26-0001"
    assert items[0]["sponsor"] == "R. White"


def test_fetch_bill_detail_parses_district_of_columbia_legislation_payload() -> None:
    settings = get_settings()
    api = DistrictOfColumbiaApiClient(settings)
    api.close()

    api.client = FakeClient(
        {
            ("GET", "/api/Search/GetLegislationDetails/B26-0001"): FakeResponse(
                "/api/Search/GetLegislationDetails/B26-0001",
                json_data={
                    "legislationNumber": "B26-0001",
                    "title": "Rent Stabilized Housing Inflation Protection Continuation Emergency Amendment Act of 2025",
                    "shortDescription": "Continues the emergency rent protection rules for a short time.",
                    "status": "Enacted",
                    "tag": "Enacted",
                    "legislationTextUrl": "/downloads/LIMS/56820/B26-0001.html",
                    "introducerSummary": {
                        "summaryDataList": [
                            {
                                "label": "Introduced by",
                                "content": 'Councilmember <a href="/introducerDetail/198/26">R. White</a>',
                            },
                            {
                                "label": "Committee Referral",
                                "content": "Retained by the Council",
                            },
                            {
                                "label": "Act Number",
                                "content": "A26-0003",
                            },
                        ]
                    },
                    "legislationHistory": [
                        {
                            "date": "Jan 06, 2025",
                            "sortDate": "2025-01-06T00:00:00",
                            "type": "Introduced",
                        },
                        {
                            "date": "Jan 10, 2025",
                            "sortDate": "2025-01-10T00:00:00",
                            "type": "Final Reading",
                        },
                    ],
                    "actNumber": "A26-0003",
                    "lawNumber": "",
                    "resolutionNumber": "",
                },
            ),
            ("GET", "https://lims.dccouncil.gov/downloads/LIMS/56820/B26-0001.html"): FakeResponse(
                "https://lims.dccouncil.gov/downloads/LIMS/56820/B26-0001.html",
                text="<html><body><p>This act continues protection for rent-stabilized housing.</p></body></html>",
                headers={"content-type": "text/html; charset=UTF-8"},
            ),
        }
    )

    detail = api.fetch_bill_detail("https://lims.dccouncil.gov/Legislation/B26-0001")

    assert detail["bill"] == "B26-0001"
    assert detail["billStatus"] == "Enacted"
    assert detail["sponsor"] == "R. White"
    assert detail["chapter"] == "A26-0003"
    assert "Committee Referral: Retained by the Council" in detail["digestHTML"]
    assert "rent-stabilized housing" in detail["currentBillHTML"]
