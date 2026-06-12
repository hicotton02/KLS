from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


NEVADA_BILL_TYPES = ("AB", "SB", "ACR", "AJR", "AR", "SCR", "SJR", "SR", "IP")
NEVADA_BILL_NUMBER_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)(?P<number>\d+)$", re.IGNORECASE)
NEVADA_SESSION_LINK_PATTERN = re.compile(r"/App/NELIS/REL/(?P<code>[^/?#]+)$", re.IGNORECASE)
NEVADA_CHAPTER_PATTERN = re.compile(r"Chapter\s+(?P<chapter>\d+)", re.IGNORECASE)


def parse_nevada_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%A, %B %d, %Y", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = NEVADA_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group("prefix"), int(match.group("number")))


class NevadaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.nevada_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._session_codes: dict[int, str] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = self.session_code_for_year(year)
        items: dict[str, dict[str, Any]] = {}
        for bill_type in NEVADA_BILL_TYPES:
            fragment = self._bills_tab(session_code, bill_type)
            for entry in self._list_entries(fragment, session_code):
                items[entry["billNum"]] = entry
        return sorted(items.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        source_item = item or {}
        session_code = self._session_code_from_detail(detail_path, source_item)
        bill_key = self._bill_key(detail_path, source_item)
        if not session_code or not bill_key:
            raise ValueError(f"Nevada bill key could not be determined from {detail_path}")

        overview_url = absolute_url(
            self.settings.nevada_site_base,
            f"/App/NELIS/REL/{session_code}/Bill/{bill_key}/Overview",
        ) or detail_path
        overview_response = self.client.get(overview_url)
        overview_response.raise_for_status()

        overview_html = self._selected_tab(session_code, bill_key, "Overview", referer=str(overview_response.url))
        text_html = self._selected_tab(session_code, bill_key, "Text", referer=str(overview_response.url))
        amendments_html = self._selected_tab(session_code, bill_key, "Amendments", referer=str(overview_response.url))

        overview_soup = BeautifulSoup(overview_html, "html.parser")
        text_soup = BeautifulSoup(text_html, "html.parser")
        amendments_soup = BeautifulSoup(amendments_html, "html.parser")

        bill_num = first_non_empty(
            clean_text(str(source_item.get("billNum") or "")),
            self._bill_name(text_soup),
            self._bill_name_from_title(overview_response.text),
        )
        if not bill_num:
            raise ValueError(f"Nevada bill number could not be determined from {detail_path}")

        rows = self._overview_rows(overview_soup)
        title = first_non_empty(rows.get("Title"), clean_text(str(source_item.get("billTitle") or "")), bill_num)
        summary = first_non_empty(rows.get("Summary"), title)
        digest = clean_text(self._trim_duplicate_close_text(rows.get("Digest"), "Close digest"))
        sponsor = clean_text(rows.get("Primary Sponsor"))
        last_action = clean_text(rows.get("Most Recent History Action"))
        chapter = self._chapter_from_text(last_action)
        introduction_date = parse_nevada_date(rows.get("Introduction Date"))
        introduced_path, current_version_path = self._text_versions(text_soup)
        amendments = self._amendments(amendments_soup, bill_num)

        digest_parts: list[str] = []
        if digest:
            digest_parts.append(f"<p>{html.escape(digest)}</p>")
        fiscal_notes = clean_text(rows.get("Fiscal Notes"))
        if fiscal_notes:
            digest_parts.append(f"<p>{html.escape(fiscal_notes)}</p>")

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": summary,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": "",
            "signedDate": "",
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(current_version_path or ""),
            "sponsorStringHouse": sponsor if bill_num.startswith("A") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path,
            "digest": current_version_path,
            "summary": str(overview_response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    introduced_path,
                    chapter,
                    last_action,
                    str(len(amendments)),
                )
                if clean_text(str(part))
            ),
            "summaryHTML": f"<p>{html.escape(summary)}</p>" if summary else "",
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": "",
            "billActions": (
                [{"statusDate": introduction_date, "location": "", "statusMessage": last_action}] if last_action else []
            ),
            "amendments": amendments,
            "officialPage": str(overview_response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def session_code_for_year(self, year: int) -> str:
        cached = self._session_codes.get(year)
        if cached is not None:
            return cached
        response = self.client.get("/App/NELIS/REL")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            text = clean_text(anchor.get_text(" ", strip=True))
            if not text or "Special Session" in text or f"({year}) Session" not in text:
                continue
            match = NEVADA_SESSION_LINK_PATTERN.search(str(anchor.get("href") or ""))
            if match is not None:
                self._session_codes[year] = match.group("code")
                return match.group("code")
        raise ValueError(f"Nevada session code could not be determined for {year}")

    def _bills_tab(self, session_code: str, bill_type: str) -> str:
        referer = absolute_url(self.settings.nevada_site_base, f"/App/NELIS/REL/{session_code}/Bills?selectedBillTypes={bill_type}")
        response = self.client.get(
            f"/App/NELIS/REL/{session_code}/HomeBill/BillsTab",
            params={"selectedBillTypes": bill_type, "Filters.PageSize": "2147483647", "Page": "1"},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer or self.settings.nevada_site_base},
        )
        response.raise_for_status()
        return response.text

    def _selected_tab(self, session_code: str, bill_key: str, tab_name: str, *, referer: str) -> str:
        response = self.client.post(
            f"/App/NELIS/REL/{session_code}/Bill/FillSelectedBillTab",
            data={"billKey": bill_key, "selectedTab": tab_name},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer},
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def _list_entries(fragment: str, session_code: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(fragment, "html.parser")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in soup.select("a[href*='/Bill/'][id]"):
            bill_num = clean_text(link.get_text(" ", strip=True)).upper()
            href = str(link.get("href") or "")
            if not bill_num or bill_num in seen or not NEVADA_BILL_NUMBER_PATTERN.fullmatch(bill_num):
                continue
            row = link.find_parent("div", class_=re.compile(r"row"))
            title_node = row.find("div", class_=re.compile(r"col-md-10")) if row is not None else None
            title = clean_text(title_node.get_text(" ", strip=True)) if title_node is not None else bill_num
            detail_path = absolute_url(
                f"https://www.leg.state.nv.us/App/NELIS/REL/{session_code}/Bills?selectedBillTypes={bill_num[:2]}",
                href,
            )
            if not detail_path:
                continue
            seen.add(bill_num)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": detail_path,
                    "currentVersionPath": None,
                    "currentVersionFingerprint": detail_path,
                }
            )
        return items

    @staticmethod
    def _session_code_from_detail(detail_path: str, item: dict[str, Any]) -> str:
        for candidate in (detail_path, str(item.get("detailPath") or "")):
            match = re.search(r"/App/NELIS/REL/(?P<code>[^/]+)/Bill/", candidate)
            if match is not None:
                return clean_text(match.group("code"))
        return ""

    @staticmethod
    def _bill_key(detail_path: str, item: dict[str, Any]) -> str:
        for candidate in (detail_path, str(item.get("detailPath") or "")):
            match = re.search(r"/Bill/(?P<key>\d+)/", candidate)
            if match is not None:
                return clean_text(match.group("key"))
        return ""

    @staticmethod
    def _bill_name(soup: BeautifulSoup) -> str:
        hidden = soup.find("input", attrs={"name": "BillName"})
        if hidden is not None:
            return clean_text(hidden.get("value") or "")
        return ""

    @staticmethod
    def _bill_name_from_title(html_text: str) -> str:
        match = re.search(r"<title>(?P<bill>[A-Z]+\d+)\s+Overview</title>", html_text, re.IGNORECASE)
        if match is not None:
            return clean_text(match.group("bill")).upper()
        return ""

    @staticmethod
    def _overview_rows(soup: BeautifulSoup) -> dict[str, str]:
        rows: dict[str, str] = {}
        for row in soup.select("div.row.mt-2"):
            children = row.find_all("div", recursive=False)
            texts = [clean_text(child.get_text(" ", strip=True)) for child in children if clean_text(child.get_text(" ", strip=True))]
            if len(texts) < 2:
                continue
            key = texts[0]
            value = " ".join(texts[1:])
            rows[key] = value
        return rows

    @staticmethod
    def _trim_duplicate_close_text(value: str | None, marker: str) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        if marker in raw:
            raw = raw.split(marker, 1)[0].strip()
        return clean_text(raw)

    @staticmethod
    def _text_versions(soup: BeautifulSoup) -> tuple[str | None, str | None]:
        links: list[str] = []
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if not href.lower().endswith(".pdf"):
                continue
            url = absolute_url("https://www.leg.state.nv.us", href)
            if url and url not in links:
                links.append(url)
        if not links:
            iframe = soup.find("iframe", src=True)
            if iframe is not None:
                remote = parse_qs(urlparse(str(iframe.get("src") or "")).query).get("remoteURL", [""])[0]
                remote = clean_text(remote)
                if remote:
                    links.append(remote)
        if not links:
            return None, None
        return links[0], links[-1]

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        match = NEVADA_CHAPTER_PATTERN.search(clean_text(value))
        if match is None:
            return ""
        return clean_text(match.group("chapter"))

    @staticmethod
    def _amendments(soup: BeautifulSoup, bill_num: str) -> list[dict[str, Any]]:
        text = clean_text(soup.get_text(" ", strip=True))
        if "There are no amendments for this bill." in text:
            return []
        heading = clean_text((soup.find("h2") or {}).get_text(" ", strip=True))
        status = heading.replace(bill_num, "").strip()
        amendments: list[dict[str, Any]] = []
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            label = clean_text(anchor.get_text(" ", strip=True))
            if not label.startswith("Amendment ") or not href.lower().endswith(".pdf"):
                continue
            amendments.append(
                {
                    "amendmentNumber": label,
                    "house": "ASSEMBLY" if bill_num.startswith("A") else "SENATE",
                    "order": label.split(" ", 1)[1] if " " in label else label,
                    "sequence": "",
                    "status": status,
                    "sponsor": "",
                    "documentUrl": absolute_url("https://www.leg.state.nv.us", href),
                }
            )
        return amendments
