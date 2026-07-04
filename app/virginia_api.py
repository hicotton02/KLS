from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


VIRGINIA_WEBAPI_KEY = "FCE351B6-9BD8-46E0-B18F-5572F4CCA5B9"
VIRGINIA_BILLS_CSV_TEMPLATE = "https://lis.blob.core.windows.net/lisfiles/{session_code}/BILLS.CSV"
VIRGINIA_BILL_NUMBER_PATTERN = re.compile(r"^[A-Z]{1,4}\d+$", re.IGNORECASE)
VIRGINIA_CHAPTER_PATTERN = re.compile(r"\bCHAP\d+\b", re.IGNORECASE)


def parse_virginia_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_virginia_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if VIRGINIA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _bill_type(bill_num: str) -> str:
    match = re.fullmatch(r"([A-Z]+)\d+", str(bill_num or "").strip().upper())
    return match.group(1) if match else ""


def _document_url(entry: dict[str, Any] | None) -> str:
    if not isinstance(entry, dict):
        return ""
    for key in ("HTMLFile", "PDFFile", "JSONFile", "LinkFile"):
        files = entry.get(key)
        if isinstance(files, list):
            for file_item in files:
                url = clean_text(str((file_item or {}).get("FileURL") or ""))
                if url:
                    return url
    return clean_text(str(entry.get("DocURL") or entry.get("LinkURL") or ""))


def _latest_action_from_row(row: dict[str, str]) -> tuple[str, str]:
    candidates: list[tuple[str, int, str]] = []
    for priority, (action_key, date_key) in enumerate(
        (
            ("Last_house_action", "Last_house_action_date"),
            ("Last_senate_action", "Last_senate_action_date"),
            ("Last_conference_action", "Last_conference_action_date"),
            ("Last_governor_action", "Last_governor_action_date"),
        ),
        start=1,
    ):
        action_text = clean_text(row.get(action_key))
        action_date = parse_virginia_date(row.get(date_key))
        if not action_text:
            continue
        candidates.append((action_date or "", priority, action_text))

    if not candidates:
        return "", ""

    action_date, _, action_text = max(candidates, key=lambda item: (item[0], item[1]))
    return action_text, action_date


def _extract_chapter(value: str | None) -> str:
    raw = clean_text(value)
    match = VIRGINIA_CHAPTER_PATTERN.search(raw)
    if match is None:
        return raw
    return match.group(0).upper()


class VirginiaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.virginia_site_base,
            headers={
                "User-Agent": "keeping-law-simple/1.0",
                "WebAPIKey": VIRGINIA_WEBAPI_KEY,
                "Accept": "application/json, text/plain, */*",
            },
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._default_session: dict[str, Any] | None = None

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = self.session_code_for_year(year)
        response = self.client.get(VIRGINIA_BILLS_CSV_TEMPLATE.format(session_code=session_code))
        response.raise_for_status()

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        reader = csv.DictReader(io.StringIO(response.text))

        for row in reader:
            bill_num = normalize_virginia_bill_number(row.get("Bill_id"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)

            title = clean_text(row.get("Bill_description")) or bill_num
            sponsor = clean_text(row.get("Patron_name"))
            last_action, last_action_date = _latest_action_from_row(row)
            chapter = first_non_empty(
                _extract_chapter(row.get("Chapter_id")),
                _extract_chapter(row.get("Last_governor_action")),
            )
            bill_status = first_non_empty(clean_text(row.get("Last_governor_action")), last_action)
            signed_date = ""
            if row.get("Approved") == "Y" and last_action_date:
                signed_date = last_action_date

            items.append(
                {
                    "billNum": bill_num,
                    "billType": _bill_type(bill_num),
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": bill_status,
                    "lastAction": last_action or bill_status,
                    "lastActionDate": last_action_date,
                    "signedDate": signed_date,
                    "effectiveDate": "",
                    "chapter": chapter,
                    "enrolledNumber": first_non_empty(
                        clean_text(row.get("Full_text_doc6")),
                        clean_text(row.get("Full_text_doc5")),
                        clean_text(row.get("Full_text_doc4")),
                        clean_text(row.get("Full_text_doc3")),
                        clean_text(row.get("Full_text_doc2")),
                        clean_text(row.get("Full_text_doc1")),
                    ),
                    "sessionCode": session_code,
                    "detailPath": bill_num,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, year: int, bill_num: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_bill = normalize_virginia_bill_number(bill_num)
        if not normalized_bill:
            raise ValueError(f"Virginia bill number could not be parsed from {bill_num}")

        session_code = self.session_code_for_year(year)
        legislation = self._fetch_legislation(session_code, normalized_bill)
        if not legislation and item is not None:
            return self._fallback_detail_from_item(year, session_code, normalized_bill, item)
        if not legislation:
            raise ValueError(f"Virginia legislation detail did not include {normalized_bill}")
        legislation_id = int(legislation["LegislationID"])
        summaries = self._fetch_summaries(session_code, normalized_bill)
        texts = self._fetch_texts(session_code, legislation_id)
        history = self._fetch_history(session_code, legislation_id)

        latest_summary = self._latest_summary(summaries)
        current_text = self._current_text(texts)
        introduced_text = self._introduced_text(texts)
        latest_event = self._latest_event(history)
        sponsors = self._patron_names(legislation.get("Patrons") or [])
        sponsor = ", ".join(sponsors)
        bill_title = first_non_empty(
            clean_text(str(legislation.get("LegislationTitle") or "")),
            clean_text(str(current_text.get("DraftTitle") or "")),
            clean_text(str(legislation.get("Description") or "")),
            normalized_bill,
        )
        catch_title = first_non_empty(
            clean_text(str(legislation.get("Description") or "")),
            clean_text(str(legislation.get("LegislationTitle") or "")),
            normalized_bill,
        )
        summary_html = clean_text(str(latest_summary.get("Summary") or ""))
        current_bill_html = str(current_text.get("DraftText") or "")
        chapter = first_non_empty(
            _extract_chapter(str(legislation.get("ChapterNumber") or "")),
            _extract_chapter(str(current_text.get("DocumentCode") or "")),
            _extract_chapter(str(legislation.get("LegislationStatus") or "")),
        )
        last_action = first_non_empty(
            clean_text(str(latest_event.get("Description") or "")),
            clean_text(str(legislation.get("LegislationStatus") or "")),
        )
        last_action_date = parse_virginia_date(str(latest_event.get("EventDate") or ""))
        signed_date = last_action_date if chapter and last_action_date else ""
        current_version_path = _document_url(current_text)
        introduced_path = _document_url(introduced_text)

        return {
            "bill": normalized_bill,
            "billType": _bill_type(normalized_bill),
            "catchTitle": catch_title,
            "sponsor": sponsor,
            "billTitle": bill_title,
            "billStatus": clean_text(str(legislation.get("LegislationStatus") or "")) or last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": first_non_empty(
                clean_text(str(current_text.get("DocumentCode") or "")),
                clean_text(str(current_text.get("LegislationVersion") or "")),
            ),
            "sponsorStringHouse": sponsor if str(legislation.get("ChamberCode") or "").upper() == "H" else None,
            "sponsorStringSenate": sponsor if str(legislation.get("ChamberCode") or "").upper() == "S" else None,
            "introduced": introduced_path or None,
            "digest": self.bill_url(session_code, normalized_bill),
            "summary": self.bill_url(session_code, normalized_bill),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    clean_text(str(current_text.get("DocumentCode") or "")),
                    clean_text(str(current_text.get("LegislationVersion") or "")),
                    clean_text(str(current_text.get("VersionDate") or "")),
                    current_version_path,
                )
                if part
            ),
            "summaryHTML": summary_html or self._paragraph_html(catch_title),
            "digestHTML": self._paragraph_html(bill_title),
            "currentBillHTML": current_bill_html,
            "billActions": self._history_rows(history),
            "amendments": self._amendments_from_texts(texts),
            "officialPage": self.bill_url(session_code, normalized_bill),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def session_code_for_year(self, year: int) -> str:
        default_session = self._get_default_session()
        default_code = clean_text(str(default_session.get("SessionCode") or ""))
        if default_code.startswith(str(year)):
            return default_code
        return f"{year}1"

    def bill_url(self, session_code: str, bill_num: str) -> str:
        return f"{self.settings.virginia_site_base.rstrip('/')}/bill-details/{session_code}/{bill_num}"

    def _get_default_session(self) -> dict[str, Any]:
        if self._default_session is None:
            response = self.client.get("/Session/api/getDefaultSessionAsync")
            response.raise_for_status()
            self._default_session = response.json()
        return self._default_session

    def _fetch_legislation(self, session_code: str, bill_num: str) -> dict[str, Any]:
        response = self.client.post(
            "/AdvancedLegislationSearch/api/GetLegislationListAsync",
            headers={"content-type": "application/json; charset=utf-8"},
            json={
                "SessionCode": session_code,
                "LegislationNumbers": [{"LegislationNumber": bill_num}],
            },
        )
        response.raise_for_status()
        payload = self._safe_json(response)
        items = payload.get("Legislations") or []
        return dict(items[0]) if items else {}

    def _fetch_summaries(self, session_code: str, bill_num: str) -> list[dict[str, Any]]:
        response = self.client.get(
            "/LegislationSummary/api/GetLegislationSummaryListAsync",
            params={"sessionCode": session_code, "legislationNumber": bill_num},
        )
        response.raise_for_status()
        payload = self._safe_json(response)
        return [dict(item) for item in payload.get("LegislationSummaries") or []]

    def _fetch_texts(self, session_code: str, legislation_id: int) -> list[dict[str, Any]]:
        response = self.client.get(
            "/LegislationText/api/GetLegislationTextByIDAsync",
            params={"isPublic": "true", "legislationID": str(legislation_id), "sessionCode": session_code},
        )
        response.raise_for_status()
        payload = self._safe_json(response)
        return [dict(item) for item in payload.get("TextsList") or []]

    def _fetch_history(self, session_code: str, legislation_id: int) -> list[dict[str, Any]]:
        response = self.client.get(
            "/LegislationEvent/api/GetPublicLegislationEventHistoryListAsync",
            params={"legislationID": str(legislation_id), "sessionCode": session_code},
        )
        response.raise_for_status()
        payload = self._safe_json(response)
        return [dict(item) for item in payload.get("LegislationEvents") or []]

    @staticmethod
    def _latest_summary(summaries: list[dict[str, Any]]) -> dict[str, Any]:
        if not summaries:
            return {}
        return max(
            summaries,
            key=lambda item: (
                clean_text(str(item.get("SummaryDate") or "")),
                1 if item.get("IsActive") else 0,
                clean_text(str(item.get("SummaryVersion") or "")),
            ),
        )

    @staticmethod
    def _current_text(texts: list[dict[str, Any]]) -> dict[str, Any]:
        if not texts:
            return {}
        return max(
            texts,
            key=lambda item: (
                clean_text(str(item.get("VersionDate") or item.get("DraftDate") or "")),
                1 if item.get("IsActive") else 0,
                int(item.get("LegislationVersionID") or 0),
            ),
        )

    @staticmethod
    def _introduced_text(texts: list[dict[str, Any]]) -> dict[str, Any]:
        for item in texts:
            if "introduc" in clean_text(str(item.get("LegislationVersion") or "")).lower():
                return item
        if not texts:
            return {}
        return min(
            texts,
            key=lambda item: (
                clean_text(str(item.get("VersionDate") or item.get("DraftDate") or "")),
                int(item.get("LegislationVersionID") or 0),
            ),
        )

    @staticmethod
    def _latest_event(history: list[dict[str, Any]]) -> dict[str, Any]:
        if not history:
            return {}
        return max(
            history,
            key=lambda item: (
                clean_text(str(item.get("EventDate") or "")),
                int(item.get("Sequence") or 0),
            ),
        )

    @staticmethod
    def _patron_names(patrons: list[dict[str, Any]]) -> list[str]:
        items: list[str] = []
        for patron in patrons:
            name = clean_text(str(patron.get("PatronDisplayName") or patron.get("MemberDisplayName") or ""))
            if name and name not in items:
                items.append(name)
        return items

    @staticmethod
    def _history_rows(history: list[dict[str, Any]]) -> list[dict[str, str]]:
        rows = []
        for item in sorted(
            history,
            key=lambda entry: (
                clean_text(str(entry.get("EventDate") or "")),
                int(entry.get("Sequence") or 0),
            ),
        ):
            description = clean_text(str(item.get("Description") or ""))
            if not description:
                continue
            rows.append(
                {
                    "statusDate": parse_virginia_date(str(item.get("EventDate") or "")),
                    "statusMessage": description,
                    "location": first_non_empty(
                        clean_text(str(item.get("CommitteeName") or "")),
                        clean_text(str(item.get("ParentCommitteeName") or "")),
                        clean_text(str(item.get("ActorType") or "")),
                    ),
                }
            )
        return rows

    @staticmethod
    def _amendments_from_texts(texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        sequence = 1
        seen_numbers: set[str] = set()
        for item in sorted(
            texts,
            key=lambda entry: (
                clean_text(str(entry.get("VersionDate") or entry.get("DraftDate") or "")),
                int(entry.get("LegislationVersionID") or 0),
            ),
        ):
            version_label = first_non_empty(
                clean_text(str(item.get("Description") or "")),
                clean_text(str(item.get("LegislationVersion") or "")),
            )
            lowered = version_label.lower()
            if not any(keyword in lowered for keyword in ("amendment", "conference", "recommendation", "governor substitute")):
                continue
            amendment_number = first_non_empty(
                clean_text(str(item.get("DocumentCode") or "")),
                clean_text(str(item.get("LegislationVersion") or "")),
                f"VA-{item.get('LegislationTextID')}",
            )
            if amendment_number in seen_numbers:
                continue
            seen_numbers.add(amendment_number)
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": clean_text(str(item.get("ChamberCode") or "")),
                    "order": parse_virginia_date(str(item.get("VersionDate") or item.get("DraftDate") or "")),
                    "sequence": str(sequence),
                    "status": version_label,
                    "sponsor": clean_text(str(item.get("Sponsor") or "")),
                    "documentUrl": _document_url(item),
                }
            )
            sequence += 1
        return amendments

    @staticmethod
    def _safe_json(response: httpx.Response) -> dict[str, Any]:
        if not clean_text(response.text):
            return {}
        try:
            payload = response.json()
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _paragraph_html(value: str | None) -> str:
        text = clean_text(value)
        if not text:
            return ""
        return f"<p>{text}</p>"

    def _fallback_detail_from_item(
        self,
        year: int,
        session_code: str,
        bill_num: str,
        item: dict[str, Any],
    ) -> dict[str, Any]:
        title = first_non_empty(
            clean_text(str(item.get("billTitle") or "")),
            clean_text(str(item.get("catchTitle") or "")),
            bill_num,
        )
        last_action = first_non_empty(clean_text(str(item.get("lastAction") or "")), clean_text(str(item.get("billStatus") or "")))
        last_action_date = clean_text(str(item.get("lastActionDate") or ""))
        action_rows = []
        if last_action or last_action_date:
            action_rows.append({"statusDate": last_action_date, "statusMessage": last_action, "location": ""})
        return {
            "bill": bill_num,
            "billType": _bill_type(bill_num),
            "catchTitle": title,
            "sponsor": clean_text(str(item.get("sponsor") or "")),
            "billTitle": title,
            "billStatus": first_non_empty(clean_text(str(item.get("billStatus") or "")), last_action),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": clean_text(str(item.get("signedDate") or "")),
            "effectiveDate": clean_text(str(item.get("effectiveDate") or "")),
            "chapter": clean_text(str(item.get("chapter") or "")),
            "enrolledNumber": clean_text(str(item.get("enrolledNumber") or "")),
            "sponsorStringHouse": clean_text(str(item.get("sponsor") or "")) if bill_num.startswith("H") else None,
            "sponsorStringSenate": clean_text(str(item.get("sponsor") or "")) if bill_num.startswith("S") else None,
            "introduced": None,
            "digest": self.bill_url(session_code, bill_num),
            "summary": self.bill_url(session_code, bill_num),
            "currentVersionPath": None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    clean_text(str(item.get("enrolledNumber") or "")),
                    last_action,
                    last_action_date,
                    clean_text(str(year)),
                )
                if part
            ),
            "summaryHTML": self._paragraph_html(title),
            "digestHTML": self._paragraph_html(last_action),
            "currentBillHTML": "",
            "billActions": action_rows,
            "amendments": [],
            "officialPage": self.bill_url(session_code, bill_num),
        }
