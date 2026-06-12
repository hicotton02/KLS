from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


IDAHO_BILL_NUMBER_PATTERN = re.compile(r"^(H|S)\d{4}[A-Z]?$", re.IGNORECASE)
IDAHO_CHAPTER_PATTERN = re.compile(r"Session Law Chapter (?P<number>\d+)", re.IGNORECASE)


def parse_idaho_date(value: str | None, *, year: int | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if year is not None and re.fullmatch(r"\d{2}/\d{2}", raw):
        month, day = raw.split("/")
        return f"{year:04d}-{int(month):02d}-{int(day):02d}"
    return raw


def normalize_idaho_bill_number(value: str | None) -> str:
    raw = clean_text(str(value or "")).upper().replace(" ", "")
    match = IDAHO_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Z])0*(\d+)([A-Z]?)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0, "")
    return (match.group(1), int(match.group(2)), match.group(3))


class IdahoApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.idaho_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(f"/sessioninfo/{year}/legislation/")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for table in soup.find_all("table"):
            bill_num, title, status, detail_path = self._bill_index_row(table, year)
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[0],
                    "catchTitle": title or bill_num,
                    "billTitle": title or bill_num,
                    "sponsor": "",
                    "billStatus": status,
                    "lastAction": status,
                    "lastActionDate": "",
                    "signedDate": "",
                    "effectiveDate": "",
                    "chapter": "",
                    "enrolledNumber": "",
                    "detailPath": detail_path,
                    "sessionYear": year,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        year = int((item or {}).get("sessionYear") or self._year_from_path(str(response.url)) or 0)

        tables = soup.find_all("table")
        bill_table = tables[0] if len(tables) > 0 else None
        summary_table = tables[1] if len(tables) > 1 else None
        actions_table = tables[2] if len(tables) > 2 else None

        bill_num = normalize_idaho_bill_number(bill_table.find("td").get_text(" ", strip=True) if bill_table else (item or {}).get("billNum"))
        if not bill_num:
            raise ValueError("Idaho bill number could not be parsed")

        sponsor = self._sponsor_text(bill_table)
        summary = clean_text(summary_table.get_text(" ", strip=True)) if summary_table is not None else clean_text(str((item or {}).get("billTitle") or "")) or bill_num
        actions = self._actions(actions_table, year=year)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": ""}
        last_action = first_non_empty(
            clean_text(str(latest_action.get("statusMessage") or "")),
            clean_text(str((item or {}).get("lastAction") or "")),
            clean_text(str((item or {}).get("billStatus") or "")),
        )
        last_action_date = first_non_empty(
            parse_idaho_date(latest_action.get("statusDate"), year=year if year else None),
            clean_text(str((item or {}).get("lastActionDate") or "")),
        )
        signed_date = self._signed_date(actions, year=year)
        effective_date = self._effective_date(actions)
        chapter = self._chapter(actions)
        links = self._document_links(soup, str(response.url))
        bill_text_url = links.get("Bill Text")
        digest_url = links.get("Statement of Purpose / Fiscal Note")

        return {
            "bill": bill_num,
            "billType": bill_num[0],
            "catchTitle": summary,
            "sponsor": sponsor,
            "billTitle": summary,
            "billStatus": first_non_empty(clean_text(str((item or {}).get("billStatus") or "")), last_action),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter,
            "enrolledNumber": chapter,
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": bill_text_url,
            "digest": digest_url,
            "summary": str(response.url),
            "currentVersionPath": bill_text_url,
            "currentVersionFingerprint": "|".join(url for url in links.values() if url),
            "summaryHTML": self._paragraph_html(summary),
            "digestHTML": self._paragraph_html(clean_text(str((item or {}).get("billStatus") or ""))),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _bill_index_row(table: Tag, year: int) -> tuple[str, str, str, str]:
        row = table.find("tr", id=re.compile(r"^bill[HS]\d{4}", re.IGNORECASE))
        if row is None:
            return ("", "", "", "")
        cells = row.find_all("td")
        if len(cells) < 4:
            return ("", "", "", "")
        anchor = cells[0].find("a", href=True)
        bill_num = normalize_idaho_bill_number(anchor.get_text(" ", strip=True) if anchor is not None else cells[0].get_text(" ", strip=True))
        detail_path = absolute_url(f"https://legislature.idaho.gov/sessioninfo/{year}/legislation/", anchor.get("href") if anchor is not None else "")
        title = clean_text(cells[1].get_text(" ", strip=True))
        status = clean_text(cells[3].get_text(" ", strip=True))
        return (bill_num, title, status, detail_path or "")

    @staticmethod
    def _year_from_path(url: str) -> int | None:
        match = re.search(r"/sessioninfo/(\d{4})/legislation/", str(url))
        return int(match.group(1)) if match is not None else None

    @staticmethod
    def _sponsor_text(table: Tag | None) -> str:
        if table is None:
            return ""
        cells = table.find_all("td")
        if len(cells) < 3:
            return ""
        sponsor = clean_text(cells[-1].get_text(" ", strip=True))
        if sponsor.lower().startswith("by "):
            sponsor = sponsor[3:].strip()
        return sponsor

    @staticmethod
    def _actions(table: Tag | None, *, year: int) -> list[dict[str, str]]:
        if table is None:
            return []
        parsed: list[dict[str, str]] = []
        current_date = ""
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            raw_date = clean_text(cells[1].get_text(" ", strip=True))
            action = clean_text(cells[2].get_text(" ", strip=True))
            if raw_date:
                current_date = parse_idaho_date(raw_date, year=year)
            action_date = current_date or IdahoApiClient._embedded_action_date(action) or ""
            parsed.append(
                {
                    "location": "",
                    "statusDate": action_date,
                    "statusMessage": action,
                }
            )
        return parsed

    @staticmethod
    def _embedded_action_date(action: str) -> str:
        match = re.search(r"on\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", clean_text(action), re.IGNORECASE)
        if match is None:
            return ""
        return parse_idaho_date(match.group(1))

    @staticmethod
    def _signed_date(actions: list[dict[str, str]], *, year: int) -> str:
        for action in reversed(actions):
            text = clean_text(str(action.get("statusMessage") or ""))
            if "signed by governor" in text.lower():
                return first_non_empty(IdahoApiClient._embedded_action_date(text), clean_text(str(action.get("statusDate") or "")))
        return ""

    @staticmethod
    def _effective_date(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            text = clean_text(str(action.get("statusMessage") or ""))
            match = re.search(r"Effective:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", text, re.IGNORECASE)
            if match is not None:
                return parse_idaho_date(match.group(1))
        return ""

    @staticmethod
    def _chapter(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            text = clean_text(str(action.get("statusMessage") or ""))
            match = IDAHO_CHAPTER_PATTERN.search(text)
            if match is not None:
                return f"Chapter {int(match.group('number'))}"
        return ""

    @staticmethod
    def _document_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
        links: dict[str, str] = {}
        for anchor in soup.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            href = absolute_url(base_url, anchor.get("href"))
            if not label or not href:
                continue
            if "sessioninfo" not in href and not href.lower().endswith(".pdf"):
                continue
            if label in {"Bill Text", "Statement of Purpose / Fiscal Note", "Session Law", "Legislative Co-sponsors"}:
                links[label] = href
        return links

    @staticmethod
    def _paragraph_html(text: str) -> str:
        cleaned = clean_text(text)
        return f"<p>{cleaned}</p>" if cleaned else ""
