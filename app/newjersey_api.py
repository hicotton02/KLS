from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


NEW_JERSEY_BILL_NUMBER_PATTERN = re.compile(r"^(A|S|ACR|SCR|AJR|SJR|AR|SR)\d+$", re.IGNORECASE)
NEW_JERSEY_CHAPTER_PATTERN = re.compile(r"\bP\.L\.\d{4},\s*c\.\s*(\d+)\b", re.IGNORECASE)


def normalize_new_jersey_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if NEW_JERSEY_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_new_jersey_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 0 else value - 1


class NewJerseyApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.new_jersey_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._current_session_year: int | None = None

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_year = _session_start_year(year)
        response = self.client.get(f"/api/billSearch/allBills/{session_year}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
            raise ValueError("New Jersey all-bills payload did not include a bill list")

        bill_rows = payload[0]
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in bill_rows:
            bill_num = normalize_new_jersey_bill_number(row.get("Bill"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            governor_action = clean_text(row.get("GovernorAction"))
            detail_path = absolute_url(self.settings.new_jersey_site_base, f"/bill-search/{session_year}/{bill_num}") or ""
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": clean_text(row.get("Synopsis")) or bill_num,
                    "billTitle": clean_text(row.get("Synopsis")) or bill_num,
                    "sponsor": "",
                    "billStatus": governor_action,
                    "lastAction": governor_action,
                    "lastActionDate": "",
                    "signedDate": "",
                    "chapter": self._chapter_from_text(governor_action),
                    "detailPath": detail_path,
                    "currentVersionPath": detail_path,
                    "currentVersionFingerprint": "|".join(part for part in (detail_path, governor_action) if part),
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        bill_num, session_year = self._bill_identifiers(detail_path, item)
        is_current_session = session_year >= self._current_session()
        detail_prefix = "/api/billDetail" if is_current_session else "/api/billDetailHist"

        description = self._get_json(f"{detail_prefix}/billDescription/{bill_num}/{session_year}")
        history_rows = self._get_json(f"{detail_prefix}/billHistory/{bill_num}/{session_year}")
        sponsors = self._get_json(f"{detail_prefix}/billSponsors/{bill_num}/{session_year}")
        text_rows = self._get_json(f"{detail_prefix}/billText/{bill_num}/{session_year}")

        description_row = description[0] if isinstance(description, list) and description else {}
        primary_sponsors = sponsors[0] if isinstance(sponsors, list) and sponsors else []
        co_sponsors = sponsors[1] if isinstance(sponsors, list) and len(sponsors) > 1 else []
        current_text = text_rows[-1] if isinstance(text_rows, list) and text_rows else {}
        introduced_text = text_rows[0] if isinstance(text_rows, list) and text_rows else {}

        sponsor_names = [clean_text(row.get("Full_Name")) for row in primary_sponsors if clean_text(row.get("Full_Name"))]
        co_sponsor_names = [clean_text(row.get("Full_Name")) for row in co_sponsors if clean_text(row.get("Full_Name"))]
        sponsor = sponsor_names[0] if sponsor_names else ""
        sponsor_string = "; ".join(dict.fromkeys([*sponsor_names, *co_sponsor_names]))

        actions = self._history_actions(history_rows)
        last_action_entry = actions[-1] if actions else {}
        last_action = clean_text(last_action_entry.get("statusMessage"))
        last_action_date = clean_text(last_action_entry.get("statusDate"))

        synopsis = clean_text(description_row.get("Synopsis")) or clean_text((item or {}).get("catchTitle")) or bill_num
        code_description = clean_text(description_row.get("Code_Description"))
        fiscal_note = clean_text(description_row.get("FiscalNote"))
        status_code = clean_text(description_row.get("CurrentStatus"))
        governor_action = clean_text((item or {}).get("lastAction") or (item or {}).get("billStatus"))
        chapter = self._chapter_from_text(governor_action) or self._chapter_from_actions(actions)
        signed_date = last_action_date if chapter else ""

        current_version_path = self._document_url(current_text.get("HTML_Link") or current_text.get("PDFLink"))
        introduced_path = self._document_url(introduced_text.get("HTML_Link") or introduced_text.get("PDFLink"))
        official_page = absolute_url(self.settings.new_jersey_site_base, f"/bill-search/{session_year}/{bill_num}") or detail_path

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": synopsis,
            "sponsor": sponsor,
            "billTitle": synopsis,
            "billStatus": clean_text(governor_action or last_action or status_code),
            "lastAction": clean_text(last_action or governor_action or status_code),
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(current_text.get("Description")),
            "sponsorStringHouse": sponsor_string if bill_num.startswith("A") else None,
            "sponsorStringSenate": sponsor_string if bill_num.startswith("S") else None,
            "introduced": introduced_path or None,
            "digest": self._document_url(current_text.get("PDFLink")) or None,
            "summary": official_page,
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    clean_text(current_text.get("PDFLink")),
                    clean_text(current_text.get("Description")),
                    clean_text(last_action),
                    last_action_date,
                    chapter,
                    str(len(actions)),
                )
                if part
            ),
            "summaryHTML": self._summary_html(synopsis, code_description, fiscal_note),
            "digestHTML": self._actions_html(actions),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": official_page,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _current_session(self) -> int:
        if self._current_session_year is not None:
            return self._current_session_year
        response = self.client.get("/api/billSearch/sessions")
        response.raise_for_status()
        sessions = response.json()
        if isinstance(sessions, list) and sessions:
            first = sessions[0]
            try:
                self._current_session_year = int(first.get("value"))
            except (TypeError, ValueError, AttributeError):
                self._current_session_year = 0
        else:
            self._current_session_year = 0
        return self._current_session_year

    def _get_json(self, path: str) -> Any:
        response = self.client.get(path)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _bill_identifiers(detail_path: str, item: dict[str, Any] | None = None) -> tuple[str, int]:
        match = re.search(r"/bill-search/(?P<session>\d{4})/(?P<bill>[A-Z]+\d+)$", str(detail_path), re.IGNORECASE)
        if match is not None:
            bill_num = normalize_new_jersey_bill_number(match.group("bill"))
            session_year = int(match.group("session"))
            if bill_num:
                return bill_num, session_year
        fallback_bill = normalize_new_jersey_bill_number((item or {}).get("billNum"))
        fallback_year = _session_start_year(int((item or {}).get("year") or 0)) if (item or {}).get("year") else 0
        if fallback_bill and fallback_year:
            return fallback_bill, fallback_year
        raise ValueError(f"New Jersey bill identifiers could not be parsed from {detail_path}")

    @staticmethod
    def _history_actions(rows: Any) -> list[dict[str, str]]:
        if not isinstance(rows, list):
            return []
        actions: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            actions.append(
                {
                    "statusDate": parse_new_jersey_date(row.get("ActionDate")),
                    "location": "",
                    "statusMessage": clean_text(row.get("HistoryAction")),
                }
            )
        return actions

    @staticmethod
    def _summary_html(synopsis: str, code_description: str, fiscal_note: str) -> str:
        parts: list[str] = []
        if synopsis:
            parts.append(f"<p>{html.escape(synopsis)}</p>")
        if code_description:
            parts.append(f"<p><strong>Topic:</strong> {html.escape(code_description)}</p>")
        if fiscal_note:
            parts.append(f"<p><strong>Fiscal note:</strong> {html.escape(fiscal_note)}</p>")
        return "".join(parts)

    @staticmethod
    def _actions_html(actions: list[dict[str, str]]) -> str:
        return "".join(
            f"<p><strong>{html.escape(action['statusDate'])}</strong>: {html.escape(action['statusMessage'])}</p>"
            for action in actions[:6]
            if action.get("statusMessage")
        )

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        match = NEW_JERSEY_CHAPTER_PATTERN.search(raw)
        if match is None:
            return ""
        return match.group(1)

    def _chapter_from_actions(self, actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            chapter = self._chapter_from_text(action.get("statusMessage"))
            if chapter:
                return chapter
        return ""

    def _document_url(self, value: str | None) -> str:
        path = clean_text(value)
        if not path:
            return ""
        return absolute_url(self.settings.new_jersey_site_base, path) or path
