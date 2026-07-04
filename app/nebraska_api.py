from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.http_retry import get_with_retries
from app.settings import Settings
from app.text_utils import clean_text


NEBRASKA_BILL_NUMBER_PATTERN = re.compile(r"^(LB|LR)\s*\.?\s*(\d+)([A-Z]?)$", re.IGNORECASE)
NEBRASKA_DETAIL_PATH_PATTERN = re.compile(r"DocumentID=(\d+)", re.IGNORECASE)


def parse_nebraska_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_nebraska_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = NEBRASKA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    prefix = match.group(1).upper()
    number = int(match.group(2))
    suffix = match.group(3).upper()
    return f"{prefix}{number}{suffix}"


def _sort_bill_key(bill_num: str) -> tuple[str, int, str]:
    match = re.fullmatch(r"([A-Z]+)(\d+)([A-Z]?)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0, "")
    return (match.group(1), int(match.group(2)), match.group(3))


class NebraskaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.nebraska_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self._get(
            "/bills/search_by_date.php",
            params={"SessionDay": str(year), "print": "csv"},
        )
        response.raise_for_status()
        text = response.text.lstrip("\ufeff").strip()
        reader = csv.DictReader(io.StringIO(text))

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in reader:
            bill_num = normalize_nebraska_bill_number(row.get("Document"))
            document_id = clean_text(str(row.get("Document ID") or ""))
            if not bill_num or not document_id or bill_num in seen:
                continue
            seen.add(bill_num)
            title = clean_text(str(row.get("Description") or "")) or bill_num
            sponsor = clean_text(str(row.get("Primary Introducer") or ""))
            status = clean_text(str(row.get("Status") or ""))
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": status,
                    "lastAction": status,
                    "lastActionDate": "",
                    "detailPath": absolute_url(
                        self.settings.nebraska_site_base,
                        f"/bills/view_bill.php?DocumentID={document_id}",
                    ),
                    "documentId": document_id,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(soup, item)
        bill_title = self._bill_title(soup) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num
        sponsor = self._introducer(soup) or clean_text(str((item or {}).get("sponsor") or ""))
        introduced_date = self._introduced_date(soup)
        document_links = self._document_links(soup, str(response.url))
        current_document = self._pick_current_document(document_links)
        actions_path = self._actions_path(soup, detail_path, item)
        actions = self._fetch_actions(actions_path)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": clean_text(str((item or {}).get("billStatus") or ""))}
        last_action = clean_text(str(latest_action.get("statusMessage") or "")) or clean_text(str((item or {}).get("billStatus") or ""))
        last_action_date = parse_nebraska_date(str(latest_action.get("statusDate") or "")) or introduced_date
        signed_date = last_action_date if "governor" in last_action.lower() or "signed" in last_action.lower() else ""

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": bill_title,
            "sponsor": sponsor,
            "billTitle": bill_title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": clean_text(str(current_document.get("label") or "")) if current_document else "",
            "sponsorStringHouse": sponsor,
            "sponsorStringSenate": None,
            "introduced": self._document_by_label(document_links, "Introduced"),
            "digest": self._document_by_label(document_links, "Fiscal Note"),
            "summary": str(response.url),
            "currentVersionPath": current_document.get("url") if current_document else None,
            "currentVersionFingerprint": "|".join(link["url"] for link in document_links if link.get("url")),
            "summaryHTML": f"<p>{bill_title}</p>" if bill_title else "",
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        return get_with_retries(
            self.client,
            url,
            max_attempts=7,
            base_delay_seconds=2.0,
            max_delay_seconds=90.0,
            **kwargs,
        )

    @staticmethod
    def _bill_number(soup: BeautifulSoup, item: dict[str, Any] | None) -> str:
        heading = soup.find("h2")
        if heading is not None:
            raw = clean_text(heading.get_text(" ", strip=True)).split(" - ", 1)[0]
            parsed = normalize_nebraska_bill_number(raw)
            if parsed:
                return parsed
        fallback = normalize_nebraska_bill_number((item or {}).get("billNum"))
        if fallback:
            return fallback
        raise ValueError("Nebraska bill number could not be parsed")

    @staticmethod
    def _bill_title(soup: BeautifulSoup) -> str:
        heading = soup.find("h2")
        if heading is None:
            return ""
        raw = clean_text(heading.get_text(" ", strip=True))
        if " - " in raw:
            return raw.split(" - ", 1)[1].strip()
        return raw

    @staticmethod
    def _introducer(soup: BeautifulSoup) -> str:
        for anchor in soup.select("a[href*='/bills/search_by_introducer.php']"):
            label = clean_text(anchor.get_text(" ", strip=True))
            if label and label.lower() not in {"introduced by:", "date of introduction:"}:
                return label
        return ""

    @staticmethod
    def _introduced_date(soup: BeautifulSoup) -> str:
        for anchor in soup.select("a[href*='/bills/search_by_date.php']"):
            label = clean_text(anchor.get_text(" ", strip=True))
            if label:
                return parse_nebraska_date(label)
        return ""

    @staticmethod
    def _document_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            href = str(anchor.get("href") or "")
            if not label or label == "View All Recorded Votes":
                continue
            if not any(token in href.lower() for token in ("/floordocs/", ".pdf", "/reports/fiscal")):
                continue
            url = absolute_url(base_url, href)
            if not url or url in seen:
                continue
            seen.add(url)
            links.append({"label": label, "url": url})
        return links

    @staticmethod
    def _pick_current_document(links: list[dict[str, str]]) -> dict[str, str] | None:
        priority = ("Slip Law", "Final Reading", "Engrossed", "Introduced")
        for label in priority:
            for link in links:
                if clean_text(link.get("label")).lower() == label.lower():
                    return link
        return links[-1] if links else None

    @staticmethod
    def _document_by_label(links: list[dict[str, str]], label: str) -> str | None:
        normalized = label.lower()
        for link in links:
            if clean_text(link.get("label")).lower() == normalized:
                return link.get("url")
        return None

    @staticmethod
    def _actions_path(soup: BeautifulSoup, detail_path: str, item: dict[str, Any] | None) -> str:
        action_link = soup.select_one("a[href*='/bills/view_actions.php']")
        if action_link is not None:
            return absolute_url(detail_path, action_link.get("href")) or detail_path
        match = NEBRASKA_DETAIL_PATH_PATTERN.search(str(detail_path))
        if match is not None:
            return absolute_url(detail_path, f"/bills/view_actions.php?DocumentID={match.group(1)}") or detail_path
        document_id = clean_text(str((item or {}).get("documentId") or ""))
        if document_id:
            return absolute_url(detail_path, f"/bills/view_actions.php?DocumentID={document_id}") or detail_path
        raise ValueError("Nebraska action history path could not be determined")

    def _fetch_actions(self, path: str) -> list[dict[str, str]]:
        response = self._get(path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        if table is None:
            return []
        parsed: list[dict[str, str]] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            parsed.append(
                {
                    "location": "",
                    "statusDate": parse_nebraska_date(cells[0].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )
        parsed.reverse()
        return parsed
