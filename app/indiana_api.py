from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any

import requests

from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


INDIANA_BILL_NUMBER_PATTERN = re.compile(r"^([A-Z]+)\s*0*(\d+)$", re.IGNORECASE)
INDIANA_BILL_PATH_PATTERN = re.compile(
    r"/(?P<year>\d{4}(?:ss\d+)?)/bills/(?P<bill>[a-z]+\d+)(?:/)?$",
    re.IGNORECASE,
)
INDIANA_VERSION_PATH_PATTERN = re.compile(
    r"/(?P<year>\d{4}(?:ss\d+)?)/bills/(?P<bill>[a-z]+\d+)/versions/(?P<version>[^/?#]+)(?:/)?$",
    re.IGNORECASE,
)
INDIANA_AMENDMENT_PATH_PATTERN = re.compile(
    r"/(?P<year>\d{4}(?:ss\d+)?)/bills/(?P<bill>[a-z]+\d+)/versions/(?P<version>[^/?#]+)/amendments/(?P<amendment>[^/?#]+)(?:/)?$",
    re.IGNORECASE,
)
INDIANA_PUBLIC_LAW_PATTERN = re.compile(r"\bpublic law\b[\s#:.-]*([0-9]+)", re.IGNORECASE)


def parse_indiana_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "T" in raw:
        return raw.split("T", 1)[0]
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_indiana_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = INDIANA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1)}{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = INDIANA_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _bill_type_from_number(bill_num: str) -> str:
    match = re.match(r"[A-Z]+", str(bill_num or "").upper())
    return match.group(0) if match else str(bill_num or "")[:2]


def _normalize_name_fields(person: dict[str, Any]) -> str:
    full_name = clean_text(person.get("fullName"))
    if full_name:
        return full_name
    position = clean_text(person.get("position_title"))
    first = clean_text(first_non_empty(person.get("firstName"), person.get("firstname")))
    last = clean_text(first_non_empty(person.get("lastName"), person.get("lastname")))
    parts = [part for part in (position, first, last) if part]
    return " ".join(parts)


def _status_label(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered == "p":
        return "Passed"
    if lowered == "f":
        return "Failed"
    if lowered == "w":
        return "Withdrawn"
    return raw


class IndianaApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = self.settings.request_timeout_seconds
        self.api_base = self.settings.indiana_site_base.rstrip("/")
        self.api_key = clean_text(self.settings.indiana_api_key)
        self.client = requests.Session()
        self.client.headers.update(
            {
                "User-Agent": f"KeepingLawSimple/1.0 (+{self.settings.public_base_url.rstrip('/')})",
                "Accept": "application/json,text/plain,*/*",
            }
        )
        if self.api_key:
            self.client.headers["X-Api-Key"] = self.api_key

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        self._require_api_key()
        payload = self._api_get_json(f"/{year}/bills")
        expected_count = int(payload.get("itemCount") or 0)
        items_by_bill: dict[str, dict[str, Any]] = {}
        for raw_item in payload.get("items") or []:
            item = self._normalize_search_item(year, raw_item if isinstance(raw_item, dict) else {})
            if item is not None:
                items_by_bill[str(item["billNum"])] = item

        if expected_count and expected_count != len(items_by_bill):
            raise ValueError(
                f"Indiana API count mismatch for {year}: expected {expected_count}, parsed {len(items_by_bill)}"
            )

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_api_key()
        year, bill_num, bill_path = self._bill_identifiers(detail_path, item)
        payload = self._api_get_json(bill_path)

        actions = self._fetch_actions(payload.get("actions"))
        versions = self._sorted_versions(payload.get("versions") or [])
        latest_version = payload.get("latestVersion") or (versions[-1] if versions else {})
        latest_version_link = self._absolute_url(latest_version.get("link"))
        introduced_version = versions[0] if versions else {}
        introduced_version_link = self._absolute_url(introduced_version.get("link"))

        version_details = self._fetch_version_details(versions)
        version_details_by_name = {
            clean_text(detail.get("printVersionName")): detail for detail in version_details if clean_text(detail.get("printVersionName"))
        }
        current_version_detail = version_details_by_name.get(clean_text(latest_version.get("printVersionName"))) or (
            version_details[-1] if version_details else {}
        )

        title = clean_text(first_non_empty(payload.get("title"), latest_version.get("title"), payload.get("description"))) or bill_num
        summary_text = clean_text(
            first_non_empty(
                latest_version.get("shortDescription"),
                payload.get("description"),
                latest_version.get("title"),
            )
        ) or title
        digest_text = clean_text(first_non_empty(latest_version.get("digest"), current_version_detail.get("digest")))
        author_names = self._person_names(payload.get("authors") or [])
        coauthor_names = self._person_names(payload.get("coauthors") or [])
        sponsor_names = self._person_names(payload.get("sponsors") or [])
        cosponsor_names = self._person_names(payload.get("cosponsors") or [])
        sponsor = first_non_empty(
            author_names[0] if author_names else "",
            sponsor_names[0] if sponsor_names else "",
            (item or {}).get("sponsor"),
        )
        house_sponsors = ", ".join(name for name in author_names + coauthor_names if name)
        senate_sponsors = ", ".join(name for name in sponsor_names + cosponsor_names if name)

        last_action = clean_text(actions[-1]["statusMessage"] if actions else "")
        last_action_date = clean_text(actions[-1]["statusDate"] if actions else "")
        bill_status = first_non_empty(
            payload.get("stage"),
            latest_version.get("stageVerbose"),
            payload.get("committeeStatus"),
            payload.get("status"),
            last_action,
        )
        official_page = self._absolute_url(first_non_empty(payload.get("link"), bill_path))
        chapter = ""
        signed_date = ""
        for action in reversed(actions):
            message = clean_text(action.get("statusMessage"))
            message_lower = message.lower()
            if not signed_date and "signed by the governor" in message_lower:
                signed_date = clean_text(action.get("statusDate"))
            if not chapter:
                chapter_match = INDIANA_PUBLIC_LAW_PATTERN.search(message)
                if chapter_match is not None:
                    chapter = chapter_match.group(1)
                    if not signed_date:
                        signed_date = clean_text(action.get("statusDate"))

        digest_bits: list[str] = []
        if author_names:
            digest_bits.append(f"Authors: {', '.join(author_names)}")
        if sponsor_names:
            digest_bits.append(f"Senate sponsors: {', '.join(sponsor_names)}")
        if bill_status:
            digest_bits.append(f"Stage: {bill_status}")
        committee_status = clean_text(payload.get("committeeStatus"))
        if committee_status and committee_status != bill_status:
            digest_bits.append(f"Committee status: {committee_status}")
        digest_bits.extend(
            f"{action['statusDate']}: {action['statusMessage']}".strip(": ")
            for action in actions[-3:]
            if action.get("statusMessage")
        )

        amendments = self._collect_amendments(version_details)
        current_version_fingerprint = json.dumps(
            {
                "bill": bill_num,
                "year": year,
                "title": title,
                "summary": summary_text,
                "digest": digest_text,
                "stage": bill_status,
                "actions": actions,
                "currentVersion": clean_text(latest_version.get("printVersionName")),
                "versions": [
                    {
                        "name": clean_text(version.get("printVersionName")),
                        "updated": parse_indiana_date(version.get("updated")),
                        "filed": parse_indiana_date(version.get("filed")),
                    }
                    for version in versions
                ],
                "amendments": [
                    {
                        "number": item.get("amendmentNumber"),
                        "status": item.get("status"),
                        "sponsor": item.get("sponsor"),
                    }
                    for item in amendments
                ],
                "chapter": chapter,
                "signedDate": signed_date,
            },
            sort_keys=True,
        )

        return {
            "bill": bill_num,
            "billType": _bill_type_from_number(bill_num),
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": bill_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(latest_version.get("printVersionName")),
            "sponsorStringHouse": house_sponsors or None,
            "sponsorStringSenate": senate_sponsors or None,
            "introduced": introduced_version_link or official_page,
            "digest": latest_version_link or official_page,
            "summary": official_page,
            "currentVersionPath": latest_version_link or official_page,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": self._paragraph_html(title, summary_text if summary_text != title else ""),
            "digestHTML": self._paragraph_html(digest_text, *digest_bits),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": official_page,
            "specialSessionValue": None,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        raw_url = clean_text(url)
        if not raw_url:
            return ""

        amendment_match = INDIANA_AMENDMENT_PATH_PATTERN.search(self._relative_path(raw_url))
        if amendment_match is not None:
            payload = self._api_get_json(amendment_match.group(0))
            author = payload.get("author") or {}
            return clean_text(
                "\n\n".join(
                    part
                    for part in (
                        clean_text(payload.get("name")),
                        clean_text(payload.get("description")),
                        _normalize_name_fields(author if isinstance(author, dict) else {}),
                        clean_text(payload.get("type")),
                        _status_label(payload.get("state")),
                    )
                    if clean_text(part)
                )
            )

        version_match = INDIANA_VERSION_PATH_PATTERN.search(self._relative_path(raw_url))
        if version_match is not None:
            payload = self._api_get_json(version_match.group(0))
            return clean_text(
                "\n\n".join(
                    part
                    for part in (
                        clean_text(payload.get("title")),
                        clean_text(payload.get("shortDescription")),
                        clean_text(payload.get("digest")),
                    )
                    if clean_text(part)
                )
            )

        bill_match = INDIANA_BILL_PATH_PATTERN.search(self._relative_path(raw_url))
        if bill_match is not None:
            payload = self._api_get_json(bill_match.group(0))
            latest = payload.get("latestVersion") or {}
            return clean_text(
                "\n\n".join(
                    part
                    for part in (
                        clean_text(first_non_empty(payload.get("title"), latest.get("title"))),
                        clean_text(first_non_empty(latest.get("shortDescription"), payload.get("description"))),
                        clean_text(latest.get("digest")),
                    )
                    if clean_text(part)
                )
            )

        return ""

    def _require_api_key(self) -> None:
        if self.api_key:
            return
        raise ValueError("Indiana API key is not configured")

    def _api_get_json(self, path_or_url: str) -> dict[str, Any]:
        response = self.client.get(self._absolute_url(path_or_url), timeout=max(self.timeout, 120))
        response.raise_for_status()
        return response.json()

    def _normalize_search_item(self, year: int, raw_item: dict[str, Any]) -> dict[str, Any] | None:
        bill_num = normalize_indiana_bill_number(first_non_empty(raw_item.get("billName"), raw_item.get("displayName")))
        if not bill_num:
            return None

        title = clean_text(first_non_empty(raw_item.get("description"), raw_item.get("displayName"))) or bill_num
        detail_path = self._absolute_url(raw_item.get("link"))
        current_version_fingerprint = json.dumps(
            {
                "bill": bill_num,
                "filed": parse_indiana_date(raw_item.get("filed")),
                "active": bool(raw_item.get("active")),
                "type": clean_text(raw_item.get("type")),
                "title": title,
                "link": detail_path,
            },
            sort_keys=True,
        )

        return {
            "billNum": bill_num,
            "billType": _bill_type_from_number(bill_num),
            "catchTitle": title,
            "billTitle": title,
            "summaryText": title,
            "sponsor": "",
            "billStatus": "Active" if raw_item.get("active") else "",
            "lastAction": "",
            "lastActionDate": parse_indiana_date(raw_item.get("filed")),
            "signedDate": "",
            "effectiveDate": "",
            "chapter": "",
            "detailPath": detail_path,
            "currentVersionPath": "",
            "currentVersionFingerprint": current_version_fingerprint,
            "officialPage": detail_path,
            "year": year,
        }

    def _fetch_actions(self, action_block: Any) -> list[dict[str, str]]:
        action_link = ""
        if isinstance(action_block, dict):
            action_link = clean_text(action_block.get("link"))
        if not action_link:
            return []
        payload = self._api_get_json(action_link)
        actions: list[dict[str, str | int]] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            message = clean_text(item.get("description"))
            if not message:
                continue
            chamber = item.get("chamber") or {}
            committee = item.get("committee") or {}
            location = clean_text((chamber if isinstance(chamber, dict) else {}).get("name"))
            if not location:
                location = clean_text((committee if isinstance(committee, dict) else {}).get("name"))
            actions.append(
                {
                    "statusDate": parse_indiana_date(item.get("date")),
                    "location": location,
                    "statusMessage": message,
                    "_sequence": int(clean_text(item.get("sequence")) or 0),
                }
            )
        actions.sort(key=lambda item: (str(item["statusDate"]), int(item["_sequence"]), str(item["statusMessage"])))
        return [
            {
                "statusDate": str(item["statusDate"]),
                "location": str(item["location"]),
                "statusMessage": str(item["statusMessage"]),
            }
            for item in actions
        ]

    def _fetch_version_details(self, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        details: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        for version in versions:
            version_link = self._absolute_url(version.get("link"))
            if not version_link or version_link in seen_links:
                continue
            seen_links.add(version_link)
            try:
                details.append(self._api_get_json(version_link))
            except requests.RequestException:
                continue
        details.sort(key=lambda detail: self._version_sort_key(detail))
        return details

    def _collect_amendments(self, version_details: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        for detail in version_details:
            version_name = clean_text(detail.get("printVersionName"))
            version_label = clean_text(detail.get("stageVerbose")) or version_name
            for field_name, order_label in (
                ("cmte_amendments", "Committee"),
                ("amendments", "Filed"),
                ("floor_amendments", "Floor"),
            ):
                for amendment in detail.get(field_name) or []:
                    if not isinstance(amendment, dict):
                        continue
                    amendment_number = clean_text(amendment.get("name"))
                    if not amendment_number or amendment_number in seen:
                        continue
                    seen.add(amendment_number)
                    author = amendment.get("author") or {}
                    sponsor = _normalize_name_fields(author if isinstance(author, dict) else {})
                    collected.append(
                        {
                            "amendmentNumber": amendment_number,
                            "house": "",
                            "order": " ".join(part for part in (order_label, version_label) if part),
                            "sequence": clean_text(amendment.get("publishtime")) or version_name,
                            "status": _status_label(amendment.get("state")) or clean_text(amendment.get("type")) or "Filed",
                            "sponsor": sponsor,
                            "documentUrl": self._absolute_url(amendment.get("link")),
                        }
                    )
        return collected

    @staticmethod
    def _person_names(items: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _normalize_name_fields(item)
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names

    @staticmethod
    def _sorted_versions(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(versions, key=IndianaApiClient._version_sort_key)

    @staticmethod
    def _version_sort_key(version: dict[str, Any]) -> tuple[int, str, str]:
        version_number = clean_text(first_non_empty(version.get("printVersion"), version.get("printVersionName")))
        digits_match = re.search(r"(\d+)", version_number)
        numeric = int(digits_match.group(1)) if digits_match is not None else 0
        updated = parse_indiana_date(first_non_empty(version.get("updated"), version.get("filed"), version.get("printed")))
        version_name = clean_text(version.get("printVersionName"))
        return (numeric, updated, version_name)

    @staticmethod
    def _paragraph_html(*parts: str) -> str:
        blocks = [clean_text(part) for part in parts if clean_text(part)]
        return "".join(f"<p>{html.escape(block)}</p>" for block in blocks)

    def _bill_identifiers(self, detail_path: str, item: dict[str, Any] | None) -> tuple[str, str, str]:
        candidates = [
            clean_text(detail_path),
            clean_text((item or {}).get("detailPath")),
            clean_text((item or {}).get("officialPage")),
        ]
        for candidate in candidates:
            match = INDIANA_BILL_PATH_PATTERN.search(self._relative_path(candidate))
            if match is not None:
                year = match.group("year")
                bill_num = normalize_indiana_bill_number(match.group("bill"))
                return year, bill_num, match.group(0)
        bill_num = normalize_indiana_bill_number((item or {}).get("billNum"))
        year = clean_text((item or {}).get("year"))
        if bill_num and year:
            return year, bill_num, f"/{year}/bills/{bill_num.lower()}"
        raise ValueError(f"Indiana bill path could not be parsed from {detail_path!r}")

    def _absolute_url(self, path_or_url: str | None) -> str:
        raw = clean_text(path_or_url)
        if not raw:
            return ""
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        return f"{self.api_base}{raw if raw.startswith('/') else '/' + raw}"

    def _relative_path(self, path_or_url: str | None) -> str:
        raw = clean_text(path_or_url)
        if not raw:
            return ""
        if raw.startswith(self.api_base):
            return raw[len(self.api_base) :]
        api_prefix = "https://api.iga.in.gov"
        if raw.startswith(api_prefix):
            return raw[len(api_prefix) :]
        return raw
