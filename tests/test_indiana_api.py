from __future__ import annotations

import json
from typing import Any

import requests

from app.indiana_api import IndianaApiClient
from app.settings import get_settings


class FakeResponse:
    def __init__(self, url: str, *, json_data: object, status_code: int = 200) -> None:
        self.url = url
        self._json_data = json_data
        self.status_code = status_code
        self.headers = {"content-type": "application/json; charset=utf-8"}
        self.text = json.dumps(json_data)
        self.content = self.text.encode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} for {self.url}")

    def json(self) -> object:
        return self._json_data


class FakeSession:
    def __init__(self, get_responses: dict[str, FakeResponse]) -> None:
        self._get_responses = get_responses
        self.headers: dict[str, str] = {}

    def get(
        self,
        url: str,
        timeout: float | None = None,
    ) -> FakeResponse:
        if url not in self._get_responses:
            raise AssertionError(f"Unexpected GET request: {url}")
        return self._get_responses[url]

    def close(self) -> None:
        return None


def _settings_with_indiana_key() -> Any:
    settings = get_settings()
    return settings.__class__(**{**settings.__dict__, "indiana_api_key": "indiana-test-key"})


def test_fetch_year_bills_reads_official_indiana_index() -> None:
    settings = _settings_with_indiana_key()
    api = IndianaApiClient(settings)
    api.close()

    list_url = "https://api.iga.in.gov/2026/bills"
    api.client = FakeSession(
        {
            list_url: FakeResponse(
                list_url,
                json_data={
                    "itemCount": 2,
                    "items": [
                        {
                            "billName": "HB1001",
                            "displayName": "HB 1001",
                            "type": "BILL",
                            "description": "Housing matters.",
                            "link": "/2026/bills/hb1001",
                            "filed": "2026-01-07",
                            "active": True,
                        },
                        {
                            "billName": "SB2",
                            "displayName": "SB 2",
                            "type": "BILL",
                            "description": "School safety matters.",
                            "link": "/2026/bills/sb2",
                            "filed": "2026-01-08",
                            "active": True,
                        },
                    ],
                },
            )
        }
    )

    items = api.fetch_year_bills(2026)

    assert [item["billNum"] for item in items] == ["HB1001", "SB2"]
    assert items[0]["detailPath"] == "https://api.iga.in.gov/2026/bills/hb1001"
    assert items[0]["billStatus"] == "Active"
    assert items[1]["lastActionDate"] == "2026-01-08"


