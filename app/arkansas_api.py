from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


ARKANSAS_BILL_NUMBER_PATTERN = re.compile(r"^(HB|HR|HMR|HCMR|SB|SR|SMR)\d+$", re.IGNORECASE)
ARKANSAS_VIEW_LINK_PATTERN = re.compile(
    r"/Bills/ViewBills\?type=([A-Z]+)&ddBienniumSession=([^&\"']+)",
    re.IGNORECASE,
)
ARKANSAS_PAGE_COUNT_PATTERN = re.compile(r"Page\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)


def parse_arkansas_date(value: str | None) -> str:
    raw = str(value or "").replace("\xa0", " ").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %I:%M:%S %p", "%m/%d/%y %I:%M:%S %p"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_arkansas_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if ARKANSAS_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class ArkansasApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.arkansas_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._type_links_by_year: dict[int, list[tuple[str, str]]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        type_links = self._type_links_for_year(year)
        items_by_bill: dict[str, dict[str, Any]] = {}

        for bill_type, session_value in type_links:
            start = 0
            while True:
                response = self.client.get(
                    "/Bills/ViewBills",
                    params={
                        "type": bill_type,
                        "ddBienniumSession": session_value,
                        **({"start": str(start)} if start else {}),
                    },
                )
                response.raise_for_status()
                page_items = self._parse_view_page(response.text, str(response.url), session_value)
                if not page_items:
                    break
                for item in page_items:
                    items_by_bill[item["billNum"]] = item

                pager = ARKANSAS_PAGE_COUNT_PATTERN.search(response.text)
                if pager is None or int(pager.group(1)) >= int(pager.group(2)):
                    break
                start += 20

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(str(response.url), soup)
        title = self._bill_title(soup) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num
        metadata = self._metadata_fields(soup)
        actions = self._action_rows(soup)
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}

        item_current_version_path = clean_text(str((item or {}).get("currentVersionPath") or ""))
        current_version_path = (
            absolute_url(str(response.url), self._named_link_url(soup, "/Home/FTPDocument?path=%2FBills"))
            or item_current_version_path
            or None
        )
        if bill_num and current_version_path and bill_num not in current_version_path.upper().replace(" ", ""):
            current_version_path = item_current_version_path or current_version_path
        act_pdf = absolute_url(str(response.url), self._named_link_url(soup, "/Acts/FTPDocument")) or ""
        introduced_path = current_version_path

        status_text = first_non_empty_text(
            metadata.get("Status"),
            clean_text(str(latest_action.get("statusMessage") or "")),
            clean_text(str((item or {}).get("billStatus") or "")),
        )
        last_action = first_non_empty_text(
            clean_text(str(latest_action.get("statusMessage") or "")),
            status_text,
        )
        last_action_date = first_non_empty_text(
            clean_text(str(latest_action.get("statusDate") or "")),
            metadata.get("Act Date"),
            metadata.get("Introduction Date"),
        )
        sponsor = first_non_empty_text(
            metadata.get("Lead Sponsor"),
            clean_text(str((item or {}).get("sponsor") or "")),
        )
        chapter = first_non_empty_text(
            metadata.get("Act Number"),
            self._chapter_from_text(status_text),
            self._chapter_from_text(last_action),
        )
        signed_date = metadata.get("Act Date") if chapter else ""

        fingerprint_parts = [
            clean_text(str(current_version_path or "")),
            clean_text(str(act_pdf or "")),
            clean_text(str(chapter or "")),
            clean_text(str(last_action or "")),
            clean_text(str(last_action_date or "")),
        ]

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": status_text,
            "lastAction": last_action,
            "lastActionDate": parse_arkansas_date(last_action_date),
            "signedDate": parse_arkansas_date(signed_date),
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(str(chapter or "")),
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path,
            "digest": act_pdf,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(part for part in fingerprint_parts if part),
            "summaryHTML": f"<p>{title}</p>",
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _type_links_for_year(self, year: int) -> list[tuple[str, str]]:
        cached = self._type_links_by_year.get(year)
        if cached is not None:
            return cached

        response = self.client.get("/Bills/SearchByRange")
        response.raise_for_status()

        links: list[tuple[str, str]] = []
        for bill_type, session_value in ARKANSAS_VIEW_LINK_PATTERN.findall(response.text):
            decoded_session = unquote(session_value)
            if str(year) not in decoded_session:
                continue
            candidate = (bill_type.upper(), decoded_session)
            if candidate not in links:
                links.append(candidate)

        if not links:
            raise ValueError(f"Arkansas bill type links were not found for {year}")

        self._type_links_by_year[year] = links
        return links

    def _parse_view_page(self, html_text: str, page_url: str, session_value: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")

        items: list[dict[str, Any]] = []
        for row in soup.select(
            "div[role='grid'] > div.row.tableRow, "
            "div[role='grid'] > div.row.tableRowAlt, "
            "div#tableDataWrapper > div.row.tableRow, "
            "div#tableDataWrapper > div.row.tableRowAlt"
        ):
            bill_link = row.find("a", href=re.compile(r"/Bills/Detail\?id=", re.IGNORECASE))
            if bill_link is None:
                continue
            parsed = urlparse(absolute_url(page_url, bill_link.get("href")) or "")
            bill_num = normalize_arkansas_bill_number(parse_qs(parsed.query).get("id", [""])[0])
            if not bill_num:
                bill_num = normalize_arkansas_bill_number(bill_link.get_text(" ", strip=True))
            if not bill_num:
                continue

            title_column = row.select_one("div.col-md-7")
            sponsor_link = row.find(
                "a",
                href=re.compile(r"/(Legislators|Committees)/Detail", re.IGNORECASE),
            )
            bill_pdf_link = row.find("a", href=re.compile(r"/Home/FTPDocument\?path=%2FBills", re.IGNORECASE))

            title = clean_text(title_column.get_text(" ", strip=True) if title_column else "") or bill_num
            sponsor = clean_text(sponsor_link.get_text(" ", strip=True) if sponsor_link else "")
            detail_path = absolute_url(page_url, bill_link.get("href"))
            current_version_path = absolute_url(page_url, bill_pdf_link.get("href")) if bill_pdf_link is not None else ""
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "sessionValue": session_value,
                    "detailPath": detail_path,
                    "currentVersionPath": current_version_path,
                    "currentVersionFingerprint": current_version_path,
                }
            )

        return items

    @staticmethod
    def _bill_number(detail_url: str, soup: BeautifulSoup) -> str:
        parsed = urlparse(detail_url)
        bill_num = normalize_arkansas_bill_number(parse_qs(parsed.query).get("id", [""])[0])
        if bill_num:
            return bill_num
        heading = soup.find("h1")
        if heading is not None:
            match = re.match(r"([A-Z]+\d+)\b", clean_text(heading.get_text(" ", strip=True)), re.IGNORECASE)
            if match:
                bill_num = normalize_arkansas_bill_number(match.group(1))
                if bill_num:
                    return bill_num
        raise ValueError(f"Arkansas bill number could not be parsed from {detail_url}")

    @staticmethod
    def _bill_title(soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        if heading is None:
            return ""
        raw = clean_text(heading.get_text(" ", strip=True))
        if " - " in raw:
            return clean_text(raw.split(" - ", 1)[1])
        return raw

    @staticmethod
    def _metadata_fields(soup: BeautifulSoup) -> dict[str, str]:
        wrapper = soup.find(
            "div",
            id="tableDataWrapper",
            attrs={"role": "grid"},
        )
        if wrapper is None:
            return {}

        fields: dict[str, str] = {}
        for row in wrapper.find_all("div", class_=re.compile(r"\btableRow(?:Alt)?\b"), recursive=False):
            cells = row.find_all("div", recursive=False)
            if len(cells) != 2:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
            value = clean_text(cells[1].get_text(" ", strip=True))
            if label:
                fields[label] = value
        return fields

    @staticmethod
    def _named_link_url(soup: BeautifulSoup, href_prefix: str) -> str:
        link = soup.find("a", href=re.compile(re.escape(href_prefix), re.IGNORECASE))
        if link is None:
            return ""
        return clean_text(link.get("href") or "")

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        match = re.search(r"\bAct\s+(\d+)\b", str(value or ""), re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    @classmethod
    def _action_rows(cls, soup: BeautifulSoup) -> list[dict[str, str]]:
        heading = soup.find("h3", string=lambda value: isinstance(value, str) and "Bill Status History" in value)
        if heading is None:
            return []

        grid = heading.find_parent("div")
        while grid is not None and grid.get("id") != "tableDataWrapper":
            grid = grid.find_next_sibling("div")
            if grid is not None and grid.get("id") == "tableDataWrapper" and grid.get("role") == "grid":
                break
        if grid is None:
            return []

        rows: list[dict[str, str]] = []
        for row in grid.find_all("div", class_=re.compile(r"\btableRow(?:Alt)?\b"), recursive=False):
            cells = row.find_all("div", recursive=False)
            if len(cells) < 3:
                continue
            vote_link = row.find("a", href=re.compile(r"/Bills/Votes", re.IGNORECASE))
            rows.append(
                {
                    "location": clean_text(cells[0].get_text(" ", strip=True)),
                    "statusDate": parse_arkansas_date(cells[1].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[2].get_text(" ", strip=True)),
                    "voteUrl": absolute_url(cls._base_url_from_link(vote_link), vote_link.get("href")) if vote_link else "",
                }
            )
        return rows

    @staticmethod
    def _base_url_from_link(link: Tag | None) -> str:
        if link is None:
            return ""
        href = str(link.get("href") or "")
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return "https://www.arkleg.state.ar.us"


def first_non_empty_text(*values: str | None) -> str:
    for value in values:
        text = clean_text(str(value or ""))
        if text:
            return text
    return ""
