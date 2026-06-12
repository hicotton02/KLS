from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


KENTUCKY_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HR|SR|HJR|SJR)\d+$", re.IGNORECASE)
KENTUCKY_CHAPTER_PATTERN = re.compile(r"\bActs?\s+Ch\.\s*([0-9A-Z-]+)\b", re.IGNORECASE)


def parse_kentucky_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_kentucky_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = re.fullmatch(
        r"(HOUSE|SENATE)\s+(BILL|CONCURRENT\s+RESOLUTION|JOINT\s+RESOLUTION|RESOLUTION)\s+0*(\d+)",
        raw,
    )
    if match is not None:
        chamber = "H" if match.group(1) == "HOUSE" else "S"
        kind = match.group(2)
        number = int(match.group(3))
        if kind == "BILL":
            return f"{chamber}B{number}"
        if kind == "CONCURRENT RESOLUTION":
            return f"{chamber}CR{number}"
        if kind == "JOINT RESOLUTION":
            return f"{chamber}JR{number}"
        if kind == "RESOLUTION":
            return f"{chamber}R{number}"
    compact = raw.replace(" ", "")
    match = re.fullmatch(r"([A-Z]+)0*(\d+)", compact)
    if match is None:
        return ""
    normalized = f"{match.group(1)}{int(match.group(2))}"
    if KENTUCKY_BILL_NUMBER_PATTERN.fullmatch(normalized):
        return normalized
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _session_code(year: int) -> str:
    return f"{int(year) % 100:02d}rs"


class KentuckyApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.kentucky_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = _session_code(year)
        response = self.client.get(f"/record/{session_code}/all_bills_resolutions_title.html")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if table is None:
            raise ValueError("Kentucky bill listing table was not found")

        items_by_bill: dict[str, dict[str, Any]] = {}
        for row in table.find_all("tr")[1:]:
            cells = row.find_all(["th", "td"])
            if len(cells) < 3:
                continue
            bill_link = cells[0].find("a", href=True)
            bill_label = clean_text(cells[0].get_text(" ", strip=True))
            bill_num = normalize_kentucky_bill_number(bill_label)
            if not bill_num:
                continue
            detail_path = absolute_url(str(response.url), bill_link.get("href") if bill_link is not None else "")
            title = clean_text(cells[2].get_text(" ", strip=True)) or bill_num
            sponsor = clean_text(cells[1].get_text(" ", strip=True))
            items_by_bill[bill_num] = {
                "billNum": bill_num,
                "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                "catchTitle": title,
                "billTitle": title,
                "sponsor": sponsor,
                "billStatus": "",
                "lastAction": "",
                "lastActionDate": "",
                "detailPath": detail_path,
                "currentVersionPath": None,
                "currentVersionFingerprint": "|".join(part for part in (detail_path or "", sponsor, title) if part),
            }

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title_tag = soup.find("title")
        bill_num = normalize_kentucky_bill_number(clean_text(title_tag.get_text(" ", strip=True)) if title_tag else "")
        bill_num = first_non_empty(bill_num, normalize_kentucky_bill_number((item or {}).get("billNum")))
        if not bill_num:
            raise ValueError(f"Kentucky bill number could not be determined from {detail_path}")

        tables = soup.find_all("table")
        if len(tables) < 2:
            raise ValueError(f"Kentucky bill detail tables were missing for {detail_path}")

        info_rows = self._info_rows(tables[0])
        history_rows = self._history_rows(tables[1])

        last_action = first_non_empty(info_rows.get("last action", {}).get("text"), history_rows[-1]["statusMessage"] if history_rows else "")
        last_action_date = first_non_empty(
            parse_kentucky_date(info_rows.get("last action", {}).get("date")),
            history_rows[-1]["statusDate"] if history_rows else "",
        )
        chapter = self._chapter(last_action)
        signed_date = last_action_date if chapter else ""

        document_row = info_rows.get("bill documents", {})
        current_version_path = absolute_url(
            str(response.url),
            self._pick_document_link(document_row.get("links", []), include=("current", "final")),
        )
        introduced_path = absolute_url(
            str(response.url),
            self._pick_document_link(document_row.get("links", []), include=("introduced",)),
        ) or current_version_path
        fiscal_row = info_rows.get("fiscal impact statement", {})
        digest_path = absolute_url(str(response.url), self._pick_first_link(fiscal_row.get("links", []))) or f"{detail_path}#amendments"

        amendment_tables = tables[2:]
        amendments = self._amendments(amendment_tables, response)

        digest_bits: list[str] = []
        if last_action:
            digest_bits.append(f"Latest action: {last_action}")
        bill_request = info_rows.get("bill request number", {}).get("text")
        if bill_request:
            digest_bits.append(f"Bill request number: {bill_request}")
        fiscal_text = fiscal_row.get("text")
        if fiscal_text:
            digest_bits.append(f"Fiscal impact: {fiscal_text}")
        if amendments:
            digest_bits.append(f"Official amendment entries: {len(amendments)}")

        sponsor = clean_text((item or {}).get("sponsor"))
        sponsor_string_house = sponsor if bill_num.startswith("H") else None
        sponsor_string_senate = sponsor if bill_num.startswith("S") else None

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": first_non_empty(info_rows.get("title", {}).get("text"), clean_text((item or {}).get("catchTitle")), bill_num),
            "sponsor": sponsor,
            "billTitle": first_non_empty(info_rows.get("title", {}).get("text"), clean_text((item or {}).get("billTitle")), bill_num),
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": bill_num,
            "sponsorStringHouse": sponsor_string_house,
            "sponsorStringSenate": sponsor_string_senate,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": detail_path,
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part for part in (current_version_path or "", last_action, last_action_date, chapter, str(len(amendments))) if part
            ),
            "summaryHTML": f"<p>{first_non_empty(info_rows.get('title', {}).get('text'), bill_num)}</p>",
            "digestHTML": "".join(f"<p>{bit}</p>" for bit in digest_bits if bit),
            "currentBillHTML": "",
            "billActions": history_rows,
            "amendments": amendments,
            "officialPage": detail_path,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _info_rows(table: Tag) -> dict[str, dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":").lower()
            links = [
                {
                    "label": clean_text(link.get_text(" ", strip=True)),
                    "url": link.get("href"),
                }
                for link in cells[1].find_all("a", href=True)
            ]
            text = clean_text(cells[1].get_text(" ", strip=True))
            rows[label] = {
                "text": text,
                "links": links,
                "date": parse_kentucky_date(text.split(":", 1)[0]) if label == "last action" else "",
            }
        return rows

    @staticmethod
    def _history_rows(table: Tag) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) < 2:
                continue
            first = clean_text(cells[0].get_text(" ", strip=True))
            second = clean_text(cells[1].get_text(" ", strip=True))
            if first.lower() == "date" or not first or not second:
                continue
            rows.append(
                {
                    "statusDate": parse_kentucky_date(first),
                    "statusMessage": second,
                    "location": "",
                }
            )
        return rows

    def _amendments(self, tables: list[Tag], response: httpx.Response) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        for index, table in enumerate(tables, start=1):
            rows = self._info_rows(table)
            label = rows.get("amendment", {}).get("text")
            if not label:
                continue
            amendment_link = self._pick_first_link(rows.get("amendment", {}).get("links", []))
            if not amendment_link:
                continue
            amendment_number = self._amendment_number(label, index)
            sponsor = rows.get("sponsor", {}).get("text") or ""
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": "H" if label.lower().startswith("house ") else ("S" if label.lower().startswith("senate ") else None),
                    "order": label,
                    "sequence": f"{index:04d}",
                    "status": rows.get("summary", {}).get("text") or "",
                    "sponsor": sponsor,
                    "documentUrl": absolute_url(str(response.url), amendment_link) or amendment_link,
                }
            )
        return amendments

    @staticmethod
    def _pick_document_link(links: list[dict[str, Any]], *, include: tuple[str, ...]) -> str | None:
        normalized_tokens = tuple(token.lower() for token in include)
        for link in links:
            label = clean_text(link.get("label")).lower()
            if any(token in label for token in normalized_tokens):
                return clean_text(link.get("url"))
        return KentuckyApiClient._pick_first_link(links)

    @staticmethod
    def _pick_first_link(links: list[dict[str, Any]]) -> str | None:
        for link in links:
            url = clean_text(link.get("url"))
            if url:
                return url
        return None

    @staticmethod
    def _chapter(last_action: str) -> str:
        match = KENTUCKY_CHAPTER_PATTERN.search(str(last_action or ""))
        if match is None:
            return ""
        return clean_text(match.group(1))

    @staticmethod
    def _amendment_number(label: str, fallback_index: int) -> str:
        raw = clean_text(label)
        patterns = (
            (r"^House Committee Substitute (\d+)$", "HCS{}"),
            (r"^Senate Committee Substitute (\d+)$", "SCS{}"),
            (r"^House Floor Amendment (\d+)$", "HFA{}"),
            (r"^Senate Floor Amendment (\d+)$", "SFA{}"),
            (r"^Committee Substitute (\d+)$", "CS{}"),
        )
        for pattern, template in patterns:
            match = re.fullmatch(pattern, raw, re.IGNORECASE)
            if match is not None:
                return template.format(match.group(1))
        compact = re.sub(r"[^A-Z0-9]+", "", raw.upper())
        return compact or f"AMD{fallback_index}"
