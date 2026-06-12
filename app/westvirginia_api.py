from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


WEST_VIRGINIA_LIST_PATH = "/Bill_Status/bills_all_bills.cfm"
WEST_VIRGINIA_DETAIL_PATTERN = re.compile(r"Bills_history\.cfm\?input=(\d+)&year=(\d+)&sessiontype=RS&btype=bill", re.IGNORECASE)


def parse_west_virginia_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_west_virginia_bill_number(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.search(r"\b(House|Senate)\s+Bill\s+(\d+)\b", raw, re.IGNORECASE)
    if match:
        prefix = "HB" if match.group(1).lower() == "house" else "SB"
        return f"{prefix}{match.group(2)}"
    compact = raw.replace(" ", "").upper()
    match = re.fullmatch(r"(HB|SB)(\d+)", compact)
    if match:
        return compact
    return compact


class WestVirginiaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.west_virginia_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(
            WEST_VIRGINIA_LIST_PATH,
            params={"year": str(year), "sessiontype": "RS", "btype": "bill"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        for link in soup.find_all("a", href=True):
            href = str(link.get("href") or "")
            if not WEST_VIRGINIA_DETAIL_PATTERN.search(href):
                continue
            row = link.find_parent("tr")
            if row is None:
                continue
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            bill_num = normalize_west_virginia_bill_number(link.get_text(" ", strip=True))
            if not bill_num or bill_num in seen_bill_nums:
                continue
            seen_bill_nums.add(bill_num)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": cells[1] if len(cells) > 1 else bill_num,
                    "billTitle": cells[1] if len(cells) > 1 else bill_num,
                    "sponsor": "",
                    "billStatus": cells[2] if len(cells) > 2 else "",
                    "lastAction": cells[-1] if len(cells) > 3 else "",
                    "lastActionDate": parse_west_virginia_date(cells[-1]) if len(cells) > 3 else "",
                    "detailPath": absolute_url(str(response.url), href),
                }
            )
        return items

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(soup)
        summary = self._label_value_text(soup, "SUMMARY:")
        lead_sponsor = self._label_value_text(soup, "LEAD SPONSOR:")
        sponsors = self._label_value_text(soup, "SPONSORS:")
        subjects = self._label_value_text(soup, "SUBJECT(S):")
        versions = self._bill_text_versions(soup, str(response.url))
        actions = self._action_rows(soup)
        amendments = self._amendment_rows(soup, str(response.url))
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": self._label_value_text(soup, "LAST ACTION:")}
        current_version = versions[0] if versions else {}
        introduced_version = versions[-1] if versions else {}

        sponsor_parts = [lead_sponsor, sponsors]
        sponsor_text = ", ".join(dict.fromkeys(part for part in sponsor_parts if part))

        signed_date = ""
        for action in actions:
            if "approved by governor" in str(action.get("statusMessage") or "").lower():
                signed_date = str(action.get("statusDate") or "")
                break

        digest_html = f"<p>{subjects}</p>" if subjects else ""

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": summary or bill_num,
            "sponsor": sponsor_text,
            "billTitle": summary or bill_num,
            "billStatus": self._label_value_text(soup, "LAST ACTION:") or str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": str(current_version.get("version") or ""),
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": str(introduced_version.get("document_url") or "") or None,
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": str(current_version.get("document_url") or "") or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    str(current_version.get("version") or ""),
                    str(current_version.get("html_url") or ""),
                    str(current_version.get("pdf_url") or ""),
                ]
                if part
            ),
            "summaryHTML": f"<p>{summary or bill_num}</p>",
            "digestHTML": digest_html,
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _bill_number(soup: BeautifulSoup) -> str:
        for tag in soup.find_all(["h1", "h2", "h3", "title"]):
            bill_num = normalize_west_virginia_bill_number(tag.get_text(" ", strip=True))
            if re.fullmatch(r"(HB|SB)\d+", bill_num):
                return bill_num
        raise ValueError("West Virginia bill number could not be parsed")

    @staticmethod
    def _label_value_cell(soup: BeautifulSoup, label: str) -> Tag | None:
        strong = soup.find("strong", string=lambda value: isinstance(value, str) and value.strip() == label)
        if strong is None:
            return None
        label_cell = strong.find_parent("td")
        if label_cell is None:
            return None
        return label_cell.find_next_sibling("td")

    @classmethod
    def _label_value_text(cls, soup: BeautifulSoup, label: str) -> str:
        value_cell = cls._label_value_cell(soup, label)
        if value_cell is None:
            return ""
        return value_cell.get_text(" ", strip=True)

    @classmethod
    def _bill_text_versions(cls, soup: BeautifulSoup, detail_url: str) -> list[dict[str, str]]:
        value_cell = cls._label_value_cell(soup, "BILL TEXT:")
        if value_cell is None:
            return []

        rows: list[dict[str, str]] = []
        label_parts: list[str] = []
        links: dict[str, str] = {}

        def flush() -> None:
            nonlocal label_parts, links
            label = " ".join(part for part in label_parts if part).strip().rstrip("-").strip()
            if label or links:
                rows.append(
                    {
                        "version": label,
                        "html_url": links.get("html", ""),
                        "pdf_url": links.get("pdf", ""),
                        "docx_url": links.get("docx", ""),
                        "document_url": links.get("html", "") or links.get("pdf", "") or links.get("docx", ""),
                    }
                )
            label_parts = []
            links = {}

        for child in value_cell.children:
            if isinstance(child, NavigableString):
                text = " ".join(str(child).split())
                if text:
                    label_parts.append(text)
                continue
            if not isinstance(child, Tag):
                continue
            if child.name == "br":
                flush()
                continue
            if child.name != "a":
                text = child.get_text(" ", strip=True)
                if text:
                    label_parts.append(text)
                continue
            link_text = child.get_text(" ", strip=True).lower()
            link_url = absolute_url(detail_url, child.get("href"))
            if link_text in {"html", "pdf", "docx"} and link_url:
                links[link_text] = link_url
        flush()

        return rows

    @classmethod
    def _amendment_rows(cls, soup: BeautifulSoup, detail_url: str) -> list[dict[str, str]]:
        value_cell = cls._label_value_cell(soup, "FLOOR AMENDMENTS:")
        if value_cell is None:
            return []

        rows: list[dict[str, str]] = []
        seen_amendments: set[str] = set()
        for link in value_cell.find_all("a", href=True):
            amendment_number = link.get_text(" ", strip=True)
            normalized = amendment_number.upper()
            if not amendment_number or normalized in seen_amendments:
                continue
            seen_amendments.add(normalized)
            lower_name = amendment_number.lower()
            chamber = "S" if " sfa" in f" {lower_name}" or " sfat" in f" {lower_name}" else "H"
            status = ""
            if "adopted" in lower_name:
                status = "Adopted"
            elif "rejected" in lower_name:
                status = "Rejected"
            elif "pulled" in lower_name:
                status = "Pulled"
            rows.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": chamber,
                    "order": "",
                    "sequence": "",
                    "status": status,
                    "sponsor": "",
                    "documentUrl": absolute_url(detail_url, link.get("href")) or "",
                }
            )
        return rows

    @staticmethod
    def _action_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table", id="action-table")
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) != 4:
                continue
            rows.append(
                {
                    "location": cells[0],
                    "statusMessage": cells[1],
                    "statusDate": parse_west_virginia_date(cells[2]),
                }
            )
        return rows
