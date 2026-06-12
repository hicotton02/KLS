from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


PENNSYLVANIA_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HR|SR|HCR|SCR|HJR|SJR)\d+$", re.IGNORECASE)
PENNSYLVANIA_CHAPTER_PATTERN = re.compile(r"\bAct(?:s)?\s*(?:Ch\.|No\.)\s*([0-9A-Z-]+)\b", re.IGNORECASE)


def parse_pennsylvania_date(value: str | None) -> str:
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


def normalize_pennsylvania_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    match = re.fullmatch(r"([A-Z]+)0*(\d+)", raw)
    if match is None:
        return ""
    normalized = f"{match.group(1)}{int(match.group(2))}"
    if PENNSYLVANIA_BILL_NUMBER_PATTERN.fullmatch(normalized):
        return normalized
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 1 else value - 1


def _bill_prefix(body: str, subtype: str) -> str:
    normalized_body = clean_text(body).upper()
    normalized_subtype = clean_text(subtype).upper()
    if normalized_subtype in {"B", "A"}:
        return f"{normalized_body}B"
    if normalized_subtype == "J":
        return f"{normalized_body}JR"
    if normalized_subtype == "C":
        return f"{normalized_body}CR"
    if normalized_subtype == "R":
        return f"{normalized_body}R"
    return f"{normalized_body}{normalized_subtype}"


def _sequence_int(value: str | None) -> int:
    raw = clean_text(value)
    if not raw.isdigit():
        return 999999
    return int(raw)


class PennsylvaniaApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.pennsylvania_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._bill_cache: dict[int, list[dict[str, Any]]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_year = _session_start_year(year)
        records = self._load_session_records(session_year)
        items: list[dict[str, Any]] = []
        for record in records:
            items.append(
                {
                    "billNum": record["billNum"],
                    "billType": record["billType"],
                    "catchTitle": record["catchTitle"],
                    "billTitle": record["billTitle"],
                    "sponsor": record["sponsor"],
                    "billStatus": record["billStatus"],
                    "lastAction": record["lastAction"],
                    "lastActionDate": record["lastActionDate"],
                    "signedDate": record["signedDate"],
                    "chapter": record["chapter"],
                    "detailPath": record["detailPath"],
                    "currentVersionPath": record["currentVersionPath"],
                    "currentVersionFingerprint": record["currentVersionFingerprint"],
                    "sourceRecord": record,
                }
            )
        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        record = item.get("sourceRecord") if isinstance(item, dict) else None
        if not isinstance(record, dict):
            record = self._record_from_detail_path(detail_path)
        if not isinstance(record, dict):
            raise ValueError(f"Pennsylvania bill detail could not be resolved from {detail_path}")
        return dict(record)

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _record_from_detail_path(self, detail_path: str) -> dict[str, Any] | None:
        match = re.search(r"/legislation/bills/(?P<year>\d{4})/(?P<bill>[A-Za-z]+\d+)", detail_path, re.IGNORECASE)
        if match is None:
            return None
        session_year = _session_start_year(int(match.group("year")))
        bill_num = normalize_pennsylvania_bill_number(match.group("bill"))
        if not bill_num:
            return None
        for record in self._load_session_records(session_year):
            if record["billNum"] == bill_num:
                return record
        return None

    def _load_session_records(self, session_year: int) -> list[dict[str, Any]]:
        cached = self._bill_cache.get(session_year)
        if cached is not None:
            return cached

        response = self.client.get(f"/data/file?documentType=BillHistoryData&session={session_year}_0")
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        try:
            xml_name = archive.namelist()[0]
            root = ET.fromstring(archive.read(xml_name))
        finally:
            archive.close()

        session = root.find("session")
        if session is None:
            raise ValueError("Pennsylvania bill history export did not contain a session node")

        records = [self._parse_bill_node(session_year, node) for node in session.findall("bill")]
        self._bill_cache[session_year] = records
        return records

    def _parse_bill_node(self, session_year: int, node: ET.Element) -> dict[str, Any]:
        body = clean_text(node.findtext("body")).upper()
        subtype = clean_text(node.findtext("subType")).upper()
        number_value = clean_text(node.findtext("number"))
        bill_prefix = _bill_prefix(body, subtype)
        bill_num = normalize_pennsylvania_bill_number(f"{bill_prefix}{number_value}")
        if not bill_num:
            raise ValueError("Pennsylvania bill number could not be parsed")

        short_title = clean_text(node.findtext("shortTitle")) or bill_num
        sponsors = self._sponsors(node.find("sponsors"))
        sponsor = sponsors[0] if sponsors else ""
        sponsor_string = ", ".join(sponsors) if sponsors else None

        printer_rows = self._printer_rows(node.find("printersNumberHistory"))
        current_version = printer_rows[0] if printer_rows else {}
        introduced_version = printer_rows[-1] if printer_rows else {}

        actions = self._action_rows(node.find("actionHistory"))
        latest_action = actions[-1] if actions else {}
        last_action = clean_text(latest_action.get("statusMessage"))
        last_action_date = clean_text(latest_action.get("statusDate"))
        chapter = self._chapter_from_actions(actions)
        signed_date = last_action_date if chapter else ""

        memo = node.find("cosponsorshipMemo")
        memo_title = clean_text(memo.text if memo is not None else "")
        memo_url = clean_text(memo.attrib.get("memoUrl") if memo is not None else "")

        amendments = self._amendments(node.find("amendments"))

        detail_path = f"{self.settings.pennsylvania_site_base.rstrip('/')}/legislation/bills/{session_year}/{bill_num}"
        digest_bits: list[str] = []
        if memo_title:
            digest_bits.append(f"Co-sponsorship memo: {memo_title}")
        if last_action:
            digest_bits.append(f"Latest action: {last_action}")
        if chapter:
            digest_bits.append(f"Act number: {chapter}")

        return {
            "bill": bill_num,
            "billNum": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": short_title,
            "sponsor": sponsor,
            "billTitle": short_title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": bill_num,
            "sponsorStringHouse": sponsor_string if body == "H" else None,
            "sponsorStringSenate": sponsor_string if body == "S" else None,
            "introduced": introduced_version.get("url"),
            "digest": memo_url or detail_path,
            "summary": detail_path,
            "detailPath": detail_path,
            "currentVersionPath": current_version.get("url"),
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version.get("url") or "",
                    current_version.get("number") or "",
                    last_action,
                    last_action_date,
                    chapter,
                    str(len(amendments)),
                )
                if part
            ),
            "summaryHTML": f"<p>{short_title}</p>",
            "digestHTML": "".join(f"<p>{bit}</p>" for bit in digest_bits if bit),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": detail_path,
        }

    @staticmethod
    def _sponsors(node: ET.Element | None) -> list[str]:
        if node is None:
            return []
        sponsors: list[tuple[int, str]] = []
        for sponsor in node.findall("sponsor"):
            name = clean_text(sponsor.text)
            if not name:
                continue
            sponsors.append((_sequence_int(sponsor.attrib.get("sequenceNumber")), name))
        sponsors.sort(key=lambda item: (item[0], item[1]))
        return [name for _, name in sponsors]

    def _printer_rows(self, node: ET.Element | None) -> list[dict[str, str]]:
        if node is None:
            return []
        rows: list[tuple[int, dict[str, str]]] = []
        for item in node.findall("number"):
            printer_number = clean_text(item.text)
            url = clean_text(item.attrib.get("billTextPdfUrl"))
            rows.append(
                (
                    _sequence_int(item.attrib.get("sequence")),
                    {
                        "number": printer_number,
                        "url": url,
                    },
                )
            )
        rows.sort(key=lambda entry: entry[0])
        return [row for _, row in rows]

    @staticmethod
    def _action_rows(node: ET.Element | None) -> list[dict[str, str]]:
        if node is None:
            return []
        rows: list[tuple[int, dict[str, str]]] = []
        for action in node.findall("action"):
            full_action = clean_text(action.findtext("fullAction")) or clean_text(action.findtext("verb"))
            committee = clean_text(action.findtext("committee"))
            chamber = clean_text(action.attrib.get("actionChamber"))
            rows.append(
                (
                    _sequence_int(action.attrib.get("sequence")),
                    {
                        "statusDate": parse_pennsylvania_date(action.findtext("date")),
                        "statusMessage": full_action,
                        "location": committee or chamber,
                    },
                )
            )
        rows.sort(key=lambda entry: entry[0])
        return [row for _, row in rows]

    @staticmethod
    def _amendments(node: ET.Element | None) -> list[dict[str, Any]]:
        if node is None:
            return []
        amendments: list[dict[str, Any]] = []
        for index, amendment in enumerate(node.findall("amendment"), start=1):
            number = clean_text(amendment.attrib.get("number"))
            chamber = clean_text(amendment.attrib.get("chamber")).upper()
            amendment_number = clean_text(amendment.text) or (f"{chamber}A{number}" if chamber and number else "")
            if not amendment_number:
                continue
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": chamber or None,
                    "order": clean_text(amendment.attrib.get("date")),
                    "sequence": f"{index:04d}",
                    "status": clean_text(amendment.attrib.get("date")),
                    "sponsor": "",
                    "documentUrl": clean_text(amendment.attrib.get("amendmentUrl"))
                    or clean_text(amendment.attrib.get("aicUrl")),
                }
            )
        return amendments

    @staticmethod
    def _chapter_from_actions(actions: list[dict[str, str]]) -> str:
        for row in reversed(actions):
            match = PENNSYLVANIA_CHAPTER_PATTERN.search(str(row.get("statusMessage") or ""))
            if match is not None:
                return clean_text(match.group(1))
        return ""
