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


CALIFORNIA_BILL_NUMBER_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+(?:X\d+)?)-?(?P<number>\d+)$", re.IGNORECASE)
CALIFORNIA_SORT_PATTERN = re.compile(r"^(?P<prefix>[A-Z0-9]+)-(?P<number>\d+)$", re.IGNORECASE)
CALIFORNIA_CHAPTER_PATTERN = re.compile(r"Chapter\s+(?P<chapter>\d+)", re.IGNORECASE)
CALIFORNIA_DIGEST_PATTERN = re.compile(
    r"LEGISLATIVE COUNSEL'S DIGEST\s+(?P<digest>.+?)\s+Digest Key",
    re.IGNORECASE | re.DOTALL,
)
CALIFORNIA_DIGEST_FALLBACK_PATTERN = re.compile(
    r"LEGISLATIVE COUNSEL'S DIGEST\s+(?P<digest>.+?)\s+Bill Text",
    re.IGNORECASE | re.DOTALL,
)
CALIFORNIA_VERSION_VALUE_PATTERN = re.compile(r"^\d+[A-Z0-9]+$")


def california_session_code(year: int) -> str:
    start_year = year if year % 2 == 1 else year - 1
    return f"{start_year}{start_year + 1}"


def parse_california_date(value: str | None) -> str:
    raw = clean_text(value)
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


