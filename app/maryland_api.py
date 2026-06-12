from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


MARYLAND_BILL_NUMBER_PATTERN = re.compile(r"^(HB|HS|HJ|SB|SJ)\d{4}$", re.IGNORECASE)
MARYLAND_AMENDMENT_NUMBER_PATTERN = re.compile(r"\b(\d{6}/\d{1,2})\b")
MARYLAND_CHAPTER_PATTERN = re.compile(r"\bChapter\s+(\d+)\b", re.IGNORECASE)


def parse_maryland_date(value: str | None) -> str:
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
    return raw


def normalize_maryland_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if not MARYLAND_BILL_NUMBER_PATTERN.fullmatch(raw):
        return ""
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _bill_type(bill_num: str) -> str:
    match = re.fullmatch(r"([A-Z]+)\d+", str(bill_num or "").strip().upper())
    return match.group(1) if match else ""


def _paragraph_html(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    return f"<p>{text}</p>"


class MarylandApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.maryland_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def session_code_for_year(self, year: int) -> str:
        return f"{year}RS"

    def public_bill_url(self, year: int, bill_num: str) -> str:
        session_code = self.session_code_for_year(year)
        normalized_bill = normalize_maryland_bill_number(bill_num)
        return f"{self.settings.maryland_site_base.rstrip('/')}/mgawebsite/Legislation/Details/{normalized_bill}?ys={session_code}"

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = self.session_code_for_year(year)
        response = self.client.get(f"/{session_code}/misc/billsmasterlist/legislation.json")
        response.raise_for_status()
        payload = response.json()

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in payload:
            bill_num = normalize_maryland_bill_number((row or {}).get("BillNumber"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            title = clean_text((row or {}).get("Title")) or bill_num
            sponsor = clean_text((row or {}).get("SponsorPrimary"))
            status = clean_text((row or {}).get("Status"))
            synopsis = clean_text((row or {}).get("Synopsis"))
            detail_path = self.public_bill_url(year, bill_num)
            chapter = clean_text((row or {}).get("ChapterNumber"))
            items.append(
                {
                    "billNum": bill_num,
                    "billType": _bill_type(bill_num),
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": status,
                    "lastAction": status,
                    "lastActionDate": "",
                    "signedDate": "",
                    "effectiveDate": "",
                    "chapter": chapter,
                    "enrolledNumber": "",
                    "detailPath": detail_path,
                    "summaryText": synopsis,
                    "crossfileBillNumber": clean_text((row or {}).get("CrossfileBillNumber")),
                    "analysisLabel": "",
                    "sessionCode": session_code,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        parsed = urlparse(str(response.url))
        session_code = clean_text(parse_qs(parsed.query).get("ys", [""])[0]) or self._session_code_from_url(str(response.url))
        bill_num = self._bill_number(parsed.path, soup)
        top_box = self._top_box(soup)
        sections = self._detail_sections(soup)
        details_metadata = self._details_metadata(sections.get("Details", {}).get("html"))
        history_rows = self._history_rows(soup, str(response.url))
        text_links = self._history_text_links(history_rows)
        current_version_path = self._current_text_link(soup, str(response.url))
        introduced_path = text_links[0]["documentUrl"] if text_links else None
        digest_url = top_box.get("analysis_url") or None

        synopsis = clean_text(sections.get("Synopsis", {}).get("text"))
        title = clean_text(top_box.get("Title")) or clean_text(sections.get("Title", {}).get("text")) or bill_num
        sponsor = clean_text(top_box.get("Sponsored by"))
        status = clean_text(top_box.get("Status"))
        effective_date = parse_maryland_date(details_metadata.get("effective_date"))
        chapter = self._chapter_from_history(history_rows)
        signed_date = self._signed_date(history_rows)
        last_action = self._last_action(history_rows, fallback=status)
        last_action_date = self._last_action_date(history_rows)
        current_version_fingerprint = "|".join(
            item["documentUrl"]
            for item in ([{"documentUrl": current_version_path or ""}] + text_links)
            if item["documentUrl"]
        )

        return {
            "bill": bill_num,
            "billType": _bill_type(bill_num),
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": status or last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter,
            "enrolledNumber": clean_text(top_box.get("Bill File")) or bill_num,
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path,
            "digest": digest_url,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": _paragraph_html(synopsis or title),
            "digestHTML": _paragraph_html(clean_text(top_box.get("Analysis")) or title),
            "currentBillHTML": "",
            "billActions": history_rows,
            "amendments": self._amendments_from_history(history_rows),
            "officialPage": str(response.url),
            "sessionCode": session_code,
            "crossfileBillNumber": details_metadata.get("crossfile_bill_number", ""),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _bill_number(self, path: str, soup: BeautifulSoup) -> str:
        match = re.search(r"/Details/([A-Z]+\d+)", path, re.IGNORECASE)
        if match is not None:
            normalized = normalize_maryland_bill_number(match.group(1))
            if normalized:
                return normalized
        heading = soup.find("h2")
        normalized = normalize_maryland_bill_number(heading.get_text(" ", strip=True) if heading else "")
        if normalized:
            return normalized
        raise ValueError(f"Maryland bill number could not be parsed from {path}")

    @staticmethod
    def _session_code_from_url(url: str) -> str:
        match = re.search(r"/(\d{4}RS)/", url, re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    def _top_box(self, soup: BeautifulSoup) -> dict[str, str]:
        values: dict[str, str] = {}
        box = soup.select_one("dl.top-box")
        if box is None:
            return values

        for dt, dd in zip(box.find_all("dt", recursive=False), box.find_all("dd", recursive=False)):
            label = clean_text(dt.get_text(" ", strip=True))
            if not label:
                continue
            values[label] = clean_text(dd.get_text(" ", strip=True))
            if label == "Analysis":
                link = dd.find("a", href=True)
                if link is not None:
                    values["analysis_url"] = absolute_url(self.settings.maryland_site_base, link.get("href")) or ""

        heading_link = soup.find("a", href=True, string=lambda value: clean_text(value) == values.get("Title", ""))
        if heading_link is not None:
            values["title_url"] = absolute_url(self.settings.maryland_site_base, heading_link.get("href")) or ""

        bill_heading = soup.find("h2")
        bill_label = clean_text(bill_heading.get_text(" ", strip=True) if bill_heading else "")
        current_bill_link = soup.find("a", href=True, string=lambda value: clean_text(value) == bill_label)
        if current_bill_link is not None:
            values["current_text_url"] = absolute_url(self.settings.maryland_site_base, current_bill_link.get("href")) or ""
            values["Bill File"] = clean_text(current_bill_link.get_text(" ", strip=True))
        return values

    def _detail_sections(self, soup: BeautifulSoup) -> dict[str, dict[str, str]]:
        sections: dict[str, dict[str, str]] = {}
        summary = soup.find(id="divSummary")
        if summary is None:
            return sections
        for label_node in summary.select("div.details-section-name"):
            label = clean_text(label_node.get_text(" ", strip=True))
            value_node = label_node.find_next_sibling("div")
            if not label or value_node is None:
                continue
            sections[label] = {
                "text": clean_text(value_node.get_text(" ", strip=True)),
                "html": str(value_node),
            }
        return sections

    @staticmethod
    def _detail_value_from_section(section_text: str | None, prefix: str) -> str:
        text = clean_text(section_text)
        if not text:
            return ""
        pattern = re.escape(prefix) + r"\s*(.+?)(?=(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*:)|$)"
        match = re.search(pattern, text)
        return clean_text(match.group(1)) if match else ""

    @staticmethod
    def _details_metadata(section_html: str | None) -> dict[str, str]:
        if not section_html:
            return {}
        soup = BeautifulSoup(section_html, "html.parser")
        values: dict[str, str] = {}
        for row in soup.select("div.container-fluid.pl-0 div.col-sm-12"):
            text = clean_text(row.get_text(" ", strip=True))
            if text.startswith("Cross-filed with:"):
                link = row.find("a", href=True)
                values["crossfile_bill_number"] = clean_text(link.get_text(" ", strip=True) if link is not None else text.removeprefix("Cross-filed with:"))
            elif text.startswith("Effective Date(s):"):
                values["effective_date"] = clean_text(text.removeprefix("Effective Date(s):"))
            elif text.startswith("Bill File Type:"):
                values["bill_file_type"] = clean_text(text.removeprefix("Bill File Type:"))
        return values

    def _current_text_link(self, soup: BeautifulSoup, page_url: str) -> str | None:
        heading = soup.find("h2")
        bill_label = clean_text(heading.get_text(" ", strip=True) if heading else "")
        anchor = soup.find("a", href=True, string=lambda value: clean_text(value) == bill_label)
        if anchor is None:
            return None
        return absolute_url(page_url, anchor.get("href")) or None

    def _history_rows(self, soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
        table = soup.find(id="detailsHistory")
        if table is None:
            return []

        rows: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) != 5:
                continue
            chamber = clean_text(cells[0].get_text(" ", strip=True))
            calendar_date = parse_maryland_date(cells[1].get_text(" ", strip=True))
            legislative_date = parse_maryland_date(cells[2].get_text(" ", strip=True))
            action_cell = cells[3]
            action_text = clean_text(action_cell.get_text(" ", strip=True))
            proceedings_url = ""
            proceedings_link = cells[4].find("a", href=True)
            if proceedings_link is not None:
                proceedings_url = absolute_url(page_url, proceedings_link.get("href")) or ""
            action_links = [
                {
                    "label": clean_text(link.get_text(" ", strip=True)),
                    "documentUrl": absolute_url(page_url, link.get("href")) or "",
                }
                for link in action_cell.find_all("a", href=True)
                if absolute_url(page_url, link.get("href"))
            ]
            rows.append(
                {
                    "chamber": chamber,
                    "statusDate": legislative_date or calendar_date,
                    "calendarDate": calendar_date,
                    "legislativeDate": legislative_date,
                    "statusMessage": action_text,
                    "location": chamber,
                    "proceedingsUrl": proceedings_url,
                    "links": action_links,
                }
            )
        return rows

    @staticmethod
    def _history_text_links(history_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in history_rows:
            for link in row.get("links") or []:
                label = clean_text(link.get("label"))
                document_url = clean_text(link.get("documentUrl"))
                if not label or not document_url or not label.lower().startswith("text -"):
                    continue
                if document_url in seen:
                    continue
                seen.add(document_url)
                links.append({"label": label, "documentUrl": document_url})
        return links

    @staticmethod
    def _amendments_from_history(history_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in history_rows:
            action_text = clean_text(row.get("statusMessage"))
            for link in row.get("links") or []:
                document_url = clean_text(link.get("documentUrl"))
                if "/amds/" not in document_url.lower():
                    continue
                amendment_number = clean_text(link.get("label")) or MarylandApiClient._amendment_number_from_text(action_text)
                if not amendment_number or amendment_number in seen:
                    continue
                seen.add(amendment_number)
                amendments.append(
                    {
                        "amendmentNumber": amendment_number,
                        "label": f"Amendment {amendment_number}",
                        "status": action_text,
                        "statusDate": clean_text(row.get("statusDate")),
                        "documentUrl": document_url,
                        "sponsor": MarylandApiClient._amendment_sponsor(action_text),
                    }
                )
        return amendments

    @staticmethod
    def _amendment_number_from_text(value: str | None) -> str:
        match = MARYLAND_AMENDMENT_NUMBER_PATTERN.search(clean_text(value))
        return clean_text(match.group(1) if match else "")

    @staticmethod
    def _amendment_sponsor(value: str | None) -> str:
        text = clean_text(value)
        match = re.search(r"\(([^)]+)\)", text)
        return clean_text(match.group(1) if match else "")

    @staticmethod
    def _chapter_from_history(history_rows: list[dict[str, Any]]) -> str:
        for row in reversed(history_rows):
            match = MARYLAND_CHAPTER_PATTERN.search(clean_text(row.get("statusMessage")))
            if match is not None:
                return clean_text(match.group(1))
        return ""

    @staticmethod
    def _signed_date(history_rows: list[dict[str, Any]]) -> str:
        for row in reversed(history_rows):
            status_message = clean_text(row.get("statusMessage")).lower()
            if "approved by the governor" in status_message or "signed by governor" in status_message:
                return clean_text(row.get("statusDate"))
        return ""

    @staticmethod
    def _last_action(history_rows: list[dict[str, Any]], *, fallback: str = "") -> str:
        for row in reversed(history_rows):
            action = clean_text(row.get("statusMessage"))
            if action and not action.lower().startswith("text -"):
                return action
        return clean_text(fallback)

    @staticmethod
    def _last_action_date(history_rows: list[dict[str, Any]]) -> str:
        for row in reversed(history_rows):
            action = clean_text(row.get("statusMessage"))
            action_date = clean_text(row.get("statusDate"))
            if action and action_date and not action.lower().startswith("text -"):
                return action_date
        return ""
