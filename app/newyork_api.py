from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import requests

from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


NEW_YORK_BILL_NUMBER_PATTERN = re.compile(r"^([A-Z]+)\s*0*(\d+)$", re.IGNORECASE)
NEW_YORK_PUBLIC_BILL_PATTERN = re.compile(r"/legislation/bills/(?P<session>\d{4})/(?P<bill>[A-Z]+\d+)", re.IGNORECASE)
NEW_YORK_CHAPTER_PATTERN = re.compile(r"\bCHAP(?:TER|\.)\s*([0-9]+)\b", re.IGNORECASE)


def parse_new_york_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_new_york_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = NEW_YORK_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1)}{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = NEW_YORK_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _is_new_york_pagination_boundary(exc: requests.HTTPError) -> bool:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code == 400 or str(exc).startswith("400")


class NewYorkApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = self.settings.request_timeout_seconds
        self.public_base = self.settings.new_york_site_base.rstrip("/")
        self.api_base = self.settings.new_york_api_base.rstrip("/")
        self.api_key = clean_text(self.settings.new_york_api_key)
        self.scraper = requests.Session()
        self.scraper.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

    def close(self) -> None:
        self.scraper.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        self._require_api_key()
        items_by_bill: dict[str, dict[str, Any]] = {}
        expected_total: int | None = None

        for sort_order in ("printNo.keyword:ASC", "printNo.keyword:DESC"):
            total = self._fetch_year_bill_window(year, sort_order, items_by_bill)
            if expected_total is None:
                expected_total = total
            if expected_total is not None and len(items_by_bill) >= expected_total:
                break

        if expected_total is not None and expected_total != len(items_by_bill):
            raise ValueError(
                f"New York API count mismatch for {year}: expected {expected_total}, parsed {len(items_by_bill)}"
            )

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def _fetch_year_bill_window(
        self,
        year: int,
        sort_order: str,
        items_by_bill: dict[str, dict[str, Any]],
    ) -> int | None:
        limit = 100
        offset = 1
        expected_total: int | None = None

        while True:
            try:
                payload = self._api_get_json(
                    f"/bills/{year}/search",
                    [
                        ("term", "*"),
                        ("limit", str(limit)),
                        ("sort", sort_order),
                        ("offset", str(offset)),
                    ],
                )
            except requests.HTTPError as exc:
                if offset > 1 and _is_new_york_pagination_boundary(exc):
                    break
                raise
            result = payload.get("result") or {}
            rows = result.get("items") or []

            if expected_total is None:
                expected_total = int(payload.get("total") or 0)

            for row in rows:
                data = row.get("result") if isinstance(row, dict) else None
                item = self._search_result_item(year, data if isinstance(data, dict) else {})
                if item is not None:
                    items_by_bill[str(item["billNum"])] = item

            offset_end = int(payload.get("offsetEnd") or 0)
            if not rows or expected_total <= 0 or offset_end >= expected_total:
                break
            offset = offset_end + 1

        return expected_total

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_api_key()
        year, bill_num = self._bill_identifiers(detail_path, item)

        detail_result = self._api_get_json(
            f"/bills/{year}/{bill_num}",
            [("view", "default")],
        ).get("result") or {}
        full_text_result = self._api_get_json(
            f"/bills/{year}/{bill_num}",
            [("view", "only_fulltext"), ("fullTextFormat", "PLAIN")],
        ).get("result") or {}

        full_text = self._normalize_full_text(full_text_result.get("fullText"))
        title = clean_text(detail_result.get("title")) or clean_text((item or {}).get("catchTitle")) or bill_num
        summary_text = clean_text(detail_result.get("summary")) or title
        sponsor_names = self._sponsor_names(detail_result)
        sponsor = first_non_empty(sponsor_names[0] if sponsor_names else "", (item or {}).get("sponsor"))
        actions = self._normalize_actions(detail_result.get("actions"))
        status = detail_result.get("status") or {}
        status_desc = self._status_description(status)
        last_action = clean_text(actions[-1]["statusMessage"] if actions else "") or status_desc
        last_action_date = clean_text(actions[-1]["statusDate"] if actions else "") or parse_new_york_date(
            status.get("actionDate")
        )
        approval_message = clean_text(detail_result.get("approvalMessage"))
        veto_messages = self._veto_messages(detail_result)
        chapter = self._chapter_from_actions(actions) or self._chapter_from_text(approval_message)
        signed_date = self._signed_date(detail_result, actions, status)
        official_page = self._public_bill_url(year, bill_num)

        digest_bits: list[str] = []
        if sponsor_names:
            digest_bits.append(f"Sponsor: {', '.join(sponsor_names)}")
        if status_desc:
            digest_bits.append(f"Status: {status_desc}")
        committee_name = clean_text(status.get("committeeName"))
        if committee_name:
            digest_bits.append(f"Committee: {committee_name}")
        if approval_message:
            digest_bits.append(approval_message)
        digest_bits.extend(veto_messages)

        current_version_fingerprint = json.dumps(
            {
                "bill": bill_num,
                "session": year,
                "title": title,
                "summary": summary_text,
                "status": status_desc,
                "approvalMessage": approval_message,
                "actions": actions,
                "chapter": chapter,
                "signedDate": signed_date,
                "fullTextPrefix": full_text[:800],
            },
            sort_keys=True,
        )

        bill_type = re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num[:1]
        sponsor_string = ", ".join(sponsor_names)

        return {
            "bill": bill_num,
            "billType": bill_type,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": status_desc or last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(detail_result.get("activeVersion")),
            "sponsorStringHouse": sponsor_string if bill_num.startswith("A") and sponsor_string else None,
            "sponsorStringSenate": sponsor_string if bill_num.startswith("S") and sponsor_string else None,
            "introduced": official_page,
            "digest": official_page,
            "summary": official_page,
            "currentVersionPath": official_page,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": self._paragraph_html(title, summary_text if summary_text != title else ""),
            "digestHTML": self._paragraph_html(*digest_bits),
            "currentBillHTML": f"<pre>{html.escape(full_text)}</pre>" if full_text else "",
            "billActions": actions,
            "amendments": [],
            "officialPage": official_page,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        try:
            year, bill_num = self._bill_identifiers(url or "", None)
        except ValueError:
            return ""
        full_text_result = self._api_get_json(
            f"/bills/{year}/{bill_num}",
            [("view", "only_fulltext"), ("fullTextFormat", "PLAIN")],
        ).get("result") or {}
        return self._normalize_full_text(full_text_result.get("fullText"))

    def _require_api_key(self) -> None:
        if self.api_key:
            return
        raise ValueError("New York API key is not configured")

    def _api_get_json(self, path: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        request_params = list(params or [])
        request_params.append(("key", self.api_key))
        url = f"{self.api_base}{path}"
        if request_params:
            url = f"{url}?{urlencode(request_params)}"
        response = self.scraper.get(url, timeout=max(self.timeout, 120))
        response.raise_for_status()
        return response.json()

    def _search_result_item(self, year: int, data: dict[str, Any]) -> dict[str, Any] | None:
        bill_num = normalize_new_york_bill_number(data.get("basePrintNo") or data.get("printNo"))
        if not bill_num:
            return None

        title = clean_text(data.get("title")) or clean_text(data.get("summary")) or bill_num
        sponsor_names = self._sponsor_names(data)
        sponsor = sponsor_names[0] if sponsor_names else ""
        actions = self._normalize_actions(data.get("actions"))
        status = data.get("status") or {}
        status_desc = self._status_description(status)
        last_action = clean_text(actions[-1]["statusMessage"] if actions else "") or status_desc
        last_action_date = clean_text(actions[-1]["statusDate"] if actions else "") or parse_new_york_date(
            status.get("actionDate")
        )
        detail_path = self._public_bill_url(year, bill_num)
        current_version_fingerprint = json.dumps(
            {
                "bill": bill_num,
                "session": year,
                "title": title,
                "summary": clean_text(data.get("summary")),
                "status": status_desc,
                "actions": actions,
                "activeVersion": clean_text(data.get("activeVersion")),
            },
            sort_keys=True,
        )

        return {
            "billNum": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "billTitle": title,
            "sponsor": sponsor,
            "billStatus": status_desc or last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": "",
            "effectiveDate": "",
            "chapter": "",
            "detailPath": detail_path,
            "currentVersionPath": detail_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryText": clean_text(data.get("summary")) or title,
        }

    def _bill_identifiers(self, detail_path: str, item: dict[str, Any] | None) -> tuple[int, str]:
        raw_path = str(detail_path or "")
        match = NEW_YORK_PUBLIC_BILL_PATTERN.search(raw_path)
        if match is not None:
            bill_num = normalize_new_york_bill_number(match.group("bill"))
            if bill_num:
                return int(match.group("session")), bill_num

        bill_num = normalize_new_york_bill_number((item or {}).get("billNum"))
        if bill_num and (item or {}).get("year"):
            return int(item["year"]), bill_num
        if bill_num:
            public_match = re.search(r"/bills/(\d{4})/", raw_path)
            if public_match is not None:
                return int(public_match.group(1)), bill_num
        raise ValueError(f"New York bill identifiers could not be determined from {detail_path!r}")

    def _public_bill_url(self, year: int, bill_num: str) -> str:
        return f"{self.public_base}/legislation/bills/{year}/{bill_num}"

    @staticmethod
    def _status_description(status: Any) -> str:
        if not isinstance(status, dict):
            return clean_text(status)
        return clean_text(status.get("statusDesc"))

    @staticmethod
    def _sponsor_names(payload: dict[str, Any]) -> list[str]:
        names: list[str] = []
        primary = clean_text(((payload.get("sponsor") or {}).get("member") or {}).get("fullName"))
        if primary:
            names.append(primary)

        for block_name in ("additionalSponsors", "programInfo"):
            block = payload.get(block_name)
            if not isinstance(block, list):
                continue
            for entry in block:
                name = clean_text(
                    (((entry or {}).get("member") or {}).get("fullName"))
                    or ((entry or {}).get("member") or {}).get("shortName")
                    or (entry or {}).get("fullName")
                    or (entry or {}).get("name")
                )
                if name and name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _normalize_actions(payload: Any) -> list[dict[str, str]]:
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        actions: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            message = clean_text(item.get("text"))
            if not message:
                continue
            chamber = clean_text(item.get("chamber")).title()
            actions.append(
                {
                    "statusDate": parse_new_york_date(item.get("date")),
                    "location": chamber,
                    "statusMessage": message,
                }
            )
        return actions

    @staticmethod
    def _veto_messages(payload: dict[str, Any]) -> list[str]:
        messages = payload.get("vetoMessages")
        if not isinstance(messages, list):
            return []
        collected: list[str] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            text = clean_text(message.get("memo"))
            if text:
                collected.append(text)
        return collected

    @staticmethod
    def _signed_date(payload: dict[str, Any], actions: list[dict[str, str]], status: dict[str, Any]) -> str:
        if payload.get("signed"):
            signed_action = parse_new_york_date(status.get("actionDate"))
            if signed_action:
                return signed_action
        for action in reversed(actions):
            message = clean_text(action.get("statusMessage"))
            if "signed" in message.lower() or "chapter" in message.lower():
                return clean_text(action.get("statusDate"))
        return ""

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        match = NEW_YORK_CHAPTER_PATTERN.search(raw)
        if match is None:
            return ""
        return match.group(1)

    def _chapter_from_actions(self, actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            chapter = self._chapter_from_text(action.get("statusMessage"))
            if chapter:
                return chapter
        return ""

    @staticmethod
    def _normalize_full_text(value: Any) -> str:
        raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.rstrip() for line in raw.split("\n")]
        return "\n".join(lines).strip()

    @staticmethod
    def _paragraph_html(*paragraphs: str) -> str:
        parts = []
        for paragraph in paragraphs:
            text = clean_text(paragraph)
            if text:
                parts.append(f"<p>{html.escape(text)}</p>")
        return "".join(parts)
