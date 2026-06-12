from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url
from app.settings import Settings
from app.text_utils import html_to_text, pdf_bytes_to_text


MINNESOTA_BILL_PATH_PATTERN = re.compile(
    r"/bills/(?P<legislature>\d+)/(?P<year>\d{4})/(?P<session>\d+)/(?:HF|SF)/(?P<number>\d+)/",
    re.IGNORECASE,
)
MINNESOTA_BILL_NUMBER_PATTERN = re.compile(r"^(HF|SF)\d+$", re.IGNORECASE)
MINNESOTA_SESSION_WORDS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
}


def parse_minnesota_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%m/%d/%Y %I:%M %p"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def parse_minnesota_special_session(label: str | None) -> int:
    raw = " ".join(str(label or "").split()).strip().lower()
    if not raw:
        return 0
    if "special session" not in raw:
        return 0
    for word, value in MINNESOTA_SESSION_WORDS.items():
        if word in raw:
            return value
    return 1


def normalize_minnesota_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if MINNESOTA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class MinnesotaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.minnesota_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get("/bills/", params={"year": str(year)})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = soup.select_one("table.table-hover")
        if table is None:
            raise ValueError("Minnesota bill list table was not found")

        items: list[dict[str, Any]] = []
        seen: set[tuple[int, int, str]] = set()

        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue

            session_label = " ".join(cells[0].get_text(" ", strip=True).split())
            if not session_label.startswith(str(year)):
                continue
            special_session_value = parse_minnesota_special_session(session_label)

            for bill_cell in cells[1:3]:
                link = bill_cell.find("a", href=True)
                if link is None:
                    continue
                bill_num = normalize_minnesota_bill_number(link.get_text(" ", strip=True))
                if not bill_num:
                    continue
                key = (year, special_session_value, bill_num)
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": bill_num[:2],
                        "catchTitle": bill_num,
                        "billTitle": bill_num,
                        "sponsor": "",
                        "billStatus": "",
                        "lastAction": "",
                        "lastActionDate": "",
                        "specialSessionValue": special_session_value,
                        "detailPath": absolute_url(self.settings.minnesota_site_base, link.get("href")),
                    }
                )

        return sorted(
            items,
            key=lambda item: (
                int(item.get("specialSessionValue") or 0),
                _sort_bill_key(str(item["billNum"])),
            ),
        )

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(str(response.url), soup)
        special_session_value = self._special_session_value_from_url(str(response.url))
        description = self._section_paragraph(soup, "Description")
        authors = self._author_names(soup)
        actions = self._action_rows(soup)
        versions = self._version_rows(soup, str(response.url))
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": "", "location": ""}
        current_version = versions[-1] if versions else {}
        introduced_version = versions[0] if versions else {}
        long_description_url = self._named_link_url(soup, "Long Description")

        signed_date = ""
        chapter_no = ""
        for action in reversed(actions):
            action_text = str(action.get("statusMessage") or "")
            lowered = action_text.lower()
            if not signed_date and "governor" in lowered and "signed" in lowered:
                signed_date = str(action.get("statusDate") or "")
            if not chapter_no:
                match = re.search(r"\bchapter\s+(\d+)\b", action_text, re.IGNORECASE)
                if match:
                    chapter_no = match.group(1)
                    if not signed_date:
                        signed_date = str(action.get("statusDate") or "")
            if signed_date and chapter_no:
                break

        sponsor = ", ".join(authors)
        current_version_path = str(current_version.get("document_url") or "") or None

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": description or bill_num,
            "sponsor": sponsor,
            "billTitle": description or bill_num,
            "billStatus": str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter_no,
            "enrolledNumber": str(current_version.get("version") or ""),
            "sponsorStringHouse": sponsor if bill_num.startswith("HF") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("SF") else None,
            "introduced": str(introduced_version.get("document_url") or "") or None,
            "digest": long_description_url,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": str(current_version.get("fingerprint") or ""),
            "summaryHTML": self._paragraph_html(description or bill_num),
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
            "specialSessionValue": special_session_value,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        if not url:
            return ""
        response = self.client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if str(response.url).lower().endswith(".pdf") or "pdf" in content_type:
            return pdf_bytes_to_text(response.content)

        soup = BeautifulSoup(response.text, "html.parser")
        document = soup.find(id="document")
        if document is not None:
            return html_to_text(str(document))
        main = soup.find("main")
        if main is not None:
            return html_to_text(str(main))
        return html_to_text(response.text)

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _bill_number(detail_url: str, soup: BeautifulSoup) -> str:
        match = re.search(r"/(HF|SF)/(\d+)/", detail_url, re.IGNORECASE)
        if match:
            return f"{match.group(1).upper()}{match.group(2)}"
        for tag in soup.find_all(["h1", "title"]):
            tag_text = tag.get_text(" ", strip=True)
            match = re.search(r"\b(HF|SF)\s*(\d+)\b", tag_text, re.IGNORECASE)
            if match:
                return f"{match.group(1).upper()}{match.group(2)}"
        raise ValueError(f"Minnesota bill number could not be parsed from {detail_url}")

    @staticmethod
    def _special_session_value_from_url(detail_url: str) -> int:
        match = MINNESOTA_BILL_PATH_PATTERN.search(detail_url)
        if match is None:
            return 0
        return int(match.group("session") or 0)

    @staticmethod
    def _section_paragraph(soup: BeautifulSoup, heading_text: str) -> str:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and heading_text in value)
        if heading is None:
            return ""
        paragraph = heading.find_next_sibling("p")
        if paragraph is None:
            return ""
        return " ".join(paragraph.get_text(" ", strip=True).split())

    @staticmethod
    def _author_names(soup: BeautifulSoup) -> list[str]:
        heading = soup.find(lambda tag: tag.name == "h2" and tag.get_text(" ", strip=True).startswith("Authors"))
        if heading is None:
            return []
        author_div = heading.find_next_sibling("div")
        if author_div is None:
            return []
        names: list[str] = []
        for link in author_div.find_all("a", href=True):
            name = " ".join(link.get_text(" ", strip=True).split())
            if name and name not in names:
                names.append(name)
        return names

    def _version_rows(self, soup: BeautifulSoup, detail_url: str) -> list[dict[str, str]]:
        table = None
        for candidate in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True) for th in candidate.find_all("th")]
            if "Engrossments" in headers:
                table = candidate
                break
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue

            version_link = cells[0].find("a", href=True)
            if version_link is None:
                continue
            version_name = " ".join(version_link.get_text(" ", strip=True).split())
            version_url = absolute_url(detail_url, version_link.get("href")) or ""

            pdf_url = ""
            for link in cells[0].find_all("a", href=True):
                href = absolute_url(detail_url, link.get("href")) or ""
                if href.lower().endswith("/pdf/") or "pdf" in " ".join(link.get_text(" ", strip=True).split()).lower():
                    pdf_url = href
                    break

            posted_text = " ".join(cells[1].get_text(" ", strip=True).split())
            posted_date = parse_minnesota_date(posted_text.replace("Posted on", "", 1).strip())
            rows.append(
                {
                    "version": version_name,
                    "document_url": version_url,
                    "pdf_url": pdf_url,
                    "posted_date": posted_date,
                    "fingerprint": "|".join(part for part in [version_name, posted_date, version_url, pdf_url] if part),
                }
            )
        return rows

    def _action_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        chronological = soup.find("div", id="chronological-tab-pane")
        if chronological is None:
            return []

        rows: list[dict[str, str]] = []
        current_date = ""
        for row in chronological.find_all("tr"):
            heading = row.find("th", colspan="2")
            if heading is not None and not row.find("td"):
                current_date = parse_minnesota_date(heading.get_text(" ", strip=True))
                continue

            cells = row.find_all("td", recursive=False)
            if len(cells) != 2:
                continue

            for chamber, cell in (("House", cells[0]), ("Senate", cells[1])):
                status_message = self._action_message(cell)
                if not status_message:
                    continue
                rows.append(
                    {
                        "statusDate": current_date,
                        "location": chamber,
                        "statusMessage": status_message,
                    }
                )
        return rows

    @staticmethod
    def _action_message(cell: Tag) -> str:
        primary = cell.find(
            lambda tag: tag.name == "div" and "col" in tag.get("class", []) and "action_item" not in tag.get("class", [])
        )
        if primary is not None:
            return " ".join(primary.get_text(" ", strip=True).split())
        return " ".join(cell.get_text(" ", strip=True).split())

    @staticmethod
    def _named_link_url(soup: BeautifulSoup, link_text: str) -> str | None:
        link = soup.find("a", string=lambda value: isinstance(value, str) and value.strip() == link_text)
        if link is None:
            return None
        return str(link.get("href") or "") or None