def normalize_california_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    match = CALIFORNIA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group('prefix')}-{int(match.group('number'))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = CALIFORNIA_SORT_PATTERN.fullmatch(str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group("prefix"), int(match.group("number")))


class CaliforniaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.california_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(
            "/faces/billSearchClient.xhtml",
            params={"session_year": california_session_code(year), "house": "Both"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items_by_bill: dict[str, dict[str, Any]] = {}
        for row in soup.select("table tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            bill_link = cells[0].find("a", href=re.compile(r"bill_id=", re.IGNORECASE))
            if bill_link is None:
                continue

            bill_num = normalize_california_bill_number(bill_link.get_text(" ", strip=True))
            if not bill_num:
                continue

            detail_path = absolute_url(str(response.url), bill_link.get("href"))
            if not detail_path:
                continue

            title = clean_text(cells[1].get_text(" ", strip=True)) or bill_num
            sponsor = clean_text(cells[2].get_text(" ", strip=True))
            status = clean_text(cells[3].get_text(" ", strip=True))
            items_by_bill[bill_num] = {
                "billNum": bill_num,
                "billType": bill_num.split("-", 1)[0],
                "catchTitle": title,
                "billTitle": title,
                "sponsor": sponsor,
                "billStatus": status,
                "lastAction": status,
                "lastActionDate": "",
                "detailPath": detail_path,
                "currentVersionPath": None,
                "currentVersionFingerprint": detail_path,
            }

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        source_item = item or {}
        bill_id = self._bill_id(detail_path, source_item)
        if not bill_id:
            raise ValueError(f"California bill_id could not be determined from {detail_path}")

        status_url = absolute_url(detail_path, f"/faces/billStatusClient.xhtml?bill_id={bill_id}") or detail_path
        history_url = absolute_url(detail_path, f"/faces/billHistoryClient.xhtml?bill_id={bill_id}") or detail_path
        text_url = absolute_url(detail_path, f"/faces/billTextClient.xhtml?bill_id={bill_id}") or detail_path

        status_response = self.client.get(status_url)
        history_response = self.client.get(history_url)
        text_response = self.client.get(text_url)
        status_response.raise_for_status()
        history_response.raise_for_status()
        text_response.raise_for_status()

        status_soup = BeautifulSoup(status_response.text, "html.parser")
        history_soup = BeautifulSoup(history_response.text, "html.parser")
        text_soup = BeautifulSoup(text_response.text, "html.parser")

        bill_num = first_non_empty(
            normalize_california_bill_number(source_item.get("billNum")),
            self._bill_number_from_heading(text_soup),
            self._bill_number_from_heading(status_soup),
        )
        if not bill_num:
            raise ValueError(f"California bill number could not be determined from {detail_path}")

        title = first_non_empty(
            self._bill_title(text_soup, bill_num),
            clean_text(str(source_item.get("billTitle") or "")),
            clean_text(str(source_item.get("catchTitle") or "")),
            bill_num,
        )

        status_fields = self._status_fields(status_soup)
        bill_actions = self._history_rows(history_soup)
        latest_action = bill_actions[0] if bill_actions else {"statusDate": "", "statusMessage": ""}
        current_location = first_non_empty(status_fields.get("House Location"), status_fields.get("Senate Location"))
        last_action = first_non_empty(
            clean_text(str(latest_action.get("statusMessage") or "")),
            current_location,
            clean_text(str(source_item.get("billStatus") or "")),
        )
        last_action_date = parse_california_date(str(latest_action.get("statusDate") or ""))
        sponsor = self._clean_author(
            first_non_empty(
                status_fields.get("Lead Authors"),
                status_fields.get("Lead Author"),
                clean_text(str(source_item.get("sponsor") or "")),
            )
        )
        digest_text = first_non_empty(self._digest_text(text_soup), title)
        current_version_path, introduced_path, current_version_value = self._version_paths(
            base_url=str(text_response.url),
            bill_id=bill_id,
            soup=text_soup,
        )
        chapter = self._chapter_from_actions(bill_actions)
        signed_date = self._signed_date_from_actions(bill_actions, chapter)

        digest_parts: list[str] = []
        if digest_text:
            digest_parts.append(f"<p>{html.escape(digest_text)}</p>")
        if current_location and current_location.lower() not in digest_text.lower():
            digest_parts.append(f"<p>{html.escape(current_location)}</p>")

        return {
            "bill": bill_num,
            "billType": bill_num.split("-", 1)[0],
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": first_non_empty(last_action, current_location),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(str(current_version_value or "")),
            "sponsorStringHouse": sponsor if bill_num.startswith("A") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path,
            "digest": current_version_path,
            "summary": str(text_response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_value,
                    current_version_path,
                    last_action_date,
                    chapter,
                )
                if clean_text(str(part))
            ),
            "summaryHTML": f"<p>{html.escape(digest_text)}</p>" if digest_text else "",
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": str(self._bill_all(text_soup) or ""),
            "billActions": bill_actions,
            "amendments": [],
            "officialPage": str(status_response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _bill_id(detail_path: str, item: dict[str, Any]) -> str:
        for candidate in (detail_path, str(item.get("detailPath") or "")):
            parsed = urlparse(candidate)
            bill_id = clean_text(parse_qs(parsed.query).get("bill_id", [""])[0])
            if bill_id:
                return bill_id
        return ""

    @staticmethod
    def _bill_number_from_heading(soup: BeautifulSoup) -> str:
        for heading in soup.find_all(["h1", "h2", "h3"]):
            parsed = normalize_california_bill_number(heading.get_text(" ", strip=True).split(" ", 1)[0])
            if parsed:
                return parsed
            parsed = normalize_california_bill_number(heading.get_text(" ", strip=True))
            if parsed:
                return parsed
        return ""

    @staticmethod
    def _bill_title(soup: BeautifulSoup, bill_num: str) -> str:
        for heading in soup.find_all(["h1", "h2", "h3"]):
            text = clean_text(heading.get_text(" ", strip=True))
            if not text or bill_num not in text:
                continue
            if "(" in text:
                text = text.split("(", 1)[0].strip()
            prefix = f"{bill_num} "
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
            if text and text != bill_num:
                return text
        return ""

    @staticmethod
    def _status_fields(soup: BeautifulSoup) -> dict[str, str]:
        fields: dict[str, str] = {}
        for row in soup.select("div.statusRow"):
            label = row.find("label")
            value = row.find(class_=re.compile(r"statusCellData"))
            if label is None or value is None:
                continue
            key = clean_text(label.get_text(" ", strip=True)).rstrip(":")
            text = clean_text(value.get_text(" ", strip=True))
            if key:
                fields[key] = text if text != "-" else ""
        return fields

    @staticmethod
    def _history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            rows.append(
                {
                    "statusDate": parse_california_date(cells[0].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[1].get_text(" ", strip=True)),
                    "location": "",
                }
            )
        return rows

    @staticmethod
    def _clean_author(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        raw = re.sub(r"\s*\([A-Z]\)\s*$", "", raw)
        return clean_text(raw)

    @staticmethod
    def _bill_all(soup: BeautifulSoup) -> Tag | None:
        return soup.find(id="bill_all")

    def _digest_text(self, soup: BeautifulSoup) -> str:
        bill_all = self._bill_all(soup)
        if bill_all is None:
            return ""
        text = clean_text(bill_all.get_text("\n", strip=True))
        for pattern in (CALIFORNIA_DIGEST_PATTERN, CALIFORNIA_DIGEST_FALLBACK_PATTERN):
            match = pattern.search(text)
            if match is not None:
                return clean_text(match.group("digest"))
        return ""

    def _version_paths(self, *, base_url: str, bill_id: str, soup: BeautifulSoup) -> tuple[str | None, str | None, str]:
        options = [
            option
            for option in soup.find_all("option")
            if CALIFORNIA_VERSION_VALUE_PATTERN.fullmatch(clean_text(option.get("value") or ""))
        ]
        if not options:
            return None, None, ""
        current_option = options[0]
        introduced_option = options[-1]
        current_value = clean_text(current_option.get("value") or "")
        introduced_value = clean_text(introduced_option.get("value") or "")
        current_path = self._bill_pdf_url(base_url, bill_id, current_value)
        introduced_path = self._bill_pdf_url(base_url, bill_id, introduced_value)
        return current_path, introduced_path, current_value

    @staticmethod
    def _bill_pdf_url(base_url: str, bill_id: str, version: str) -> str | None:
        if not bill_id or not version:
            return None
        return absolute_url(base_url, f"/faces/billPdf.xhtml?bill_id={bill_id}&version={version}")

    @staticmethod
    def _chapter_from_actions(actions: list[dict[str, str]]) -> str:
        for action in actions:
            match = CALIFORNIA_CHAPTER_PATTERN.search(str(action.get("statusMessage") or ""))
            if match is not None:
                return clean_text(match.group("chapter"))
        return ""

    @staticmethod
    def _signed_date_from_actions(actions: list[dict[str, str]], chapter: str) -> str:
        for action in actions:
            message = clean_text(str(action.get("statusMessage") or ""))
            if "Approved by the Governor" in message:
                return parse_california_date(str(action.get("statusDate") or ""))
        if chapter:
            for action in actions:
                message = clean_text(str(action.get("statusMessage") or ""))
                if "Chaptered by Secretary of State" in message:
                    return parse_california_date(str(action.get("statusDate") or ""))
        return ""
