from __future__ import annotations

import html
import math
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.http_retry import get_with_retries
from app.settings import Settings
from app.text_utils import clean_text


MASSACHUSETTS_BILL_NUMBER_PATTERN = re.compile(r"^(HD|SD|H|S)\d+[A-Z]*$", re.IGNORECASE)
MASSACHUSETTS_BILL_TEXT_PATTERN = re.compile(
    r"^(HD|SD|H|S)\.?\s*(\d+)(?:\s*(?:,?\s*APPENDIX)?\s*([A-Z]))?$",
    re.IGNORECASE,
)
MASSACHUSETTS_CHAPTER_PATTERN = re.compile(r"\bChapter\s+(\d+)\b", re.IGNORECASE)


def normalize_massachusetts_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    text_match = MASSACHUSETTS_BILL_TEXT_PATTERN.fullmatch(raw.replace("\n", " "))
    if text_match is not None:
        suffix = text_match.group(3) or ""
        return f"{text_match.group(1).upper()}{text_match.group(2)}{suffix}"
    raw = re.sub(r"\s+", "", raw.replace(".", ""))
    if MASSACHUSETTS_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_massachusetts_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]*)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)), match.group(3))


def _session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 1 else value - 1


def _ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


def massachusetts_general_court(year: int) -> int:
    session_start = _session_start_year(year)
    return 179 + ((session_start - 1995) // 2)


def _bill_number_from_path(path: str | None) -> str:
    match = re.search(r"/Bills/\d+/([A-Z]+\d+[A-Z]*)$", str(path or ""), re.IGNORECASE)
    if match is None:
        return ""
    return normalize_massachusetts_bill_number(match.group(1))


class MassachusettsApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.massachusetts_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=max(self.settings.request_timeout_seconds, 180.0),
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        general_court = massachusetts_general_court(year)
        token, total_count = self._general_court_refiner(general_court)
        total_pages = max(1, math.ceil(total_count / 25))

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for page in range(1, total_pages + 1):
            response = self._get(
                "/Bills/Search",
                params={
                    "SearchTerms": "",
                    "Page": page,
                    "Refinements[lawsgeneralcourt]": token,
                },
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            table = soup.find("table", id="searchTable")
            if table is None:
                continue
            for row in table.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 4:
                    continue
                bill_anchor = cells[1].find("a", href=True)
                title_anchor = cells[3].find("a", href=True)
                if bill_anchor is None:
                    continue
                detail_path = absolute_url(self.settings.massachusetts_site_base, bill_anchor.get("href")) or ""
                bill_num = _bill_number_from_path(detail_path) or normalize_massachusetts_bill_number(
                    bill_anchor.get_text(" ", strip=True)
                )
                if not bill_num or bill_num in seen:
                    continue
                seen.add(bill_num)
                title_text = clean_text(cells[3].get_text(" ", strip=True)) or clean_text(
                    title_anchor.get_text(" ", strip=True) if title_anchor is not None else ""
                )
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                        "catchTitle": title_text or bill_num,
                        "billTitle": title_text or bill_num,
                        "sponsor": clean_text(cells[2].get_text(" ", strip=True)),
                        "billStatus": "",
                        "lastAction": "",
                        "lastActionDate": "",
                        "detailPath": detail_path,
                        "currentVersionFingerprint": "|".join(
                            part
                            for part in (
                                detail_path,
                                title_text,
                            )
                            if part
                        ),
                    }
                )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num, general_court = self._bill_identifiers(str(response.url), item)
        title = self._title(soup) or clean_text((item or {}).get("catchTitle")) or bill_num
        pinslip = clean_text((soup.find("p", id="pinslip") or Tag(name="p")).get_text(" ", strip=True))
        presenter = self._bill_info_value(soup, "Presenter:")
        status_value = self._bill_info_value(soup, "Status:")

        history_actions = self._history_actions(bill_num, general_court)
        last_action_entry = history_actions[-1] if history_actions else {}
        last_action = clean_text(last_action_entry.get("statusMessage") or status_value)
        last_action_date = clean_text(last_action_entry.get("statusDate"))
        chapter = self._chapter_from_actions(history_actions)
        signed_date = self._signed_date(history_actions)

        chamber_segment = "House" if bill_num.startswith("H") else "Senate"
        text_path = self._action_link(soup, "View Text") or absolute_url(
            self.settings.massachusetts_site_base,
            f"/Bills/{general_court}/{bill_num}/{chamber_segment}/Bill/Text",
        )
        pdf_path = self._action_link(soup, "Download PDF") or absolute_url(
            self.settings.massachusetts_site_base,
            f"/Bills/{general_court}/{bill_num}.pdf",
        )

        sponsor = presenter or clean_text((item or {}).get("sponsor"))

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": clean_text(status_value or last_action),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": "",
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": text_path or None,
            "digest": pdf_path or None,
            "summary": str(response.url),
            "currentVersionPath": text_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    text_path,
                    pdf_path,
                    status_value,
                    last_action,
                    last_action_date,
                    chapter,
                    str(len(history_actions)),
                )
                if part
            ),
            "summaryHTML": self._summary_html(title, pinslip, status_value),
            "digestHTML": self._actions_html(history_actions),
            "currentBillHTML": "",
            "billActions": history_actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        return get_with_retries(
            self.client,
            url,
            max_attempts=4,
            base_delay_seconds=3.0,
            max_delay_seconds=60.0,
            **kwargs,
        )

    def _general_court_refiner(self, general_court: int) -> tuple[str, int]:
        response = self._get("/Bills/Search", params={"SearchTerms": ""})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        prefix = _ordinal(general_court)

        group = soup.find("div", attrs={"data-refinername": "lawsgeneralcourt"})
        if group is None:
            raise ValueError("Massachusetts bills search did not expose a general court refiner")

        for checkbox in group.find_all("input", attrs={"data-refinertoken": True}):
            label = checkbox.find_parent("label")
            label_text = clean_text(label.get_text(" ", strip=True) if label is not None else "")
            if not label_text.startswith(prefix):
                continue
            count_match = re.search(r"\((\d+)\)\s*$", label_text)
            if count_match is None:
                raise ValueError(f"Massachusetts general court refiner count missing for {prefix}")
            token = clean_text(checkbox.get("data-refinertoken"))
            return token, int(count_match.group(1))

        raise ValueError(f"Massachusetts general court refiner not found for {prefix}")

    @staticmethod
    def _bill_identifiers(detail_url: str, item: dict[str, Any] | None = None) -> tuple[str, int]:
        match = re.search(r"/Bills/(?P<court>\d+)/(?P<bill>[A-Z]+\d+[A-Z]*)$", detail_url, re.IGNORECASE)
        if match is not None:
            bill_num = normalize_massachusetts_bill_number(match.group("bill"))
            if bill_num:
                return bill_num, int(match.group("court"))
        fallback_bill = normalize_massachusetts_bill_number((item or {}).get("billNum"))
        fallback_year = int((item or {}).get("year") or 0)
        if fallback_bill and fallback_year:
            return fallback_bill, massachusetts_general_court(fallback_year)
        raise ValueError(f"Massachusetts bill identifiers could not be parsed from {detail_url}")

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        container = soup.find("div", id="contentContainer")
        if container is None:
            return ""
        for heading in container.find_all("h2"):
            text = clean_text(heading.get_text(" ", strip=True))
            if text and text.lower() != "search the legislature" and not text.startswith("Bill "):
                return text
        return ""

    @staticmethod
    def _bill_info_value(soup: BeautifulSoup, label: str) -> str:
        dt = soup.find("dt", string=lambda value: isinstance(value, str) and clean_text(value) == label)
        if dt is None:
            return ""
        dd = dt.find_next_sibling("dd")
        if dd is None:
            return ""
        return clean_text(dd.get_text(" ", strip=True))

    def _action_link(self, soup: BeautifulSoup, label: str) -> str:
        anchor = soup.find("a", string=lambda value: isinstance(value, str) and clean_text(value) == label)
        if anchor is None:
            return ""
        return absolute_url(self.settings.massachusetts_site_base, anchor.get("href")) or ""

    def _history_actions(self, bill_num: str, general_court: int) -> list[dict[str, str]]:
        response = self._get(f"/Bills/{general_court}/{bill_num}/BillHistory")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        actions: list[dict[str, str]] = []
        for row in soup.select("table tbody tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            actions.append(
                {
                    "statusDate": parse_massachusetts_date(cells[0].get_text(" ", strip=True)),
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[2].get_text(" ", strip=True)),
                }
            )
        return actions

    @staticmethod
    def _summary_html(title: str, pinslip: str, status_value: str) -> str:
        parts: list[str] = []
        if title:
            parts.append(f"<p>{html.escape(title)}</p>")
        if pinslip:
            parts.append(f"<p>{html.escape(pinslip)}</p>")
        if status_value:
            parts.append(f"<p><strong>Status:</strong> {html.escape(status_value)}</p>")
        return "".join(parts)

    @staticmethod
    def _actions_html(actions: list[dict[str, str]]) -> str:
        return "".join(
            f"<p><strong>{html.escape(action['statusDate'])}</strong>: {html.escape(action['statusMessage'])}</p>"
            for action in actions[:6]
            if action.get("statusMessage")
        )

    @staticmethod
    def _chapter_from_actions(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            match = MASSACHUSETTS_CHAPTER_PATTERN.search(clean_text(action.get("statusMessage")))
            if match is not None:
                return match.group(1)
        return ""

    @staticmethod
    def _signed_date(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            message = clean_text(action.get("statusMessage")).lower()
            if "signed by the governor" in message or "became law" in message or "chapter " in message:
                return clean_text(action.get("statusDate"))
        return ""
