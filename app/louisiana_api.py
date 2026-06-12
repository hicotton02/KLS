from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


LOUISIANA_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HR|SR|HSR)\d+$", re.IGNORECASE)
LOUISIANA_RANGE_VALUE_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HR|SR|HSR)-(\d+)$", re.IGNORECASE)
LOUISIANA_CHAPTER_PATTERN = re.compile(r"\bAct(?:s)?\s+No\.?\s*(\d+)\b", re.IGNORECASE)
LOUISIANA_SEARCH_RESULTS_ROW_PATTERN = re.compile(r"ListViewSearchResults_ctrl\d+_HyperLink1$")
LOUISIANA_AUTHOR_ROW_PATTERN = re.compile(r"ListViewSearchResults_ctrl\d+_LinkAuthor$")
LOUISIANA_STATUS_ROW_PATTERN = re.compile(r"ListViewSearchResults_ctrl\d+_LabelStatus$")
LOUISIANA_TITLE_ROW_PATTERN = re.compile(r"ListViewSearchResults_ctrl\d+_LabelKWordAndSTitle$")


def parse_louisiana_date(value: str | None, *, year: int | None = None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if year is not None:
        match = re.fullmatch(r"(\d{1,2})/(\d{1,2})", raw)
        if match is not None:
            month = int(match.group(1))
            day = int(match.group(2))
            try:
                return datetime(year, month, day).strftime("%Y-%m-%d")
            except ValueError:
                pass
    return raw


def normalize_louisiana_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if LOUISIANA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _session_code(year: int) -> str:
    return f"{int(year) % 100:02d}RS"


class LouisianaApiClient:
    index_requires_detail_fetch = True
    range_chunk_size = 100

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.louisiana_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = _session_code(year)
        range_html = self._open_range_panel(session_code)
        range_soup = BeautifulSoup(range_html, "html.parser")
        form_state = self._hidden_fields(range_soup)

        items_by_bill: dict[str, dict[str, Any]] = {}
        for option_value, max_number in self._instrument_ranges(range_soup):
            for start in range(1, max_number + 1, self.range_chunk_size):
                stop = min(start + self.range_chunk_size - 1, max_number)
                response = self.client.post(
                    f"/BillSearchList.aspx?srch=r&sid={session_code}",
                    data={
                        **form_state,
                        "__EVENTTARGET": "",
                        "__EVENTARGUMENT": "",
                        "ctl00$ctl00$PageBody$PageContent$ddlInstTypes2": option_value,
                        "ctl00$ctl00$PageBody$PageContent$tbBillNumStart": str(start),
                        "ctl00$ctl00$PageBody$PageContent$tbBillNumStop": str(stop),
                        "ctl00$ctl00$PageBody$PageContent$btnSearchByInstRange": "Search",
                    },
                )
                response.raise_for_status()
                for item in self._parse_search_results(response.text, response.url):
                    items_by_bill[item["billNum"]] = item

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = first_non_empty(
            normalize_louisiana_bill_number(self._bill_label(soup)),
            normalize_louisiana_bill_number((item or {}).get("billNum")),
        )
        if not bill_num:
            raise ValueError(f"Louisiana bill number could not be determined from {detail_path}")

        sponsor = first_non_empty(self._author_name(soup), clean_text((item or {}).get("sponsor")))
        title = first_non_empty(self._short_title(soup), clean_text((item or {}).get("billTitle")), bill_num)
        current_status = first_non_empty(self._current_status(soup), clean_text((item or {}).get("billStatus")))
        year_hint = self._session_year(soup)
        if year_hint is None:
            year_hint = self._year_hint_from_path(detail_path, item)
        actions = self._history_rows(soup, year_hint=year_hint)
        last_action = first_non_empty(actions[0]["statusMessage"] if actions else "", current_status)
        last_action_date = first_non_empty(actions[0]["statusDate"] if actions else "", "")
        chapter_no = self._chapter_number(current_status, actions)
        signed_date = last_action_date if chapter_no else ""

        document_groups = self._document_groups(soup, str(response.url), bill_num)
        text_docs = document_groups.get("Text", [])
        digest_docs = document_groups.get("Digests", [])
        amendment_docs = document_groups.get("Amendments", [])
        current_version = text_docs[0] if text_docs else {}
        introduced_version = text_docs[-1] if text_docs else {}
        current_version_path = str(current_version.get("url") or "")
        introduced_path = str(introduced_version.get("url") or current_version_path or "")
        digest_path = str((digest_docs[0] or {}).get("url") or "") if digest_docs else ""

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": current_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter_no,
            "enrolledNumber": clean_text(current_version.get("label")),
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path or None,
            "digest": digest_path or None,
            "summary": str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    *[f"{doc.get('label')}:{doc.get('url')}" for doc in text_docs],
                    current_status,
                    last_action,
                    last_action_date,
                    chapter_no,
                    str(len(amendment_docs)),
                )
                if part
            ),
            "summaryHTML": f"<p>{html.escape(title)}</p>" if title else "",
            "digestHTML": "".join(
                f"<p>{html.escape(bit)}</p>"
                for bit in (f"Current status: {current_status}" if current_status else "", *(action["statusMessage"] for action in actions[:3]))
                if bit
            ),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": self._amendments(amendment_docs),
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _open_range_panel(self, session_code: str) -> str:
        response = self.client.get(f"/BillSearch.aspx?sid={session_code}")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        if soup.find("select", attrs={"name": "ctl00$ctl00$PageBody$PageContent$ddlInstTypes2"}) is not None:
            return response.text

        state = self._hidden_fields(soup)
        response = self.client.post(
            f"/BillSearch.aspx?sid={session_code}",
            data={
                **state,
                "__EVENTTARGET": "ctl00$ctl00$PageBody$PageContent$btnHeadRange",
                "__EVENTARGUMENT": "",
            },
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def _hidden_fields(soup: BeautifulSoup) -> dict[str, str]:
        values: dict[str, str] = {}
        for hidden in soup.find_all("input", attrs={"type": "hidden", "name": True}):
            name = str(hidden.get("name") or "").strip()
            if not name:
                continue
            values[name] = str(hidden.get("value") or "")
        return values

    @staticmethod
    def _instrument_ranges(soup: BeautifulSoup) -> list[tuple[str, int]]:
        dropdown = soup.find("select", attrs={"name": "ctl00$ctl00$PageBody$PageContent$ddlInstTypes2"})
        if dropdown is None:
            raise ValueError("Louisiana instrument range selector was not found")

        ranges: list[tuple[str, int]] = []
        for option in dropdown.find_all("option"):
            raw_value = clean_text(option.get("value"))
            match = LOUISIANA_RANGE_VALUE_PATTERN.fullmatch(raw_value)
            if match is None:
                continue
            ranges.append((f"{match.group(1).upper()}-{int(match.group(2))}", int(match.group(2))))
        if not ranges:
            raise ValueError("Louisiana instrument ranges could not be parsed")
        return ranges

    def _parse_search_results(self, html_text: str, response_url: httpx.URL) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for bill_anchor in soup.find_all("a", id=LOUISIANA_SEARCH_RESULTS_ROW_PATTERN, href=True):
            row = bill_anchor.find_parent("tr")
            next_row = row.find_next_sibling("tr") if isinstance(row, Tag) else None
            if row is None or next_row is None:
                continue

            bill_num = normalize_louisiana_bill_number(bill_anchor.get_text(" ", strip=True))
            if not bill_num or bill_num in seen:
                continue

            sponsor_anchor = row.find("a", id=LOUISIANA_AUTHOR_ROW_PATTERN)
            status_span = row.find("span", id=LOUISIANA_STATUS_ROW_PATTERN)
            title_span = next_row.find("span", id=LOUISIANA_TITLE_ROW_PATTERN)
            if title_span is None:
                continue

            seen.add(bill_num)
            title = clean_text(html.unescape(title_span.get_text(" ", strip=True))) or bill_num
            sponsor = clean_text(sponsor_anchor.get_text(" ", strip=True)) if sponsor_anchor is not None else ""
            status = clean_text(status_span.get_text(" ", strip=True)) if status_span is not None else ""

            items.append(
                {
                    "year": self._session_year_from_url(str(response_url)),
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": status,
                    "lastAction": status,
                    "lastActionDate": "",
                    "detailPath": absolute_url(str(response_url), bill_anchor.get("href")),
                    "currentVersionPath": None,
                    "currentVersionFingerprint": "|".join(part for part in (status, title) if part),
                }
            )

        return items

    @staticmethod
    def _bill_label(soup: BeautifulSoup) -> str:
        node = soup.find(id="ctl00_PageBody_LabelBillID")
        return clean_text(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""

    @staticmethod
    def _author_name(soup: BeautifulSoup) -> str:
        node = soup.find(id="ctl00_PageBody_LinkAuthor")
        return clean_text(node.get_text(" ", strip=True)) if isinstance(node, Tag) else ""

    @staticmethod
    def _short_title(soup: BeautifulSoup) -> str:
        node = soup.find(id="ctl00_PageBody_LabelShortTitle")
        return clean_text(html.unescape(node.get_text(" ", strip=True))) if isinstance(node, Tag) else ""

    @staticmethod
    def _current_status(soup: BeautifulSoup) -> str:
        node = soup.find(id="ctl00_PageBody_LabelCurrentStatus")
        if not isinstance(node, Tag):
            return ""
        return clean_text(node.get_text(" ", strip=True)).replace("Current Status:", "", 1).strip()

    def _document_groups(self, soup: BeautifulSoup, detail_url: str, bill_num: str) -> dict[str, list[dict[str, str]]]:
        groups: dict[str, list[dict[str, str]]] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            text = clean_text(anchor.get_text(" ", strip=True))
            if not href or not text:
                continue
            if "ViewDocument.aspx" not in href:
                continue
            groups.setdefault(self._document_group_name(text, bill_num), []).append(
                {
                    "label": text,
                    "url": absolute_url(detail_url, href) or "",
                }
            )
        return groups

    @staticmethod
    def _document_group_name(label: str, bill_num: str) -> str:
        lower_label = clean_text(label).lower()
        normalized_bill = clean_text(bill_num).lower()
        if "amendment" in lower_label:
            return "Amendments"
        if lower_label.startswith("digest of") or lower_label.startswith("digest "):
            return "Digests"
        if " vote " in f" {lower_label} " or lower_label.startswith("house vote") or lower_label.startswith("senate vote"):
            return "Votes"
        if normalized_bill and lower_label.startswith(normalized_bill.lower()):
            return "Text"
        return "Other"

    @staticmethod
    def _year_hint_from_path(detail_path: str, item: dict[str, Any] | None = None) -> int | None:
        if item is not None:
            for key in ("year", "whichYear"):
                value = item.get(key)
                if isinstance(value, int):
                    return value
                if str(value or "").isdigit():
                    return int(str(value))
        match = re.search(r"sid=(\d{2})RS", detail_path, re.IGNORECASE)
        if match is not None:
            return 2000 + int(match.group(1))
        return None

    @staticmethod
    def _session_year(soup: BeautifulSoup) -> int | None:
        match = re.search(r"\b(20\d{2})\s+Regular Session\b", soup.get_text(" ", strip=True), re.IGNORECASE)
        if match is None:
            return None
        return int(match.group(1))

    @staticmethod
    def _session_year_from_url(url: str) -> int | None:
        match = re.search(r"sid=(\d{2})RS", url, re.IGNORECASE)
        if match is None:
            return None
        return 2000 + int(match.group(1))

    @staticmethod
    def _history_rows(soup: BeautifulSoup, *, year_hint: int | None) -> list[dict[str, str]]:
        table = soup.find(id="ctl00_PageBody_ListViewHistory")
        if table is None:
            table = soup.find("table", string=None)
        rows: list[dict[str, str]] = []
        for row in soup.find_all("tr", valign="top"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            date_value = parse_louisiana_date(cells[0].get_text(" ", strip=True), year=year_hint)
            action = clean_text(cells[3].get_text(" ", strip=True))
            if not date_value or not action:
                continue
            rows.append(
                {
                    "statusDate": date_value,
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusMessage": action,
                }
            )
        return rows

    @staticmethod
    def _chapter_number(current_status: str, actions: list[dict[str, str]]) -> str:
        for text in [current_status, *(action.get("statusMessage", "") for action in actions)]:
            match = LOUISIANA_CHAPTER_PATTERN.search(clean_text(text))
            if match is not None:
                return match.group(1)
        return ""

    @staticmethod
    def _amendments(docs: list[dict[str, str]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        for index, doc in enumerate(docs, start=1):
            label = clean_text(doc.get("label"))
            if not label:
                continue
            lower_label = label.lower()
            amendments.append(
                {
                    "amendmentNumber": label,
                    "house": "H" if lower_label.startswith("house") else ("S" if lower_label.startswith("senate") else ""),
                    "order": index,
                    "sequence": index,
                    "status": "Adopted" if "adopted" in lower_label else ("Draft" if "draft" in lower_label else ""),
                    "sponsor": "",
                    "documentUrl": doc.get("url"),
                }
            )
        return amendments
