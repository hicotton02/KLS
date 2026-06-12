from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


ILLINOIS_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HR|SR|HJR|SJR|HJRCA|SJRCA)\d+$", re.IGNORECASE)
ILLINOIS_RANGE_LINK_PATTERN = re.compile(
    r"^/Legislation/RegularSession/(?P<doc_type>[A-Z]+)\?num1=\d+&num2=\d+&DocTypeID=(?P=doc_type)&GaId=(?P<gaid>\d+)&SessionId=(?P<sessionid>\d+)$",
    re.IGNORECASE,
)
ILLINOIS_LAST_ACTION_PATTERN = re.compile(
    r"Last Action\s+(?P<date>\d{1,2}/\d{1,2}/\d{4})\s*-\s*(?P<chamber>[^:]+):\s*(?P<action>.+)$",
    re.IGNORECASE,
)
ILLINOIS_PUBLIC_ACT_PATTERN = re.compile(r"(?P<chapter>\d{3}-\d{4})")
ILLINOIS_EFFECTIVE_DATE_PATTERN = re.compile(r"Effective Date\s+(?P<date>.+)$", re.IGNORECASE)
ILLINOIS_AMENDMENT_LABEL_PATTERN = re.compile(
    r"^(?P<house>House|Senate)\s+(?P<stage>Committee|Floor)\s+Amendment\s+No\.\s+(?P<number>\d+)$",
    re.IGNORECASE,
)
ILLINOIS_SUPPORTED_DOC_TYPES = {"HB", "SB", "HR", "SR", "HJR", "SJR", "HJRCA", "SJRCA"}


