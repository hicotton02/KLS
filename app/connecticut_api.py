from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, html_to_text


CONNECTICUT_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB)\d{5}$", re.IGNORECASE)
CONNECTICUT_RESULT_ROW_PATTERN = re.compile(
    r"<td>\s*<a\s+href=['\"](?P<href>[^'\"]*cgabillstatus\.asp\?selBillType=Bill&bill_num=(?P<bill>(?:HB|SB)\d{5})&which_year=(?P<year>\d{4})[^'\"]*)['\"]\s*>"
    r"(?P=bill)</a>\s*</td>\s*<td>(?P<title>.*?)</td>\s*<td>(?P<info>.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
CONNECTICUT_HEADING_PATTERN = re.compile(
    r"(?P<label>.+?)\s+(?P<chamber>H\.B\.|S\.B\.)\s+No\.\s+(?P<number>\d+)",
    re.IGNORECASE,
)
CONNECTICUT_FILE_NUMBER_PATTERN = re.compile(r"\bFile No\.\s*(\d+)\b", re.IGNORECASE)
CONNECTICUT_PUBLIC_ACT_PATTERN = re.compile(r"\b(?:Public|Special)\s+Act\s+No\.\s*([0-9-]+)\b", re.IGNORECASE)
CONNECTICUT_CHAPTER_PATTERN = re.compile(r"\bChapter\s+(\d+)\b", re.IGNORECASE)
CONNECTICUT_AMENDMENT_NUMBER_PATTERN = re.compile(r"Amendment\s+#(\d+)", re.IGNORECASE)
CONNECTICUT_AMENDMENT_SCHEDULE_PATTERN = re.compile(r"Vote Tally Sheet-([A-Z])\b", re.IGNORECASE)


def parse_connecticut_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_connecticut_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if CONNECTICUT_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _fragment_text(value: str | None) -> str:
    raw = html.unescape(str(value or "")).replace("&nbsp;", " ")
    return clean_text(html_to_text(raw))


class ConnecticutApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.connecticut_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            verify=False,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        items_by_bill: dict[str, dict[str, Any]] = {}

        for low, high in (("1", "4999"), ("5001", "9999")):
            response = self.client.post(
                "/asp/CGABillInfo/CGABillInfoDisplay.asp",
                data={
                    "cboSessYr": str(year),
                    "optFindM": "range",
                    "txtLowBill": low,
                    "txtHiBill": high,
                },
            )
            response.raise_for_status()
            for item in self._parse_search_results(year, response.text):
                items_by_bill[item["billNum"]] = item

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(str(response.url), soup)
        title = self._title(soup) or bill_num
        summary = self._summary(soup)
        sponsor = self._introduced_by(soup)
        heading_label = self._heading_label(soup)
        text_versions = self._section_links(soup, "Text of Bill")
        committee_actions = self._section_links(soup, "Committee Actions")
        fiscal_notes = self._section_links(soup, "Fiscal Notes")
        bill_analyses = self._section_links(soup, "Bill Analyses")
        actions = self._history_rows(soup)

        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": "", "location": ""}
        current_version = text_versions[0] if text_versions else {}
        introduced_version = text_versions[-1] if text_versions else {}
        signed_date = self._signed_date(actions)
        chapter_no = self._chapter(actions)
        enrolled_number = str(current_version.get("label") or heading_label or "")

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter_no,
            "enrolledNumber": enrolled_number,
            "sponsorStringHouse": sponsor if bill_num.startswith("HB") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("SB") else None,
            "introduced": str(introduced_version.get("documentUrl") or "") or None,
            "digest": str((bill_analyses[:1] or fiscal_notes[:1] or [{}])[0].get("documentUrl") or "") or None,
            "summary": str(response.url),
            "currentVersionPath": str(current_version.get("documentUrl") or "") or None,
            "currentVersionFingerprint": "|".join(
                f"{item.get('label')}:{item.get('documentUrl')}"
                for item in text_versions
                if item.get("label") and item.get("documentUrl")
            ),
            "summaryHTML": self._paragraph_html(summary or title),
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": self._amendments_from_committee_actions(committee_actions),
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _parse_search_results(self, year: int, html_text: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in CONNECTICUT_RESULT_ROW_PATTERN.finditer(html_text):
            if str(match.group("year") or "").strip() != str(year):
                continue
            bill_num = normalize_connecticut_bill_number(match.group("bill"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            title = _fragment_text(match.group("title")) or bill_num
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(self.settings.connecticut_site_base, match.group("href")),
                }
            )
        return items

    @staticmethod
    def _paragraph_html(value: str | None) -> str:
        text = clean_text(value)
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _bill_number(detail_url: str, soup: BeautifulSoup) -> str:
        match = re.search(r"bill_num=((?:HB|SB)\d{5})", detail_url, re.IGNORECASE)
        if match:
            return normalize_connecticut_bill_number(match.group(1))
        heading = soup.find("h3")
        if heading is not None:
            parsed = ConnecticutApiClient._heading_bill_number(heading.get_text(" ", strip=True))
            if parsed:
                return parsed
        raise ValueError(f"Connecticut bill number could not be parsed from {detail_url}")

    @staticmethod
    def _heading_bill_number(value: str | None) -> str:
        raw = clean_text(value)
        match = CONNECTICUT_HEADING_PATTERN.search(raw)
        if match is None:
            return ""
        chamber = "HB" if "H.B." in match.group("chamber").upper() else "SB"
        number = int(match.group("number"))
        return f"{chamber}{number:05d}"

    @staticmethod
    def _heading_label(soup: BeautifulSoup) -> str:
        heading = soup.find("h3")
        if heading is None:
            return ""
        raw = clean_text(heading.get_text(" ", strip=True))
        match = CONNECTICUT_HEADING_PATTERN.search(raw)
        if match is None:
            return raw
        return clean_text(match.group("label"))

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        heading = soup.find("h4")
        if heading is None:
            return ""
        return clean_text(heading.get_text(" ", strip=True))

    @staticmethod
    def _summary(soup: BeautifulSoup) -> str:
        heading = soup.find("h4")
        if heading is None:
            return ""
        paragraph = heading.find_next_sibling("p")
        if paragraph is None:
            return ""
        return clean_text(paragraph.get_text(" ", strip=True))

    @staticmethod
    def _introduced_by(soup: BeautifulSoup) -> str:
        marker = soup.find("h5", string=lambda value: isinstance(value, str) and "Introduced by" in value)
        if marker is None:
            return ""
        pieces: list[str] = []
        for sibling in marker.next_siblings:
            if isinstance(sibling, NavigableString):
                text = clean_text(str(sibling))
                if text:
                    pieces.append(text)
            elif isinstance(sibling, Tag):
                if sibling.name and sibling.name.lower().startswith("h"):
                    break
                text = clean_text(sibling.get_text(" ", strip=True))
                if text:
                    pieces.append(text)
            if pieces:
                break
        return clean_text(" ".join(pieces))

    def _section_links(self, soup: BeautifulSoup, label: str) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for table in soup.find_all("table", summary=lambda value: isinstance(value, str) and "Status of bills" in value):
            header = table.find("thead")
            if header is None:
                continue
            header_text = clean_text(header.get_text(" ", strip=True))
            if label.lower() not in header_text.lower():
                continue
            for row in table.find_all("tr"):
                links = row.find_all("a", href=True)
                if not links:
                    continue
                primary = links[0]
                item_label = clean_text(primary.get_text(" ", strip=True))
                document_url = absolute_url(self.settings.connecticut_site_base, primary.get("href")) or ""
                if not item_label or not document_url:
                    continue
                items.append(
                    {
                        "label": item_label,
                        "documentUrl": document_url,
                    }
                )
        return items

    @staticmethod
    def _history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table", summary=lambda value: isinstance(value, str) and "Bill history" in value)
        if table is None:
            return []
        actions: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            status_date = parse_connecticut_date(cells[1].get_text(" ", strip=True))
            location = clean_text(cells[2].get_text(" ", strip=True)).strip("()")
            status_message = clean_text(cells[3].get_text(" ", strip=True))
            if not status_date or not status_message:
                continue
            actions.append(
                {
                    "statusDate": status_date,
                    "statusMessage": status_message,
                    "location": location,
                }
            )
        actions.reverse()
        return actions

    @staticmethod
    def _signed_date(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            raw = str(action.get("statusMessage") or "").lower()
            if "signed by governor" in raw or "became law" in raw:
                return str(action.get("statusDate") or "")
        return ""

    @staticmethod
    def _chapter(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            action_text = str(action.get("statusMessage") or "")
            public_act = CONNECTICUT_PUBLIC_ACT_PATTERN.search(action_text)
            if public_act is not None:
                return public_act.group(1)
            chapter = CONNECTICUT_CHAPTER_PATTERN.search(action_text)
            if chapter is not None:
                return chapter.group(1)
        return ""

    @staticmethod
    def _amendments_from_committee_actions(actions: list[dict[str, str]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for index, action in enumerate(actions, start=1):
            label = str(action.get("label") or "").strip()
            if "amendment" not in label.lower():
                continue
            amendment_number = ConnecticutApiClient._amendment_number(label)
            items.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": "",
                    "order": label,
                    "sequence": index,
                    "status": "Filed",
                    "sponsor": "",
                    "documentUrl": action.get("documentUrl"),
                }
            )
        return items

    @staticmethod
    def _amendment_number(label: str) -> str:
        raw = clean_text(label)
        numbered = CONNECTICUT_AMENDMENT_NUMBER_PATTERN.search(raw)
        if numbered is not None:
            return f"Committee Amendment #{numbered.group(1)}"
        scheduled = CONNECTICUT_AMENDMENT_SCHEDULE_PATTERN.search(raw)
        if scheduled is not None:
            return f"Committee Amendment {scheduled.group(1).upper()}"
        return raw or "Committee Amendment"
