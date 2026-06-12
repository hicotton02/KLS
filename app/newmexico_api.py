from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


NEW_MEXICO_BILL_NUMBER_PATTERN = re.compile(r"^(H|S)(B|JM|JR|M|R)\d+$", re.IGNORECASE)
NEW_MEXICO_ACTION_ITEM_PATTERN = re.compile(
    r"Legislative Day:\s*(?P<leg_day>\d+)\s*Calendar Day:\s*(?P<date>\d{2}/\d{2}/\d{4})\s*(?P<action>.+)",
    re.IGNORECASE,
)


def parse_new_mexico_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%b. %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_new_mexico_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace("*", "").replace(" ", "")
    if NEW_MEXICO_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class NewMexicoApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.new_mexico_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._session_by_year: dict[int, str] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = self.session_code_for_year(year)
        response = self.client.get("/Legislation/Legislation_List", params={"Session": session_code})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = None
        for candidate in soup.find_all("table"):
            headers = [clean_text(th.get_text(" ", strip=True)) for th in candidate.find_all("th")]
            if headers[:5] == ["Bill ID", "Title", "Sponsor", "Actions", "Session"]:
                table = candidate
                break
        if table is None:
            raise ValueError(f"New Mexico legislation table was not found for session {session_code}")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 5:
                continue
            bill_link = cells[0].find("a", href=True)
            if bill_link is None:
                continue
            bill_num = self._bill_number_from_href(bill_link.get("href"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            title = clean_text(cells[1].get_text(" ", strip=True)) or bill_num
            sponsor = clean_text(cells[2].get_text(" ", strip=True))
            last_action = clean_text(cells[3].get_text(" ", strip=True))
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": last_action,
                    "lastAction": last_action,
                    "lastActionDate": "",
                    "detailPath": absolute_url(str(response.url), bill_link.get("href")),
                }
            )
        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number_from_href(str(response.url))
        if not bill_num:
            bill_num = normalize_new_mexico_bill_number(
                clean_text((soup.find("span", id=re.compile(r"lblBillID$")) or {}).get_text(" ", strip=True))
            )
        if not bill_num:
            raise ValueError(f"New Mexico bill number could not be parsed from {detail_path}")

        fields = self._main_fields(soup)
        title = first_non_empty_text(fields.get("Title"), clean_text(str((item or {}).get("billTitle") or "")), bill_num)
        sponsor_names = self._sponsor_names(soup)
        sponsor = ", ".join(sponsor_names) if sponsor_names else clean_text(str((item or {}).get("sponsor") or ""))
        action_summary = self._action_summary(soup)
        actions = self._action_rows(soup)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": action_summary}

        text_links = self._text_links(soup, str(response.url))
        introduced_path = text_links.get("introduced_html") or text_links.get("introduced_pdf") or None
        current_version_path = text_links.get("final_pdf") or text_links.get("introduced_html") or text_links.get("introduced_pdf") or None
        digest_path = self._named_link_url(soup, re.compile(r"Fiscal Impact Report", re.IGNORECASE))
        current_location = fields.get("Current Location")
        chapter = first_non_empty_text(self._chapter_from_text(action_summary), self._chapter_from_actions(actions))
        signed_date = self._signed_date_from_actions(actions)
        bill_status = first_non_empty_text(current_location, action_summary, clean_text(str(latest_action.get("statusMessage") or "")))
        last_action = first_non_empty_text(clean_text(str(latest_action.get("statusMessage") or "")), action_summary, bill_status)
        last_action_date = parse_new_mexico_date(str(latest_action.get("statusDate") or ""))

        fingerprint_parts = [
            clean_text(str(current_version_path or "")),
            clean_text(str(introduced_path or "")),
            clean_text(str(digest_path or "")),
            clean_text(action_summary),
            clean_text(str(last_action_date or "")),
        ]

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": bill_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(str(chapter or "")),
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(part for part in fingerprint_parts if part),
            "summaryHTML": f"<p>{title}</p>" if title else "",
            "digestHTML": f"<p>{current_location}</p>" if current_location else "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def session_code_for_year(self, year: int) -> str:
        cached = self._session_by_year.get(year)
        if cached is not None:
            return cached

        response = self.client.get("/Legislation/Legislation_List")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for option in soup.find_all("option"):
            value = clean_text(option.get("value") or "")
            label = clean_text(option.get_text(" ", strip=True))
            if label.startswith(f"{year} "):
                self._session_by_year[year] = value
                return value
        raise ValueError(f"New Mexico session code could not be determined for {year}")

    @staticmethod
    def _bill_number_from_href(href: str | None) -> str:
        parsed = urlparse(str(href or ""))
        query = parse_qs(parsed.query)
        chamber = clean_text(query.get("chamber", [""])[0]).upper()
        leg_type = clean_text(query.get("legType", [""])[0]).upper()
        leg_no = clean_text(query.get("legNo", [""])[0])
        if not chamber or not leg_type or not leg_no:
            return ""
        return normalize_new_mexico_bill_number(f"{chamber}{leg_type}{leg_no}")

    @staticmethod
    def _main_fields(soup: BeautifulSoup) -> dict[str, str]:
        table = soup.find("table", id="MainContent_formViewLegislation")
        if table is None:
            return {}
        cells = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all(["td", "th"])]
        fields: dict[str, str] = {}
        for index in range(0, len(cells) - 1, 2):
            key = cells[index]
            value = cells[index + 1]
            if key:
                fields[key] = value
        return fields

    @staticmethod
    def _sponsor_names(soup: BeautifulSoup) -> list[str]:
        names: list[str] = []
        for link in soup.find_all("a", id=re.compile(r"tabPanelSponsors_dataListSponsors_linkSponsor_", re.IGNORECASE)):
            name = clean_text(link.get_text(" ", strip=True))
            if name and name not in names:
                names.append(name)
        return names

    @staticmethod
    def _named_link_url(soup: BeautifulSoup, pattern: re.Pattern[str]) -> str:
        link = soup.find("a", string=lambda value: isinstance(value, str) and bool(pattern.search(value)))
        if link is None:
            return ""
        return absolute_url("https://www.nmlegis.gov", link.get("href")) or ""

    @staticmethod
    def _text_links(soup: BeautifulSoup, detail_url: str) -> dict[str, str]:
        panel = soup.find("div", id="MainContent_panelLegislationInformation")
        if panel is None:
            return {}
        links: dict[str, str] = {}
        for link in panel.find_all("a", href=True):
            label = clean_text(link.get_text(" ", strip=True))
            url = absolute_url(detail_url, link.get("href")) or ""
            lowered = label.lower()
            if "introduced (html)" in lowered:
                links["introduced_html"] = url
            elif "introduced (pdf)" in lowered:
                links["introduced_pdf"] = url
            elif lowered.startswith("final version"):
                links["final_pdf"] = url
        return links

    @staticmethod
    def _action_summary(soup: BeautifulSoup) -> str:
        table = soup.find("table", id=re.compile(r"formViewActionText$", re.IGNORECASE))
        if table is None:
            return ""
        text = clean_text(table.get_text(" ", strip=True))
        match = re.search(r"ActionText:\s*(.+?)\s*Key to Abbreviations", text, re.IGNORECASE)
        if match is not None:
            return clean_text(match.group(1))
        return text.replace("ActionText:", "", 1).strip()

    @classmethod
    def _action_rows(cls, soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table", id=re.compile(r"dataListActions$", re.IGNORECASE))
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for span in table.find_all("span", class_=re.compile(r"list-group-item")):
            text = clean_text(span.get_text(" ", strip=True))
            if not text:
                continue
            match = NEW_MEXICO_ACTION_ITEM_PATTERN.search(text)
            if match is not None:
                rows.append(
                    {
                        "location": "",
                        "statusDate": parse_new_mexico_date(match.group("date")),
                        "statusMessage": clean_text(match.group("action")),
                    }
                )
            else:
                rows.append({"location": "", "statusDate": "", "statusMessage": text})
        return rows

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        match = re.search(r"\bCh\.\s*(\d+)\b", str(value or ""), re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    @classmethod
    def _chapter_from_actions(cls, actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            chapter = cls._chapter_from_text(action.get("statusMessage"))
            if chapter:
                return chapter
        return ""

    @staticmethod
    def _signed_date_from_actions(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            if "signed by governor" in str(action.get("statusMessage") or "").lower():
                return parse_new_mexico_date(str(action.get("statusDate") or ""))
        return ""


def first_non_empty_text(*values: str | None) -> str:
    for value in values:
        text = clean_text(str(value or ""))
        if text:
            return text
    return ""
