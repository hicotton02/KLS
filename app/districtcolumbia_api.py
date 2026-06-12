from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty, html_to_text


DISTRICT_OF_COLUMBIA_BILL_NUMBER_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z]+)(?P<period>\d+)-(?P<number>\d+)$",
    re.IGNORECASE,
)


def parse_district_of_columbia_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_district_of_columbia_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = DISTRICT_OF_COLUMBIA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group('prefix')}{int(match.group('period')):02d}-{int(match.group('number')):04d}"


def district_of_columbia_session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 1 else value - 1


def district_of_columbia_council_period(year: int) -> int:
    session_year = district_of_columbia_session_start_year(year)
    return ((session_year - 1975) // 2) + 1


def _sort_bill_key(bill_num: str) -> tuple[str, int, int]:
    match = DISTRICT_OF_COLUMBIA_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0, 0)
    return (match.group("prefix"), int(match.group("period")), int(match.group("number")))


class DistrictOfColumbiaApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.district_of_columbia_site_base,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{self.settings.district_of_columbia_site_base.rstrip('/')}/",
                "Origin": self.settings.district_of_columbia_site_base.rstrip("/"),
            },
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        council_period = district_of_columbia_council_period(year)
        page_index = 0
        page_size = 250
        expected_total: int | None = None
        items_by_bill: dict[str, dict[str, Any]] = {}

        while True:
            payload = self._api_post_json(
                "/api/Search/LegislationSearch",
                self._legislation_search_payload(
                    council_period=council_period,
                    page_index=page_index,
                    page_size=page_size,
                ),
            )
            if expected_total is None:
                expected_total = int((payload.get("pagination") or {}).get("totalCount") or 0)

            search_results = self._search_results(payload)
            if not isinstance(search_results, list) or not search_results:
                break

            for raw_item in search_results:
                item = self._normalize_search_item(year, raw_item if isinstance(raw_item, dict) else {})
                if item is not None:
                    items_by_bill[str(item["billNum"])] = item

            if expected_total <= (page_index + 1) * page_size:
                break
            page_index += 1

        if expected_total is not None and expected_total != len(items_by_bill):
            raise ValueError(
                "District of Columbia legislation count mismatch for "
                f"{year}: expected {expected_total}, parsed {len(items_by_bill)}"
            )

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        bill_num = self._bill_identifiers(detail_path, item)
        payload = self._api_get_json(f"/api/Search/GetLegislationDetails/{bill_num}")

        title = clean_text(payload.get("title")) or clean_text((item or {}).get("catchTitle")) or bill_num
        short_description = clean_text(payload.get("shortDescription"))
        status = first_non_empty(payload.get("tag"), payload.get("status"), (item or {}).get("billStatus"))
        actions = self._history_actions(payload.get("legislationHistory"))
        last_action = clean_text(actions[-1]["statusMessage"] if actions else "") or status
        last_action_date = clean_text(actions[-1]["statusDate"] if actions else "") or parse_district_of_columbia_date(
            (item or {}).get("lastActionDate")
        )
        chapter = first_non_empty(payload.get("lawNumber"), payload.get("actNumber"), payload.get("resolutionNumber"))
        signed_date = last_action_date if status.lower() == "enacted" else ""
        sponsor = first_non_empty(
            self._normalize_introducer_display(self._summary_value(payload, "Introduced by")),
            self._normalize_introducer_display(self._introducer_names((item or {}).get("introducers") or [])),
        )
        official_page = self.public_bill_url(bill_num)
        current_version_path = self._text_document_url(payload)
        current_text = fetch_document_text(self.client, current_version_path) if current_version_path else ""

        digest_bits = []
        for label, value in self._summary_pairs(payload):
            digest_bits.append(f"{label}: {value}")
        if status:
            digest_bits.append(f"Status: {status}")
        if chapter:
            digest_bits.append(f"Official number: {chapter}")

        current_version_fingerprint = json.dumps(
            {
                "bill": bill_num,
                "title": title,
                "status": status,
                "chapter": chapter,
                "documentUrl": current_version_path,
                "actions": actions,
                "textPrefix": current_text[:800],
            },
            sort_keys=True,
        )

        bill_type_match = re.match(r"[A-Z]+", bill_num)
        bill_type = bill_type_match.group(0) if bill_type_match else bill_num[:1]

        return {
            "bill": bill_num,
            "billType": bill_type,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": status or last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": "",
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": official_page,
            "digest": current_version_path or official_page,
            "summary": official_page,
            "currentVersionPath": current_version_path or official_page,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": self._paragraph_html(title, short_description),
            "digestHTML": self._paragraph_html(*digest_bits),
            "currentBillHTML": f"<pre>{html.escape(current_text)}</pre>" if current_text else "",
            "billActions": actions,
            "amendments": [],
            "officialPage": official_page,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        absolute = absolute_url(self.settings.district_of_columbia_site_base, url) or str(url or "")
        if not absolute:
            return ""
        return fetch_document_text(self.client, absolute)

    def public_bill_url(self, bill_num: str) -> str:
        return f"{self.settings.district_of_columbia_site_base.rstrip('/')}/Legislation/{bill_num}"

    def _bill_identifiers(self, detail_path: str, item: dict[str, Any] | None) -> str:
        bill_num = normalize_district_of_columbia_bill_number((item or {}).get("billNum"))
        if bill_num:
            return bill_num
        match = re.search(r"[A-Z]+\d+-\d+", str(detail_path or "").upper())
        if match is not None:
            return normalize_district_of_columbia_bill_number(match.group(0))
        raise ValueError(f"District of Columbia bill number could not be determined from {detail_path!r}")

    def _api_get_json(self, path: str) -> dict[str, Any]:
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    def _api_post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _legislation_search_payload(*, council_period: int, page_index: int, page_size: int) -> dict[str, Any]:
        return {
            "searchTerm": "",
            "requestorIds": [],
            "introducerIds": [],
            "chairpersonIds": [],
            "statusIds": [],
            "committeeIds": [],
            "categoryIds": [],
            "legislationTypeIds": [],
            "hearingTypeIds": [],
            "meetingTypeIds": [],
            "focusAreaIds": [],
            "priorities": [],
            "timePeriod": {},
            "createdDate": {},
            "hearingDate": {},
            "sort": {"field": "legislationNumber", "dir": "asc"},
            "pagination": {"pageIndex": page_index, "pageSize": page_size},
            "councilPeriodId": {"ids": [council_period]},
            "recordType": "Legislation",
        }

    @staticmethod
    def _search_results(payload: dict[str, Any]) -> list[dict[str, Any]]:
        nested = (((payload.get("searchResults") or {}).get("results")) or [])
        if isinstance(nested, list) and nested:
            return nested
        fallback = payload.get("results") or []
        return fallback if isinstance(fallback, list) else []

    def _normalize_search_item(self, year: int, payload: dict[str, Any]) -> dict[str, Any] | None:
        bill_num = normalize_district_of_columbia_bill_number(payload.get("legislationNumber"))
        if not bill_num:
            return None
        title = clean_text(payload.get("title")) or bill_num
        sponsor = self._introducer_names(payload.get("introducers") or [])
        status = first_non_empty(payload.get("tag"), payload.get("status"))
        introduction_date = parse_district_of_columbia_date(payload.get("introductionDate"))
        current_version_path = absolute_url(self.settings.district_of_columbia_site_base, payload.get("legislationTextUrl"))
        return {
            "billNum": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "billTitle": title,
            "sponsor": sponsor,
            "billStatus": status,
            "lastAction": status,
            "lastActionDate": introduction_date,
            "signedDate": "",
            "effectiveDate": "",
            "chapter": "",
            "detailPath": self.public_bill_url(bill_num),
            "currentVersionPath": current_version_path or self.public_bill_url(bill_num),
            "currentVersionFingerprint": json.dumps(
                {
                    "bill": bill_num,
                    "title": title,
                    "status": status,
                    "documentUrl": current_version_path,
                    "introductionDate": introduction_date,
                    "legislationId": payload.get("legislationId"),
                },
                sort_keys=True,
            ),
        }

    @staticmethod
    def _introducer_names(payload: Any) -> str:
        if not isinstance(payload, list):
            return ""
        names: list[str] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            name = clean_text(entry.get("formalName")) or clean_text(entry.get("name"))
            if name and name not in names:
                names.append(name)
        return ", ".join(names)

    @staticmethod
    def _summary_pairs(payload: dict[str, Any]) -> list[tuple[str, str]]:
        summary = payload.get("introducerSummary") or {}
        rows = summary.get("summaryDataList") if isinstance(summary, dict) else []
        pairs: list[tuple[str, str]] = []
        if not isinstance(rows, list):
            return pairs
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = clean_text(row.get("label"))
            value = clean_text(html_to_text(str(row.get("content") or "")))
            if label and value:
                pairs.append((label, value))
        return pairs

    def _summary_value(self, payload: dict[str, Any], label: str) -> str:
        desired = clean_text(label).lower()
        for entry_label, value in self._summary_pairs(payload):
            if entry_label.lower() == desired:
                return value
        return ""

    @staticmethod
    def _normalize_introducer_display(value: str | None) -> str:
        raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not raw:
            return ""
        candidate = raw.split("\n")[-1].strip()
        candidate = re.sub(r"^(Councilmember|Chairman|Chairwoman|Council Chair)\s+", "", candidate, flags=re.IGNORECASE)
        return clean_text(candidate)

    def _history_actions(self, payload: Any) -> list[dict[str, str]]:
        if not isinstance(payload, list):
            return []
        actions: list[dict[str, str]] = []
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            message = first_non_empty(
                entry.get("actionText"),
                entry.get("type"),
                ((entry.get("data") or {}).get("description") if isinstance(entry.get("data"), dict) else ""),
            )
            message = clean_text(message)
            if not message:
                continue
            actions.append(
                {
                    "statusDate": parse_district_of_columbia_date(entry.get("sortDate") or entry.get("date")),
                    "location": "",
                    "statusMessage": message,
                }
            )
        return actions

    def _text_document_url(self, payload: dict[str, Any]) -> str:
        direct = absolute_url(self.settings.district_of_columbia_site_base, payload.get("legislationTextUrl"))
        if direct:
            return direct

        other_documents = payload.get("otherDocuments")
        if not isinstance(other_documents, list):
            return ""
        for document in other_documents:
            if not isinstance(document, dict):
                continue
            candidate = first_non_empty(
                document.get("documentUrl"),
                document.get("url"),
                document.get("downloadUrl"),
                document.get("fileUrl"),
            )
            candidate_url = absolute_url(self.settings.district_of_columbia_site_base, candidate)
            if candidate_url:
                return candidate_url
        return ""

    @staticmethod
    def _paragraph_html(*parts: str) -> str:
        paragraphs = [clean_text(part) for part in parts if clean_text(part)]
        return "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs)
