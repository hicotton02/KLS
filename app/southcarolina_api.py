from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


SOUTH_CAROLINA_BILL_NUMBER_PATTERN = re.compile(r"^([HS])\.\s*(\d+)$", re.IGNORECASE)
SOUTH_CAROLINA_INTRO_PATH_PATTERN = re.compile(
    r"(?P<prefix>/sess(?P<session>\d+)_(?P<start>\d{4})-(?P<end>\d{4}))/(?P<kind>[hs])intro(?P<year2>\d{2})/\d{8}\.htm",
    re.IGNORECASE,
)


def parse_south_carolina_date(value: str | None) -> str:
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


def normalize_south_carolina_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = SOUTH_CAROLINA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1).upper()}{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z])(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class SouthCarolinaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.south_carolina_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for intro_index_path in ("/sessphp/hintros.php", "/sessphp/sintros.php"):
            response = self.client.get(intro_index_path)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            date_paths = [
                absolute_url(self.settings.south_carolina_site_base, anchor.get("href"))
                for anchor in soup.find_all("a", href=True)
                if f"intro{year % 100:02d}" in str(anchor.get("href") or "").lower()
            ]
            for date_path in date_paths:
                if not date_path:
                    continue
                intro_match = SOUTH_CAROLINA_INTRO_PATH_PATTERN.search(date_path)
                if intro_match is None:
                    continue
                session_prefix = intro_match.group("prefix")
                session_id = intro_match.group("session")

                page = self.client.get(date_path)
                page.raise_for_status()
                page_soup = BeautifulSoup(page.text, "html.parser")
                for anchor in page_soup.find_all("a", href=True):
                    href = str(anchor.get("href") or "")
                    if not href.startswith("/billsearch.php?billnumbers="):
                        continue
                    label = clean_text(anchor.get_text(" ", strip=True))
                    bill_num = normalize_south_carolina_bill_number(label)
                    if not bill_num or bill_num in seen:
                        continue
                    params = parse_qs(urlparse(href).query)
                    bill_numbers = clean_text((params.get("billnumbers") or [""])[0])
                    if not bill_numbers:
                        continue
                    seen.add(bill_num)
                    sponsor, title = self._intro_summary(anchor)
                    detail_path = absolute_url(
                        self.settings.south_carolina_site_base,
                        f"{session_prefix}/bills/{bill_numbers}.htm",
                    )
                    items.append(
                        {
                            "billNum": bill_num,
                            "billType": bill_num[0],
                            "catchTitle": title or bill_num,
                            "billTitle": title or bill_num,
                            "sponsor": sponsor,
                            "billStatus": "",
                            "lastAction": "",
                            "lastActionDate": "",
                            "detailPath": detail_path,
                            "sessionId": session_id,
                            "billNumberOnly": bill_numbers,
                        }
                    )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        cover = soup.find("div", class_="statusCoverSheet") or soup

        bill_num = self._bill_number(cover, item)
        summary = self._summary(cover) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num
        sponsor = self._sponsors(cover) or clean_text(str((item or {}).get("sponsor") or ""))
        actions = self._actions(cover)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": ""}
        last_action = clean_text(str(latest_action.get("statusMessage") or ""))
        last_action_date = parse_south_carolina_date(str(latest_action.get("statusDate") or ""))
        if not last_action:
            last_action = self._current_status_text(cover)
        governor_action = self._governor_action(cover)
        if governor_action:
            last_action = governor_action["action"]
            if governor_action["date"]:
                last_action_date = governor_action["date"]
        signed_date = last_action_date if "signed" in last_action.lower() or "approved" in last_action.lower() else ""
        word_link = self._word_link(cover, str(response.url))
        chapter = self._act_number(cover)

        return {
            "bill": bill_num,
            "billType": bill_num[0],
            "catchTitle": summary,
            "sponsor": sponsor,
            "billTitle": summary,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": chapter,
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": word_link,
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": str(response.url),
            "currentVersionFingerprint": "|".join(part for part in [str(response.url), word_link or ""] if part),
            "summaryHTML": f"<p>{summary}</p>" if summary else "",
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _intro_summary(anchor: Tag) -> tuple[str, str]:
        pieces: list[str] = []
        for sibling in anchor.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "p":
                break
            if isinstance(sibling, NavigableString):
                pieces.append(str(sibling))
        raw = clean_text(" ".join(pieces))
        match = re.search(r"--\s*(?P<sponsor>.*?):\s*(?P<title>.*)", raw)
        if match is None:
            return ("", "")
        return (clean_text(match.group("sponsor")), clean_text(match.group("title")))

    @staticmethod
    def _bill_number(cover: Tag, item: dict[str, Any] | None) -> str:
        for paragraph in cover.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            parsed = normalize_south_carolina_bill_number(text)
            if parsed:
                return parsed
        fallback = clean_text(str((item or {}).get("billNum") or ""))
        if re.fullmatch(r"[HS]\d+", fallback):
            return fallback
        raise ValueError("South Carolina bill number could not be parsed")

    @staticmethod
    def _summary(cover: Tag) -> str:
        text = clean_text(cover.get_text(" ", strip=True))
        match = re.search(r"Summary:\s*(.*?)\s*HISTORY OF LEGISLATIVE ACTIONS", text, re.IGNORECASE)
        if match is not None:
            return clean_text(match.group(1))
        return ""

    @staticmethod
    def _sponsors(cover: Tag) -> str:
        for paragraph in cover.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            if "Sponsors:" in text or "Sponsor:" in text:
                match = re.search(r"Sponsors?:\s*(.*?)\s*Document Path:", text)
                if match is not None:
                    return clean_text(match.group(1))
        return ""

    @staticmethod
    def _current_status_text(cover: Tag) -> str:
        for paragraph in cover.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            if text.startswith("Introduced in the ") or text.startswith("Prefiled in the "):
                return text
        return ""

    @staticmethod
    def _governor_action(cover: Tag) -> dict[str, str] | None:
        text = clean_text(cover.get_text(" ", strip=True))
        match = re.search(
            r"Governor's Action:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4}),\s*(.*?)\s*(?:Summary:|HISTORY OF LEGISLATIVE ACTIONS)",
            text,
        )
        if match is None:
            return None
        action_date = parse_south_carolina_date(match.group(1))
        action = clean_text(match.group(2))
        return {"date": action_date, "action": f"Governor's Action: {action}"}

    @staticmethod
    def _actions(cover: Tag) -> list[dict[str, str]]:
        parsed: list[dict[str, str]] = []
        table = cover.find("table")
        if table is None:
            return parsed
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            parsed.append(
                {
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusDate": parse_south_carolina_date(cells[0].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[2].get_text(" ", strip=True)),
                }
            )
        return parsed

    @staticmethod
    def _word_link(cover: Tag, base_url: str) -> str | None:
        anchor = cover.find("a", string=lambda value: isinstance(value, str) and "This Bill" in value)
        if anchor is None:
            return None
        return absolute_url(base_url, anchor.get("href"))

    @staticmethod
    def _act_number(cover: Tag) -> str:
        for paragraph in cover.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True))
            match = re.match(r"A\d+,\s*R\d+,\s*[HS]\.\s*\d+", text)
            if match is not None:
                return text
        return ""