def parse_illinois_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_illinois_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if ILLINOIS_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class IllinoisApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.illinois_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._range_links_by_year: dict[int, list[str]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        range_links = self._range_links_for_year(year)
        items_by_bill: dict[str, dict[str, Any]] = {}

        for range_link in range_links:
            response = self.client.get(range_link)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            table = soup.find("table", class_=lambda classes: classes and "table-striped" in classes)
            if table is None:
                continue

            for row in table.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 2:
                    continue
                bill_link = cells[0].find("a", href=re.compile(r"/Legislation/BillStatus\?", re.IGNORECASE))
                if bill_link is None:
                    continue

                bill_num = normalize_illinois_bill_number(bill_link.get_text(" ", strip=True))
                if not bill_num:
                    continue

                detail_path = absolute_url(str(response.url), bill_link.get("href"))
                if not detail_path:
                    continue

                title = clean_text(cells[1].get_text(" ", strip=True)) or bill_num
                item = {
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
                items_by_bill[bill_num] = item

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = first_non_empty(
            str((item or {}).get("billNum") or ""),
            self._bill_number_from_heading(soup),
            self._bill_number_from_url(str(response.url)),
        )
        if not bill_num:
            raise ValueError(f"Illinois bill number could not be determined from {detail_path}")

        blocks = soup.select("div.row.p-3")
        title = clean_text(blocks[0].get_text(" ", strip=True)) if blocks else ""
        title = first_non_empty(title, str((item or {}).get("billTitle") or ""), bill_num)

        last_action_block = self._block_with_text(blocks, "Last Action")
        last_action_date, last_action = self._parse_last_action(last_action_block)

        sponsor_div = soup.find(id="sponsorDiv")
        house_sponsors = self._sponsor_names(sponsor_div, "house")
        senate_sponsors = self._sponsor_names(sponsor_div, "senate")
        sponsor = first_non_empty(
            house_sponsors[0] if house_sponsors else "",
            senate_sponsors[0] if senate_sponsors else "",
            str((item or {}).get("sponsor") or ""),
        )

        summary_block = self._block_with_text(blocks, "Synopsis As Introduced")
        statutes = [
            clean_text(node.get_text(" ", strip=True))
            for node in (summary_block.select("div.row.ml-4.mb-1.p-1 div.col-sm") if summary_block is not None else [])
            if clean_text(node.get_text(" ", strip=True))
        ]
        synopsis_text = self._synopsis_text(summary_block)
        amendments = self._amendments(summary_block, str(response.url))
        bill_actions = self._action_rows(soup)
        chapter = self._chapter_from_text(last_action)
        signed_date = last_action_date if chapter else ""
        effective_date = self._effective_date(bill_actions)

        full_text_path = self._named_link_url(soup, "Full Text", str(response.url))
        introduced_path = None
        current_version_path = full_text_path or None
        current_pdf_url = None
        if full_text_path:
            introduced_path, current_version_path, current_pdf_url = self._version_links(full_text_path)

        current_version_fingerprint = "|".join(
            part
            for part in (
                current_version_path,
                current_pdf_url,
                chapter,
                last_action,
                str(len(amendments)),
            )
            if clean_text(str(part))
        )

        digest_parts: list[str] = []
        if statutes:
            digest_parts.append(f"<p>Statutes amended: {html.escape('; '.join(statutes))}</p>")
        for amendment in amendments:
            summary_text = clean_text(str(amendment.get("summaryText") or ""))
            if summary_text:
                label = html.escape(str(amendment.get("amendmentNumber") or "Amendment"))
                digest_parts.append(f"<p><strong>{label}.</strong> {html.escape(summary_text)}</p>")

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": last_action or first_non_empty(str((item or {}).get("billStatus") or ""), title),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter,
            "enrolledNumber": chapter or bill_num,
            "sponsorStringHouse": ", ".join(house_sponsors) if house_sponsors else None,
            "sponsorStringSenate": ", ".join(senate_sponsors) if senate_sponsors else None,
            "introduced": introduced_path,
            "digest": current_pdf_url,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": f"<p>{html.escape(first_non_empty(synopsis_text, title))}</p>" if first_non_empty(synopsis_text, title) else "",
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": "",
            "billActions": bill_actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _range_links_for_year(self, year: int) -> list[str]:
        cached = self._range_links_by_year.get(year)
        if cached is not None:
            return cached

        response = self.client.get("/legislation")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        range_links: list[str] = []
        for link in soup.find_all("a", href=True):
            href = str(link.get("href") or "").strip()
            if not href:
                continue
            match = ILLINOIS_RANGE_LINK_PATTERN.fullmatch(href)
            if match is None:
                continue
            if match.group("doc_type").upper() not in ILLINOIS_SUPPORTED_DOC_TYPES:
                continue
            if href not in range_links:
                range_links.append(href)

        if not range_links:
            raise ValueError(f"Illinois range links were not found for {year}")

        self._range_links_by_year[year] = range_links
        return range_links

    @staticmethod
    def _block_with_text(blocks: list[Tag], text: str) -> Tag | None:
        needle = clean_text(text).lower()
        for block in blocks:
            block_text = clean_text(block.get_text(" ", strip=True)).lower()
            if needle in block_text:
                return block
        return None

    @staticmethod
    def _parse_last_action(block: Tag | None) -> tuple[str, str]:
        if block is None:
            return ("", "")
        text = " ".join(clean_text(part) for part in block.stripped_strings if clean_text(part))
        match = ILLINOIS_LAST_ACTION_PATTERN.search(text)
        if match is not None:
            return (
                parse_illinois_date(match.group("date")),
                clean_text(match.group("action")),
            )
        text = text.removeprefix("Last Action").strip()
        return ("", text)

    @staticmethod
    def _bill_number_from_heading(soup: BeautifulSoup) -> str:
        heading = soup.find("h2", string=re.compile(r"Bill Status of ", re.IGNORECASE))
        if heading is None:
            return ""
        match = re.search(r"Bill Status of\s+([A-Z0-9]+)", clean_text(heading.get_text(" ", strip=True)), re.IGNORECASE)
        if match is None:
            return ""
        return normalize_illinois_bill_number(match.group(1))

    @staticmethod
    def _bill_number_from_url(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        doc_type = clean_text(query.get("DocTypeID", [""])[0]).upper()
        doc_num = clean_text(query.get("DocNum", [""])[0])
        return normalize_illinois_bill_number(f"{doc_type}{doc_num}")

    @staticmethod
    def _sponsor_names(sponsor_div: Tag | None, chamber: str) -> list[str]:
        if sponsor_div is None:
            return []
        chamber_path = "/House/Members/Details/" if chamber == "house" else "/Senate/Members/Details/"
        names: list[str] = []
        for anchor in sponsor_div.find_all("a", href=True):
            if chamber_path.lower() not in str(anchor.get("href") or "").lower():
                continue
            name = clean_text(anchor.get_text(" ", strip=True))
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _synopsis_text(summary_block: Tag | None) -> str:
        if summary_block is None:
            return ""
        heading = summary_block.find("h5", string=lambda value: isinstance(value, str) and "Synopsis As Introduced" in value)
        if heading is None:
            return ""
        synopsis_group = heading.find_next_sibling("div", class_="list-group")
        if synopsis_group is None:
            return ""
        return clean_text(synopsis_group.get_text(" ", strip=True))

    def _amendments(self, summary_block: Tag | None, page_url: str) -> list[dict[str, Any]]:
        if summary_block is None:
            return []
        amendments: list[dict[str, Any]] = []
        sequence = 0
        for anchor in summary_block.find_all("a", href=re.compile(r"/legislation/billstatus/fulltext", re.IGNORECASE)):
            label = clean_text(anchor.get_text(" ", strip=True))
            if "Amendment No." not in label:
                continue
            sequence += 1
            container = anchor.find_parent(["span", "div"])
            summary_group = container.find_next_sibling("div", class_="list-group") if container is not None else None
            summary_text = clean_text(summary_group.get_text(" ", strip=True)) if summary_group is not None else ""
            match = ILLINOIS_AMENDMENT_LABEL_PATTERN.fullmatch(label)
            amendments.append(
                {
                    "amendmentNumber": label,
                    "house": match.group("house") if match is not None else "",
                    "order": match.group("number") if match is not None else str(sequence),
                    "sequence": sequence,
                    "status": label,
                    "sponsor": "",
                    "documentUrl": absolute_url(page_url, anchor.get("href")),
                    "summaryText": summary_text,
                }
            )
        return amendments

    @staticmethod
    def _action_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if not rows:
                continue
            header = [clean_text(cell.get_text(" ", strip=True)).lower() for cell in rows[0].find_all(["th", "td"])]
            if header[:3] != ["date", "chamber", "action"]:
                continue
            for row in rows[1:]:
                cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
                if len(cells) < 3:
                    continue
                date_value = parse_illinois_date(cells[0])
                if not date_value:
                    continue
                actions.append(
                    {
                        "statusDate": date_value,
                        "chamber": cells[1],
                        "statusMessage": cells[2],
                    }
                )
            if actions:
                break
        return actions

    @staticmethod
    def _effective_date(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            status = clean_text(str(action.get("statusMessage") or ""))
            match = ILLINOIS_EFFECTIVE_DATE_PATTERN.search(status)
            if match is not None:
                return parse_illinois_date(match.group("date"))
        return ""

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        match = ILLINOIS_PUBLIC_ACT_PATTERN.search(clean_text(value))
        if match is None:
            return ""
        return clean_text(match.group("chapter"))

    @staticmethod
    def _named_link_url(soup: BeautifulSoup, label: str, page_url: str) -> str:
        for anchor in soup.find_all("a", href=True):
            if clean_text(anchor.get_text(" ", strip=True)).lower() == clean_text(label).lower():
                return absolute_url(page_url, anchor.get("href")) or ""
        return ""

    def _version_links(self, full_text_path: str) -> tuple[str | None, str | None, str | None]:
        response = self.client.get(full_text_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        introduced = self._named_link_url(soup, "Introduced", str(response.url)) or None
        engrossed = self._named_link_url(soup, "Engrossed", str(response.url)) or None
        enrolled = self._named_link_url(soup, "Enrolled", str(response.url)) or None
        current_version = first_non_empty(enrolled, engrossed, introduced, str(response.url))
        pdf_url = self._named_link_url(soup, "Open PDF", str(response.url)) or None
        return introduced, current_version or None, pdf_url
