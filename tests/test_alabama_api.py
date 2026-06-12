from __future__ import annotations

import json

import httpx

from app.alabama_api import AlabamaApiClient
from app.settings import get_settings


def test_fetch_year_bills_reads_all_pages_and_checks_count() -> None:
    settings = get_settings()
    api = AlabamaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode("utf-8"))
        query = payload.get("query", "")
        variables = payload.get("variables", {})
        if "sessionByAbbreviation" in query:
            return httpx.Response(
                200,
                json={"data": {"session": {"abbreviation": "2026RS", "name": "2026 Regular Session"}}},
                request=request,
            )
        if variables.get("offset") == 0:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "instruments": {
                            "count": 2,
                            "data": [
                                {
                                    "instrumentNbr": "HB1",
                                    "shortTitle": "First bill",
                                    "subject": "Education",
                                    "sponsor": "Smith",
                                    "currentStatus": "Pending Committee Action in Second House",
                                    "lastAction": "Pending Committee Action in Second House",
                                    "actSummary": None,
                                    "viewEnacted": None,
                                    "companionInstrumentNbr": None,
                                    "effectiveDateCertain": None,
                                    "effectiveDateOther": None,
                                }
                            ],
                        }
                    }
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "instruments": {
                        "count": 2,
                        "data": [
                            {
                                "instrumentNbr": "HB2",
                                "shortTitle": "Second bill",
                                "subject": "Budget",
                                "sponsor": "Jones",
                                "currentStatus": "Enacted",
                                "lastAction": "Enacted",
                                "actSummary": None,
                                "viewEnacted": "https://example.test/act/2",
                                "companionInstrumentNbr": None,
                                "effectiveDateCertain": None,
                                "effectiveDateOther": None,
                            }
                        ],
                    }
                }
            },
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.alabama_api_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        items = api.fetch_year_bills(2026)
    finally:
        api.close()

    assert [item["billNum"] for item in items] == ["HB1", "HB2"]
    assert items[1]["billStatus"] == "Enacted"


def test_fetch_year_bills_raises_when_source_count_does_not_match() -> None:
    settings = get_settings()
    api = AlabamaApiClient(settings)
    api.client.close()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read().decode("utf-8"))
        query = payload.get("query", "")
        if "sessionByAbbreviation" in query:
            return httpx.Response(
                200,
                json={"data": {"session": {"abbreviation": "2026RS", "name": "2026 Regular Session"}}},
                request=request,
            )
        return httpx.Response(
            200,
            json={"data": {"instruments": {"count": 2, "data": [{"instrumentNbr": "HB1", "shortTitle": "Only bill"}]}}},
            request=request,
        )

    api.client = httpx.Client(
        base_url=settings.alabama_api_base,
        follow_redirects=True,
        transport=httpx.MockTransport(handler),
    )

    try:
        try:
            api.fetch_year_bills(2026)
        except RuntimeError as exc:
            assert "source count mismatch" in str(exc)
        else:
            raise AssertionError("Expected Alabama count mismatch to raise")
    finally:
        api.close()
