from __future__ import annotations

from typing import Any

import httpx

from app.http_retry import get_with_retries, post_with_retries
from app.settings import Settings
from app.text_utils import html_to_text, pdf_bytes_to_text


LIST_FIELDS = ",".join(
    [
        "BillNum",
        "ShortTitle",
        "Year",
        "ChapterNo",
        "Sponsor",
        "EnrolledNo",
        "LastActionDate",
        "LastAction",
        "SignedDate",
        "EffectiveDate",
        "BillType",
        "SpecialSessionValue",
        "BillStatus",
    ]
)


class WyomingApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.wyoming_api_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
        )
        self._historical_roster_cache: dict[int, list[dict[str, Any]]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int, special_session_value: int | None = None) -> list[dict[str, Any]]:
        filter_parts = [f"Year eq {year}"]
        if special_session_value is not None:
            filter_parts.append(f"SpecialSessionValue eq {special_session_value}")
        params = {
            "$select": LIST_FIELDS,
            "$filter": " and ".join(filter_parts),
            "$orderby": "SpecialSessionValue,BillNum",
        }
        response = get_with_retries(self.client, "/BillInformation", params=params)
        response.raise_for_status()
        return response.json()

    def fetch_bill_detail(self, year: int, bill_num: str, special_session_value: int | None = None) -> dict[str, Any]:
        path = f"/BillInformation/{year}/{bill_num}"
        if special_session_value is not None:
            path += f"/{special_session_value}"
        response = get_with_retries(self.client, path)
        response.raise_for_status()
        return response.json()

    def fetch_legislators(self, year: int, chamber: str) -> list[dict[str, Any]]:
        response = get_with_retries(self.client, f"/legislator/{year}/{chamber}")
        response.raise_for_status()
        current_payload = response.json()
        current_legislators = current_payload if isinstance(current_payload, list) else []

        historical_legislators = self._historical_roster_cache.get(year)
        if historical_legislators is None:
            historical_response = post_with_retries(
                self.client,
                "/legislator/search",
                json={"serviceYearStart": str(year), "serviceYearEnd": str(year)},
            )
            historical_response.raise_for_status()
            historical_payload = historical_response.json()
            historical_legislators = [
                item
                for raw_item in (historical_payload if isinstance(historical_payload, list) else [])
                if (item := self._normalize_historical_legislator(raw_item)) is not None
            ]
            self._historical_roster_cache[year] = historical_legislators

        selected_chamber = chamber.strip().upper()
        merged: dict[str, dict[str, Any]] = {}
        for item in historical_legislators:
            if str(item.get("district") or "").upper().startswith(selected_chamber):
                merged[str(item.get("legID") or item.get("name"))] = dict(item)
        for item in current_legislators:
            if not isinstance(item, dict):
                continue
            key = str(item.get("legID") or item.get("name"))
            base = merged.get(key, {})
            merged[key] = {**base, **{field: value for field, value in item.items() if value not in (None, "")}}
        return sorted(merged.values(), key=lambda item: str(item.get("name") or "").casefold())

    @staticmethod
    def _normalize_historical_legislator(raw_item: object) -> dict[str, Any] | None:
        if not isinstance(raw_item, dict):
            return None
        raw_name = str(raw_item.get("name") or "").strip()
        parts = [part.strip() for part in raw_name.split(",") if part.strip()]
        if len(parts) < 2:
            return None

        suffixes = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
        last_name = parts.pop(0)
        suffix = ""
        if parts and parts[0].casefold() in suffixes:
            suffix = parts.pop(0)
        elif parts and parts[-1].casefold() in suffixes:
            suffix = parts.pop()
        given_names = " ".join(parts).strip()
        first_name = given_names.split()[0] if given_names else ""
        display_name = " ".join(part for part in (given_names, last_name, suffix) if part)
        return {
            "firstName": first_name,
            "lastName": last_name,
            "name": display_name,
            "legID": raw_item.get("legID"),
            "party": None,
            "district": raw_item.get("district"),
        }

    def public_document_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return f"{self.settings.wyoming_site_base.rstrip('/')}/{path.lstrip('/')}"

    def public_bill_url(self, year: int, bill_num: str, special_session_value: int | None = None) -> str:
        url = f"{self.settings.wyoming_site_base.rstrip('/')}/Legislation/{year}/{bill_num}"
        if special_session_value is not None:
            url += f"?specialSessionValue={special_session_value}"
        return url

    def public_amendment_url(self, year: int, amendment_number: str, extension: str = "pdf") -> str:
        normalized_extension = extension.lstrip(".")
        return f"{self.settings.wyoming_site_base.rstrip('/')}/{year}/Amends/{amendment_number}.{normalized_extension}"

    def fetch_public_document_text(self, url: str | None) -> str:
        if not url:
            return ""
        response = get_with_retries(self.client, url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if url.lower().endswith(".pdf") or "pdf" in content_type:
            return pdf_bytes_to_text(response.content)
        return html_to_text(response.text)
