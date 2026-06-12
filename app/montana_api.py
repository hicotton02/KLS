from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


MONTANA_BILL_TYPE_IDS = [1, 2, 3, 4, 5, 6]
MONTANA_SEARCH_QUERY = (
    "includeCounts=true&sort=billType.sortOrder,desc&sort=billNumber,asc&sort=draft.draftNumber,asc"
)
MONTANA_BILL_NUMBER_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)(?P<number>\d+)$", re.IGNORECASE)
MONTANA_FILE_NUMBER_PATTERN = re.compile(r"^[A-Z]+(?P<number>\d{4})\.(?P<part>\d{3})\.", re.IGNORECASE)


def parse_montana_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "T" in raw:
        return raw.split("T", 1)[0]
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = MONTANA_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group("prefix"), int(match.group("number")))


class MontanaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.montana_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._sessions_by_year: dict[int, dict[str, Any]] = {}
        self._sessions_by_id: dict[int, dict[str, Any]] = {}
        self._legislator_names: dict[int, str] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session = self._session_for_year(year)
        response = self.client.post(
            f"/bills/v1/bills/search?{MONTANA_SEARCH_QUERY}&limit=5000&offset=0",
            json={"sessionIds": [session["id"]], "billTypeIds": MONTANA_BILL_TYPE_IDS},
        )
        response.raise_for_status()
        payload = response.json()
        items: list[dict[str, Any]] = []
        for row in payload.get("content") or []:
            bill_type = clean_text(((row.get("billType") or {}).get("code"))).upper()
            bill_number = clean_text(str(row.get("billNumber") or ""))
            draft_number = clean_text(((row.get("draft") or {}).get("draftNumber")) or "")
            if not bill_type or not bill_number or not draft_number:
                continue
            bill_num = f"{bill_type}{int(bill_number)}"
            latest_status = self._latest_status(row.get("draft", {}).get("billStatuses") or [])
            detail_path = absolute_url(
                self.settings.montana_site_base,
                f"/bills/v1/bills/findBySessionIdAndDraftNumber?sessionId={session['id']}&draftNumber={draft_number}",
            )
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_type,
                    "catchTitle": first_non_empty(row.get("draft", {}).get("shortTitle"), bill_num),
                    "billTitle": first_non_empty(row.get("draft", {}).get("shortTitle"), bill_num),
                    "sponsor": "",
                    "billStatus": clean_text(str((latest_status.get('billStatusCode') or {}).get("name") or "")),
                    "lastAction": clean_text(str((latest_status.get('billStatusCode') or {}).get("name") or "")),
                    "lastActionDate": parse_montana_date(str(latest_status.get("timeStamp") or "")),
                    "detailPath": detail_path,
                    "currentVersionPath": None,
                    "currentVersionFingerprint": "|".join(
                        part
                        for part in (
                            draft_number,
                            clean_text(str(row.get("versionNumber") or "")),
                            clean_text(str(row.get("sessionLawChapterNumber") or "")),
                        )
                        if part
                    ),
                }
            )

        deduped = {item["billNum"]: item for item in items}
        return sorted(deduped.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        source_item = item or {}
        session_id, draft_number = self._detail_params(detail_path, source_item)
        if not session_id or not draft_number:
            raise ValueError(f"Montana detail path could not be parsed from {detail_path}")

        response = self.client.get(
            "/bills/v1/bills/findBySessionIdAndDraftNumber",
            params={"sessionId": session_id, "draftNumber": draft_number},
        )
        response.raise_for_status()
        detail = response.json()
        session = self._session_by_id(session_id)

        bill_type = clean_text(((detail.get("billType") or {}).get("code"))).upper()
        bill_number = clean_text(str(detail.get("billNumber") or ""))
        bill_num = f"{bill_type}{int(bill_number)}" if bill_type and bill_number else clean_text(str(source_item.get("billNum") or ""))
        title = first_non_empty(detail.get("draft", {}).get("shortTitle"), clean_text(str(source_item.get("billTitle") or "")), bill_num)
        sponsor = self._legislator_name(int(detail["sponsorId"])) if detail.get("sponsorId") is not None else ""
        status_rows = self._status_rows(detail.get("draft", {}).get("billStatuses") or [])
        latest_action = status_rows[-1] if status_rows else {"statusDate": "", "statusMessage": ""}
        chapter = clean_text(
            str(detail.get("sessionLawChapterNumber") or (detail.get("sessionLawChapter") or {}).get("number") or "")
        )
        signed_date = parse_montana_date((detail.get("sessionLawChapter") or {}).get("assignedDate")) or (
            parse_montana_date(str(latest_action.get("statusDate") or "")) if chapter else ""
        )

        versions = self._bill_versions(
            legislature_ordinal=clean_text(str((session.get("legislature") or {}).get("ordinals") or "")),
            session_ordinal=clean_text(str(session.get("ordinals") or "")),
            bill_type=bill_type,
            bill_number=bill_number,
        )
        current_version = self._latest_document(versions)
        introduced_version = self._oldest_document(versions)
        current_version_path = self._document_link(current_version)
        introduced_path = self._document_link(introduced_version)
        amendments = self._amendments(
            bill_id=int(detail.get("id") or 0),
            legislature_ordinal=clean_text(str((session.get("legislature") or {}).get("ordinals") or "")),
            session_ordinal=clean_text(str(session.get("ordinals") or "")),
            bill_type=bill_type,
            bill_number=bill_number,
        )

        digest_parts: list[str] = []
        if latest_action.get("statusMessage"):
            digest_parts.append(f"<p>{html.escape(str(latest_action['statusMessage']))}</p>")
        if chapter:
            digest_parts.append(f"<p>{html.escape(f'Chapter {chapter}.')}</p>")

        return {
            "bill": bill_num,
            "billType": bill_type,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": first_non_empty(
                clean_text(str(latest_action.get("statusMessage") or "")),
                clean_text(str(source_item.get("billStatus") or "")),
            ),
            "lastAction": clean_text(str(latest_action.get("statusMessage") or "")),
            "lastActionDate": parse_montana_date(str(latest_action.get("statusDate") or "")),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(str((current_version or {}).get("fileName") or "")),
            "sponsorStringHouse": sponsor if bill_type.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_type.startswith("S") else None,
            "introduced": introduced_path,
            "digest": current_version_path,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    clean_text(str((current_version or {}).get("fileName") or "")),
                    current_version_path,
                    clean_text(str(latest_action.get("statusDate") or "")),
                    chapter,
                )
                if clean_text(str(part))
            ),
            "summaryHTML": f"<p>{html.escape(title)}</p>" if title else "",
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": "",
            "billActions": status_rows,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _session_for_year(self, year: int) -> dict[str, Any]:
        cached = self._sessions_by_year.get(year)
        if cached is not None:
            return cached
        response = self.client.get("/legislators/v1/sessions")
        response.raise_for_status()
        for session in response.json():
            ordinals = clean_text(str(session.get("ordinals") or ""))
            if session.get("type") == "REGULAR" and ordinals.startswith(str(year)):
                self._sessions_by_year[year] = session
                self._sessions_by_id[int(session["id"])] = session
                return session
        raise ValueError(f"Montana regular session was not found for {year}")

    def _session_by_id(self, session_id: int) -> dict[str, Any]:
        cached = self._sessions_by_id.get(session_id)
        if cached is not None:
            return cached
        response = self.client.get(f"/legislators/v1/sessions/{session_id}")
        response.raise_for_status()
        session = response.json()
        self._sessions_by_id[session_id] = session
        return session

    def _legislator_name(self, legislator_id: int) -> str:
        cached = self._legislator_names.get(legislator_id)
        if cached is not None:
            return cached
        response = self.client.get(f"/legislators/v1/legislators/{legislator_id}")
        response.raise_for_status()
        payload = response.json()
        name = clean_text(" ".join(part for part in [payload.get("firstName"), payload.get("lastName")] if part))
        self._legislator_names[legislator_id] = name
        return name

    @staticmethod
    def _detail_params(detail_path: str, item: dict[str, Any]) -> tuple[int, str]:
        session_id = 0
        draft_number = ""
        for candidate in (detail_path, str(item.get("detailPath") or "")):
            parsed = urlparse(candidate)
            query = parse_qs(parsed.query)
            session_text = clean_text(query.get("sessionId", [""])[0])
            draft_text = clean_text(query.get("draftNumber", [""])[0]).upper()
            if session_text.isdigit():
                session_id = int(session_text)
            if draft_text:
                draft_number = draft_text
            if session_id and draft_number:
                return session_id, draft_number
        return 0, ""

    @staticmethod
    def _latest_status(statuses: list[dict[str, Any]]) -> dict[str, Any]:
        if not statuses:
            return {}
        return sorted(
            statuses,
            key=lambda entry: clean_text(str(entry.get("timeStamp") or "")),
        )[-1]

    @staticmethod
    def _status_rows(statuses: list[dict[str, Any]]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for status in sorted(statuses, key=lambda entry: clean_text(str(entry.get("timeStamp") or ""))):
            code = status.get("billStatusCode") or {}
            message = clean_text(str(code.get("name") or ""))
            result = clean_text(str(status.get("result") or ""))
            if result:
                message = f"{message}: {result}"
            rows.append(
                {
                    "statusDate": parse_montana_date(str(status.get("timeStamp") or "")),
                    "statusMessage": message,
                    "location": clean_text(str(code.get("chamber") or "")),
                }
            )
        return rows

    def _bill_versions(
        self,
        *,
        legislature_ordinal: str,
        session_ordinal: str,
        bill_type: str,
        bill_number: str,
    ) -> list[dict[str, Any]]:
        response = self.client.get(
            "/docs/v1/documents/getBillVersions",
            params={
                "legislatureOrdinal": legislature_ordinal,
                "sessionOrdinal": session_ordinal,
                "billType": bill_type,
                "billNumber": bill_number,
            },
        )
        response.raise_for_status()
        return list(response.json() or [])

    def _amendments(
        self,
        *,
        bill_id: int,
        legislature_ordinal: str,
        session_ordinal: str,
        bill_type: str,
        bill_number: str,
    ) -> list[dict[str, Any]]:
        if bill_id <= 0:
            return []

        meta_response = self.client.get("/bills/v1/amendments/findByBillId", params={"billId": bill_id})
        docs_response = self.client.get(
            "/docs/v1/documents/getBillAmendments",
            params={
                "legislatureOrdinal": legislature_ordinal,
                "sessionOrdinal": session_ordinal,
                "billType": bill_type,
                "billNumber": bill_number,
            },
        )
        meta_response.raise_for_status()
        docs_response.raise_for_status()

        docs_by_number: dict[int, dict[str, Any]] = {}
        for document in docs_response.json() or []:
            number = self._file_part_number(clean_text(str(document.get("fileName") or "")))
            if number is None:
                continue
            existing = docs_by_number.get(number)
            if existing is None or self._document_sort_key(document) > self._document_sort_key(existing):
                docs_by_number[number] = document

        amendments: list[dict[str, Any]] = []
        seen_numbers: set[int] = set()
        for amendment in sorted(meta_response.json() or [], key=lambda entry: int(entry.get("number") or 0)):
            number = int(amendment.get("number") or 0)
            if number <= 0 or number in seen_numbers:
                continue
            seen_numbers.add(number)
            document = docs_by_number.get(number)
            amendments.append(
                {
                    "amendmentNumber": f"Amendment {number}",
                    "house": clean_text(str((amendment.get("bill") or {}).get("billType", {}).get("chamber") or "")),
                    "order": str(number),
                    "sequence": clean_text(str(amendment.get("billVersion") or "")),
                    "status": clean_text(str(amendment.get("type") or "")),
                    "sponsor": "",
                    "documentUrl": self._document_link(document),
                }
            )
        return amendments

    @staticmethod
    def _document_link(document: dict[str, Any] | None) -> str | None:
        if not document:
            return None
        for attribute in document.get("attributes") or []:
            if clean_text(str(attribute.get("name") or "")) == "DocumentLink":
                return clean_text(str(attribute.get("stringValue") or "")) or None
        return None

    @staticmethod
    def _document_sort_key(document: dict[str, Any]) -> tuple[str, str, int]:
        return (
            clean_text(str(document.get("date") or "")),
            clean_text(str(document.get("creation") or "")),
            int(document.get("id") or 0),
        )

    def _latest_document(self, documents: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not documents:
            return None
        return sorted(documents, key=self._document_sort_key)[-1]

    def _oldest_document(self, documents: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not documents:
            return None
        return sorted(documents, key=self._document_sort_key)[0]

    @staticmethod
    def _file_part_number(file_name: str) -> int | None:
        match = MONTANA_FILE_NUMBER_PATTERN.match(file_name)
        if match is None:
            return None
        return int(match.group("part"))
