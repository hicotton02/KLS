from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.settings import Settings
from app.text_utils import html_to_text


TEXT_FORMAT_PRIORITY = {
    "Formatted Text": 0,
    "Formatted XML": 1,
    "PDF": 2,
}

CONGRESS_GOV_BILL_TYPE_SLUGS = {
    "HR": "house-bill",
    "S": "senate-bill",
    "HJRES": "house-joint-resolution",
    "SJRES": "senate-joint-resolution",
    "HCONRES": "house-concurrent-resolution",
    "SCONRES": "senate-concurrent-resolution",
    "HRES": "house-resolution",
    "SRES": "senate-resolution",
}

SPONSOR_PARTY_SUFFIX = re.compile(r"\s*\[[^\]]+\]\s*$")


class _InventoryOverlap(ValueError):
    pass


def congress_bill_identifier(bill_type: str, number: str | int) -> str:
    return f"{str(bill_type or '').upper().strip()}{str(number or '').strip()}"


def congress_bill_number_part(bill_num: str, bill_type: str | None) -> str:
    normalized_bill_num = str(bill_num or "").strip()
    normalized_type = str(bill_type or "").strip().upper()
    if normalized_type and normalized_bill_num.upper().startswith(normalized_type):
        suffix = normalized_bill_num[len(normalized_type) :].strip()
        if suffix:
            return suffix
    digits = "".join(character for character in normalized_bill_num if character.isdigit())
    return digits or normalized_bill_num


def congress_bill_public_url(congress: int, bill_type: str, number: str | int) -> str:
    slug = CONGRESS_GOV_BILL_TYPE_SLUGS.get(str(bill_type or "").strip().upper())
    normalized_number = str(number or "").strip()
    if not slug or not normalized_number:
        return "https://www.congress.gov"
    return f"https://www.congress.gov/bill/{_ordinal(congress)}-congress/{slug}/{normalized_number}"


class CongressApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.congress_api_base.rstrip("/"),
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        self.client.close()

    def fetch_recent_bills(self, congress: int, limit: int) -> list[dict[str, Any]]:
        payload = self._request_json(
            f"/bill/{congress}",
            params={
                "limit": max(1, min(limit, 250)),
                "sort": "updateDate+desc",
            },
        )
        return self._extract_items(payload, "bills", singular_key="bill")

    def fetch_bill_catalog(self, congress: int, *, heartbeat: Callable[[], None] = lambda: None) -> list[dict[str, Any]]:
        try:
            return self._fetch_catalog_pages(congress, heartbeat=heartbeat)
        except _InventoryOverlap:
            # The combined source can repeat bills across pages. Each official
            # bill-type list must still reconcile to the unchanged Congress total.
            heartbeat()
            expected = self._catalog_total(self._request_json(f"/bill/{congress}", params={"limit": 1}))
            items = []
            for kind in CONGRESS_GOV_BILL_TYPE_SLUGS:
                items.extend(self._fetch_catalog_pages(congress, kind=kind, heartbeat=heartbeat))
            heartbeat()
            final = self._catalog_total(self._request_json(f"/bill/{congress}", params={"limit": 1}))
            identities = {congress_bill_identifier(row["type"], row["number"]) for row in items}
            if final != expected or len(items) != expected or len(identities) != expected:
                raise ValueError("Congress partitioned inventory does not match its unchanged official total")
            return items

    @staticmethod
    def _catalog_total(payload: dict[str, Any]) -> int:
        total = payload.get("pagination", {}).get("count")
        if type(total) is not int or total < 0:
            raise ValueError("Congress inventory has no valid official total")
        return total

    def _fetch_catalog_pages(
        self, congress: int, *, kind: str | None = None, heartbeat: Callable[[], None],
    ) -> list[dict[str, Any]]:
        items: dict[str, dict[str, Any]] = {}
        offset = 0
        expected: int | None = None
        path = f"/bill/{congress}" + (f"/{kind.lower()}" if kind else "")
        while True:
            heartbeat()
            params: dict[str, Any] = {"limit": 250, "offset": offset}
            if not kind:
                params["sort"] = "updateDate+desc"
            payload = self._request_json(path, params=params)
            total = self._catalog_total(payload)
            if expected is not None and total != expected:
                raise ValueError("Congress inventory changed during pagination; retry the inventory")
            expected = total
            page = self._extract_items(payload, "bills", singular_key="bill")
            if not page and offset < expected:
                raise ValueError("Congress inventory ended before its official total")
            for raw in page:
                bill_type, number = str(raw.get("type", "")).upper(), str(raw.get("number", ""))
                if (raw.get("congress") != congress or bill_type not in CONGRESS_GOV_BILL_TYPE_SLUGS
                        or not number.isdigit() or (kind and bill_type != kind)):
                    raise ValueError("Congress inventory contains an invalid bill identity")
                key = congress_bill_identifier(bill_type, number)
                if key in items:
                    raise _InventoryOverlap("Congress inventory repeated a bill; retry the inventory")
                # Keep public source facts, not API URLs that may contain credentials.
                items[key] = {k: raw[k] for k in (
                    "congress", "type", "number", "title", "latestAction", "updateDate", "updateDateIncludingText",
                ) if k in raw}
            offset += len(page)
            if offset >= expected:
                if len(items) != expected:
                    raise ValueError("Congress inventory does not match its official total")
                return list(items.values())

    def fetch_bill_detail(self, congress: int, bill_type: str, number: str | int) -> dict[str, Any]:
        payload = self._request_json(f"/bill/{congress}/{str(bill_type).lower()}/{number}")
        bill = payload.get("bill")
        if isinstance(bill, dict):
            return bill
        return payload

    def fetch_bill_summaries(self, congress: int, bill_type: str, number: str | int) -> list[dict[str, Any]]:
        payload = self._request_json(f"/bill/{congress}/{str(bill_type).lower()}/{number}/summaries")
        return self._extract_items(payload, "summaries", singular_key="summary")

    def fetch_bill_text_versions(self, congress: int, bill_type: str, number: str | int) -> list[dict[str, Any]]:
        payload = self._request_json(f"/bill/{congress}/{str(bill_type).lower()}/{number}/text")
        return self._extract_items(payload, "textVersions")

    def fetch_bill_actions(self, congress: int, bill_type: str, number: str | int, limit: int = 100) -> list[dict[str, Any]]:
        payload = self._request_json(
            f"/bill/{congress}/{str(bill_type).lower()}/{number}/actions",
            params={"limit": max(1, min(limit, 250))},
        )
        return self._extract_items(payload, "actions")

    def latest_summary_text(self, summaries: list[dict[str, Any]]) -> str:
        if not summaries:
            return ""
        best = max(
            summaries,
            key=lambda item: (
                str(item.get("updateDate") or ""),
                str(item.get("actionDate") or ""),
                str(item.get("versionCode") or ""),
            ),
        )
        return html_to_text(str(best.get("text") or ""))

    def pick_text_version(self, text_versions: list[dict[str, Any]], *, oldest: bool = False) -> dict[str, str] | None:
        candidates: list[dict[str, str]] = []
        for text_version in text_versions:
            version_type = str(text_version.get("type") or "").strip()
            version_date = str(text_version.get("date") or "").strip()
            for format_item in self._extract_nested_items(text_version.get("formats")):
                format_type = str(format_item.get("type") or "").strip()
                url = str(format_item.get("url") or "").strip()
                if not format_type or not url:
                    continue
                candidates.append(
                    {
                        "text_version_type": version_type,
                        "date": version_date,
                        "format_type": format_type,
                        "url": url,
                    }
                )
        if not candidates:
            return None
        if oldest:
            candidates.sort(
                key=lambda item: (
                    item["date"],
                    TEXT_FORMAT_PRIORITY.get(item["format_type"], 99),
                    item["url"],
                )
            )
        else:
            candidates.sort(
                key=lambda item: (
                    item["date"],
                    -TEXT_FORMAT_PRIORITY.get(item["format_type"], 99),
                    item["url"],
                ),
                reverse=True,
            )
        return candidates[0]

    def fetch_text_content(self, url: str | None) -> str:
        if not url:
            return ""
        lowered = url.lower()
        if lowered.endswith(".pdf"):
            return ""
        response = self._request(url, include_api_defaults=False)
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type:
            return ""
        return html_to_text(response.text)

    def extract_law(self, detail: dict[str, Any]) -> dict[str, str]:
        laws = detail.get("laws")
        candidates: list[dict[str, Any]]
        if isinstance(laws, dict) and {"type", "number"} & set(laws.keys()):
            candidates = [laws]
        else:
            candidates = self._extract_nested_items(laws)
        for item in candidates:
            law_type = str(item.get("type") or "").strip()
            law_number = str(item.get("number") or "").strip()
            if law_type or law_number:
                return {"type": law_type, "number": law_number}
        return {"type": "", "number": ""}

    def latest_action_text(self, record: dict[str, Any]) -> str:
        latest_action = record.get("latestAction")
        if isinstance(latest_action, dict):
            return str(latest_action.get("text") or "").strip()
        if isinstance(latest_action, list):
            for item in latest_action:
                if isinstance(item, dict):
                    value = str(item.get("text") or "").strip()
                    if value:
                        return value
        return ""

    def latest_action_date(self, record: dict[str, Any]) -> str:
        latest_action = record.get("latestAction")
        if isinstance(latest_action, dict):
            return str(latest_action.get("actionDate") or "").strip()
        if isinstance(latest_action, list):
            for item in latest_action:
                if isinstance(item, dict):
                    value = str(item.get("actionDate") or "").strip()
                    if value:
                        return value
        return ""

    def sponsor_name(self, detail: dict[str, Any]) -> str:
        sponsors = self._extract_nested_items(detail.get("sponsors"))
        if not sponsors:
            return ""
        sponsor = sponsors[0]
        prefix = ""
        origin_chamber = str(detail.get("originChamber") or "").strip().lower()
        if origin_chamber == "house":
            prefix = "Rep."
        elif origin_chamber == "senate":
            prefix = "Sen."

        pieces = [
            str(sponsor.get("firstName") or "").strip(),
            str(sponsor.get("middleName") or "").strip(),
            str(sponsor.get("lastName") or "").strip(),
        ]
        full_name = " ".join(piece for piece in pieces if piece).strip()
        if prefix and full_name:
            state = str(sponsor.get("state") or "").strip()
            if state:
                return f"{prefix} {full_name} ({state})"
            return f"{prefix} {full_name}"

        fallback = str(sponsor.get("fullName") or "").strip()
        return SPONSOR_PARTY_SUFFIX.sub("", fallback)

    def action_rows(self, actions: list[dict[str, Any]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in actions:
            source_system = item.get("sourceSystem")
            location = ""
            if isinstance(source_system, dict):
                location = str(source_system.get("name") or "").strip()
            if not location:
                location = str(item.get("type") or "").strip()
            rows.append(
                {
                    "statusDate": str(item.get("actionDate") or "").strip(),
                    "statusMessage": str(item.get("text") or "").strip(),
                    "location": location,
                }
            )
        return rows

    def _request_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._request(path, params=params)
        return response.json()

    def _request(self, path: str, params: dict[str, Any] | None = None, *, include_api_defaults: bool = True) -> httpx.Response:
        request_params: dict[str, Any] | None = None
        if include_api_defaults:
            request_params = {"format": "json", "api_key": self.settings.congress_api_key}
            if params:
                request_params.update(params)
        elif params:
            request_params = dict(params)

        response: httpx.Response | None = None
        for attempt in range(5):
            # One active federal worker stays below the published hourly API budget.
            time.sleep(max(0.0, 1.0 - (time.monotonic() - self._last_request_at)))
            self._last_request_at = time.monotonic()
            try:
                response = self.client.get(path, params=request_params)
            except httpx.TransportError as exc:
                if attempt == 4:
                    raise RuntimeError(f"Congress source network failure: {type(exc).__name__}") from None
                time.sleep(self._retry_delay(None, attempt))
                continue
            if response.status_code not in {429, 500, 502, 503, 504}:
                if response.is_error:
                    raise RuntimeError(f"Congress source returned HTTP {response.status_code}") from None
                return response

            retry_after = response.headers.get("Retry-After")
            response.close()
            if attempt == 4:
                break
            delay = self._retry_delay(retry_after, attempt)
            time.sleep(delay)

        if response is None:
            raise RuntimeError(f"Congress API request failed before a response was returned for {path}")
        raise RuntimeError(f"Congress source returned HTTP {response.status_code} after bounded retries")

    @staticmethod
    def _retry_delay(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass
        return min(20.0, float(2**attempt))

    @staticmethod
    def _extract_items(payload: dict[str, Any], key: str, singular_key: str | None = None) -> list[dict[str, Any]]:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return CongressApiClient._extract_nested_items(value, singular_key=singular_key)
        return []

    @staticmethod
    def _extract_nested_items(value: Any, singular_key: str | None = None) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []

        for key in [singular_key, "item", "items", "bill", "summary"]:
            if not key:
                continue
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
            if isinstance(candidate, dict):
                return [candidate]

        if {"type", "number"} & set(value.keys()):
            return [value]
        return []


def _ordinal(value: int) -> str:
    mod_hundred = value % 100
    if 10 <= mod_hundred <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"
