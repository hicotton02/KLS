from __future__ import annotations

import requests

from app.newyork_api import NewYorkApiClient
from app.settings import get_settings


class FakeResponse:
    def __init__(
        self,
        url: str,
        *,
        json_data: dict | None = None,
        status_code: int = 200,
    ) -> None:
        self.url = url
        self._json_data = json_data or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")

    def json(self) -> dict:
        return self._json_data


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self._responses = responses
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: float | None = None) -> FakeResponse:
        if url not in self._responses:
            raise AssertionError(f"Unexpected GET request: {url}")
        return self._responses[url]

    def close(self) -> None:
        return None


def test_fetch_year_bills_parses_new_york_api_search_results(monkeypatch) -> None:
    monkeypatch.setenv("KLS_NEW_YORK_API_KEY", "test-key")
    get_settings.cache_clear()

    settings = get_settings()
    api = NewYorkApiClient(settings)
    api.close()

    search_url = (
        "https://legislation.nysenate.gov/api/3/bills/2025/search"
        "?term=%2A&limit=100&sort=printNo.keyword%3AASC&offset=1&key=test-key"
    )
    api.scraper = FakeSession(
        {
            search_url: FakeResponse(
                search_url,
                json_data={
                    "total": 2,
                    "offsetEnd": 2,
                    "result": {
                        "items": [
                            {
                                "result": {
                                    "printNo": "A100",
                                    "title": "Requires electronic cigarette packaging warnings.",
                                    "summary": "Requires electronic cigarette packaging warnings.",
                                    "status": {
                                        "statusDesc": "In Assembly Committee",
                                        "actionDate": "2026-01-07",
                                        "committeeName": "Health",
                                    },
                                    "sponsor": {
                                        "member": {
                                            "fullName": "Linda Rosenthal",
                                        }
                                    },
                                    "actions": {
                                        "items": [
                                            {
                                                "date": "2026-01-07",
                                                "chamber": "ASSEMBLY",
                                                "text": "REFERRED TO HEALTH",
                                            }
                                        ]
                                    },
                                }
                            },
                            {
                                "result": {
                                    "printNo": "S9992",
                                    "title": "Requires API access to health information.",
                                    "summary": "Requires API access to health information.",
                                    "status": {
                                        "statusDesc": "In Senate Committee",
                                        "actionDate": "2026-04-21",
                                        "committeeName": "Insurance",
                                    },
                                    "sponsor": {
                                        "member": {
                                            "fullName": "Shelley Mayer",
                                        }
                                    },
                                    "actions": {
                                        "items": [
                                            {
                                                "date": "2026-04-21",
                                                "chamber": "SENATE",
                                                "text": "REFERRED TO INSURANCE",
                                            }
                                        ]
                                    },
                                }
                            },
                        ],
                    }
                },
            )
        }
    )

    items = api.fetch_year_bills(2025)

    assert [item["billNum"] for item in items] == ["A100", "S9992"]
    assert items[0]["detailPath"] == "https://www.nysenate.gov/legislation/bills/2025/A100"
    assert items[0]["sponsor"] == "Linda Rosenthal"
    assert items[0]["lastAction"] == "REFERRED TO HEALTH"


def test_fetch_year_bills_merges_descending_window_after_offset_boundary(monkeypatch) -> None:
    monkeypatch.setenv("KLS_NEW_YORK_API_KEY", "test-key")
    get_settings.cache_clear()

    settings = get_settings()
    api = NewYorkApiClient(settings)
    api.close()

    asc_first = (
        "https://legislation.nysenate.gov/api/3/bills/2025/search"
        "?term=%2A&limit=100&sort=printNo.keyword%3AASC&offset=1&key=test-key"
    )
    asc_boundary = (
        "https://legislation.nysenate.gov/api/3/bills/2025/search"
        "?term=%2A&limit=100&sort=printNo.keyword%3AASC&offset=3&key=test-key"
    )
    desc_first = (
        "https://legislation.nysenate.gov/api/3/bills/2025/search"
        "?term=%2A&limit=100&sort=printNo.keyword%3ADESC&offset=1&key=test-key"
    )
    api.scraper = FakeSession(
        {
            asc_first: FakeResponse(
                asc_first,
                json_data={
                    "total": 3,
                    "offsetEnd": 2,
                    "result": {
                        "items": [
                            {"result": {"printNo": "A1", "title": "Assembly one"}},
                            {"result": {"printNo": "A2", "title": "Assembly two"}},
                        ],
                    },
                },
            ),
            asc_boundary: FakeResponse(asc_boundary, status_code=400),
            desc_first: FakeResponse(
                desc_first,
                json_data={
                    "total": 3,
                    "offsetEnd": 3,
                    "result": {
                        "items": [
                            {"result": {"printNo": "S1", "title": "Senate one"}},
                            {"result": {"printNo": "A2", "title": "Assembly two"}},
                        ],
                    },
                },
            ),
        }
    )

    items = api.fetch_year_bills(2025)

    assert [item["billNum"] for item in items] == ["A1", "A2", "S1"]


def test_fetch_bill_detail_parses_new_york_api_payloads(monkeypatch) -> None:
    monkeypatch.setenv("KLS_NEW_YORK_API_KEY", "test-key")
    get_settings.cache_clear()

    settings = get_settings()
    api = NewYorkApiClient(settings)
    api.close()

    public_url = "https://www.nysenate.gov/legislation/bills/2025/S9992"
    detail_url = "https://legislation.nysenate.gov/api/3/bills/2025/S9992?view=default&key=test-key"
    full_text_url = (
        "https://legislation.nysenate.gov/api/3/bills/2025/S9992"
        "?view=only_fulltext&fullTextFormat=PLAIN&key=test-key"
    )
    api.scraper = FakeSession(
        {
            detail_url: FakeResponse(
                detail_url,
                json_data={
                    "result": {
                        "printNo": "S9992",
                        "title": "Requires API to facilitate patient and provider access to health information",
                        "summary": "Requires insurance companies to establish and maintain API access.",
                        "status": {
                            "statusDesc": "In Senate Committee",
                            "actionDate": "2026-04-21",
                            "committeeName": "Insurance",
                        },
                        "sponsor": {
                            "member": {
                                "fullName": "Shelley Mayer",
                            }
                        },
                        "actions": {
                            "items": [
                                {
                                    "date": "2026-04-21",
                                    "chamber": "SENATE",
                                    "text": "REFERRED TO INSURANCE",
                                }
                            ]
                        },
                        "approvalMessage": "",
                        "vetoMessages": [],
                        "activeVersion": "Original",
                        "signed": False,
                    }
                },
            ),
            full_text_url: FakeResponse(
                full_text_url,
                json_data={
                    "result": {
                        "fullText": "STATE OF NEW YORK\nIN SENATE\nAN ACT to require API access."
                    }
                },
            ),
        }
    )

    detail = api.fetch_bill_detail(
        public_url,
        {
            "billNum": "S9992",
            "sponsor": "Shelley Mayer",
        },
    )

    assert detail["bill"] == "S9992"
    assert detail["billStatus"] == "In Senate Committee"
    assert detail["lastAction"] == "REFERRED TO INSURANCE"
    assert detail["lastActionDate"] == "2026-04-21"
    assert detail["sponsor"] == "Shelley Mayer"
    assert detail["currentVersionPath"] == public_url
    assert "Committee: Insurance" in detail["digestHTML"]
    assert "STATE OF NEW YORK" in detail["currentBillHTML"]