def test_fetch_bill_detail_reads_official_indiana_payload() -> None:
    settings = _settings_with_indiana_key()
    api = IndianaApiClient(settings)
    api.close()

    bill_url = "https://api.iga.in.gov/2026/bills/hb1001"
    version_one_url = "https://api.iga.in.gov/2026/bills/HB1001/versions/HB1001.01.INTR"
    version_two_url = "https://api.iga.in.gov/2026/bills/HB1001/versions/HB1001.02.ENRS"
    amendment_url = "https://api.iga.in.gov/2026/bills/hb1001/versions/hb1001.01.intr/amendments/HB1001.01.INTR.AMH001"
    actions_url = "https://api.iga.in.gov/2026/bills/hb1001/actions"

    api.client = FakeSession(
        {
            bill_url: FakeResponse(
                bill_url,
                json_data={
                    "title": "A BILL FOR AN ACT to amend the Indiana Code concerning housing.",
                    "billName": "HB1001",
                    "description": "Housing matters.",
                    "stage": "Enrolled House Bill (H)",
                    "committeeStatus": "In Committee",
                    "authors": [
                        {"position_title": "Representative", "firstName": "Doug", "lastName": "Miller", "fullName": "Representative Doug Miller"}
                    ],
                    "coauthors": [
                        {"position_title": "Representative", "firstName": "Tony", "lastName": "Isa", "fullName": "Representative Tony Isa"}
                    ],
                    "sponsors": [
                        {"position_title": "Senator", "firstName": "Chris", "lastName": "Garten", "fullName": "Senator Chris Garten"}
                    ],
                    "cosponsors": [],
                    "link": "/2026/bills/hb1001",
                    "actions": {"link": "/2026/bills/hb1001/actions"},
                    "latestVersion": {
                        "printVersion": "2",
                        "printVersionName": "HB1001.02.ENRS",
                        "stageVerbose": "Enrolled House Bill (H)",
                        "title": "A BILL FOR AN ACT to amend the Indiana Code concerning housing.",
                        "shortDescription": "Housing matters.",
                        "digest": "Creates and changes housing rules.",
                        "link": "/2026/bills/HB1001/versions/HB1001.02.ENRS",
                    },
                    "versions": [
                        {
                            "printVersion": "1",
                            "printVersionName": "HB1001.01.INTR",
                            "updated": "2026-01-08T09:48:54",
                            "link": "/2026/bills/HB1001/versions/HB1001.01.INTR",
                        },
                        {
                            "printVersion": "2",
                            "printVersionName": "HB1001.02.ENRS",
                            "updated": "2026-02-05T13:00:00",
                            "link": "/2026/bills/HB1001/versions/HB1001.02.ENRS",
                        },
                    ],
                },
            ),
            actions_url: FakeResponse(
                actions_url,
                json_data={
                    "items": [
                        {
                            "date": "2026-01-08T09:43:53",
                            "sequence": "1200",
                            "description": "First reading: referred to Committee on Local Government",
                            "chamber": {"name": "House"},
                        },
                        {
                            "date": "2026-02-05T15:00:00",
                            "sequence": "5000",
                            "description": "Public Law 12. Signed by the Governor.",
                            "chamber": {"name": "House"},
                        },
                    ]
                },
            ),
            version_one_url: FakeResponse(
                version_one_url,
                json_data={
                    "printVersionName": "HB1001.01.INTR",
                    "title": "A BILL FOR AN ACT to amend the Indiana Code concerning housing.",
                    "shortDescription": "Housing matters.",
                    "digest": "Introduced digest text.",
                    "amendments": [
                        {
                            "name": "HB1001.01.INTR.AMH001",
                            "state": "P",
                            "type": "second",
                            "author": {"fullName": "Representative Blake Johnson"},
                            "publishtime": "2026-01-22T07:45:00",
                            "link": "/2026/bills/hb1001/versions/hb1001.01.intr/amendments/HB1001.01.INTR.AMH001",
                        }
                    ],
                    "floor_amendments": [],
                    "cmte_amendments": [],
                },
            ),
            version_two_url: FakeResponse(
                version_two_url,
                json_data={
                    "printVersionName": "HB1001.02.ENRS",
                    "title": "A BILL FOR AN ACT to amend the Indiana Code concerning housing.",
                    "shortDescription": "Housing matters.",
                    "digest": "Creates and changes housing rules.",
                    "amendments": [],
                    "floor_amendments": [],
                    "cmte_amendments": [],
                },
            ),
            amendment_url: FakeResponse(
                amendment_url,
                json_data={
                    "name": "HB1001.01.INTR.AMH001",
                    "description": "Adds a housing grant change.",
                    "state": "P",
                    "type": "second",
                    "author": {"fullName": "Representative Blake Johnson"},
                    "link": "/2026/bills/hb1001/versions/hb1001.01.intr/amendments/HB1001.01.INTR.AMH001",
                },
            ),
        }
    )

    detail = api.fetch_bill_detail(
        bill_url,
        {"billNum": "HB1001", "detailPath": bill_url, "year": 2026},
    )

    assert detail["bill"] == "HB1001"
    assert detail["sponsor"] == "Representative Doug Miller"
    assert detail["billStatus"] == "Enrolled House Bill (H)"
    assert detail["lastAction"] == "Public Law 12. Signed by the Governor."
    assert detail["lastActionDate"] == "2026-02-05"
    assert detail["signedDate"] == "2026-02-05"
    assert detail["chapter"] == "12"
    assert detail["summary"] == bill_url
    assert detail["introduced"] == version_one_url
    assert detail["currentVersionPath"] == version_two_url
    assert "Housing matters." in detail["summaryHTML"]
    assert "Creates and changes housing rules." in detail["digestHTML"]
    assert detail["currentBillHTML"] == ""
    assert detail["amendments"][0]["documentUrl"] == amendment_url


def test_fetch_public_document_text_reads_official_version_and_amendment_payloads() -> None:
    settings = _settings_with_indiana_key()
    api = IndianaApiClient(settings)
    api.close()

    version_url = "https://api.iga.in.gov/2026/bills/HB1001/versions/HB1001.02.ENRS"
    amendment_url = "https://api.iga.in.gov/2026/bills/hb1001/versions/hb1001.01.intr/amendments/HB1001.01.INTR.AMH001"
    api.client = FakeSession(
        {
            version_url: FakeResponse(
                version_url,
                json_data={
                    "title": "A BILL FOR AN ACT to amend the Indiana Code concerning housing.",
                    "shortDescription": "Housing matters.",
                    "digest": "Creates and changes housing rules.",
                },
            ),
            amendment_url: FakeResponse(
                amendment_url,
                json_data={
                    "name": "HB1001.01.INTR.AMH001",
                    "description": "Adds a housing grant change.",
                    "state": "P",
                    "type": "second",
                    "author": {"fullName": "Representative Blake Johnson"},
                },
            ),
        }
    )

    version_text = api.fetch_public_document_text(version_url)
    amendment_text = api.fetch_public_document_text(amendment_url)

    assert "Creates and changes housing rules." in version_text
    assert "Representative Blake Johnson" in amendment_text
    assert "Adds a housing grant change." in amendment_text
