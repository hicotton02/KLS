from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, html_to_text


VERMONT_BILL_NUMBER_PATTERN = re.compile(r"^(H|S|PR)\.?\s*(\d+)$", re.IGNORECASE)
VERMONT_DETAIL_STATUS_PATH_PATTERN = re.compile(r"bill/loadBillDetailedStatus/\d{4}/(\d+)", re.IGNORECASE)


def parse_vermont_date(value: str | None) -> str:
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


def normalize_vermont_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = VERMONT_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1).upper()}.{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)\.(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _vermont_session_year(year: int) -> int:
    return year if year % 2 == 0 else year + 1


class VermontApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.vermont_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            verify=False,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_year = _vermont_session_year(year)
        response = self.client.get(f"/bill/loadBillsIntroduced/{session_year}")
        response.raise_for_status()
        payload = response.json()

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in payload.get("data", []):
            if str(row.get("year") or "").strip() != str(year):
                continue
            bill_num = normalize_vermont_bill_number(row.get("BillNumber"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            title = clean_text(str(row.get("Title") or row.get("Title1") or "")) or bill_num
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num.split(".", 1)[0],
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": parse_vermont_date(str(row.get("SortMeetingDate") or "")),
                    "detailPath": absolute_url(
                        self.settings.vermont_site_base,
                        f"/bill/status/{session_year}/{bill_num}",
                    ),
                    "actNo": clean_text(str(row.get("ActNo") or "")),
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(soup, item)
        bill_title = self._bill_title(soup) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num
        summary = self._summary_fields(soup)
        sponsors = self._sponsors(summary.get("Sponsor(s)") or "")
        detailed_status_path = self._detailed_status_path(response.text)
        actions = self._fetch_detailed_status(detailed_status_path)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": "", "location": ""}

        text_links = self._bill_text_links(soup, str(response.url))
        introduced_path = text_links[0]["url"] if text_links else None
        official_links = [link["url"] for link in text_links if link["kind"] != "unofficial"]
        current_version_path = official_links[-1] if official_links else (text_links[-1]["url"] if text_links else None)
        fingerprint_parts = [link["url"] for link in text_links if link.get("url")]

        act_number = self._act_number(soup, item)
        last_action = clean_text(str(latest_action.get("statusMessage") or "")) or clean_text(
            str(summary.get("Last Recorded Action") or "")
        )
        last_action_date = parse_vermont_date(str(latest_action.get("statusDate") or ""))
        signed_date = (
            last_action_date
            if act_number and ("governor" in last_action.lower() or "approved" in last_action.lower() or "signed" in last_action.lower())
            else ""
        )

        return {
            "bill": bill_num,
            "billType": bill_num.split(".", 1)[0],
            "catchTitle": bill_title,
            "sponsor": ", ".join(sponsors),
            "billTitle": bill_title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": act_number,
            "enrolledNumber": f"Act {act_number}" if act_number else "",
            "sponsorStringHouse": ", ".join(sponsors) if bill_num.startswith("H.") else None,
            "sponsorStringSenate": ", ".join(sponsors) if bill_num.startswith("S.") else None,
            "introduced": introduced_path,
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(fingerprint_parts),
            "summaryHTML": f"<p>{bill_title}</p>" if bill_title else "",
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _bill_number(soup: BeautifulSoup, item: dict[str, Any] | None) -> str:
        title = soup.find("h1")
        if title is not None:
            match = re.search(r"\b([A-Z]{1,2}\.\d+)\b", title.get_text(" ", strip=True), re.IGNORECASE)
            if match is not None:
                parsed = normalize_vermont_bill_number(match.group(1))
                if parsed:
                    return parsed
        fallback = normalize_vermont_bill_number((item or {}).get("billNum"))
        if fallback:
            return fallback
        raise ValueError("Vermont bill number could not be parsed from the bill page")

    @staticmethod
    def _bill_title(soup: BeautifulSoup) -> str:
        title_block = soup.select_one(".bill-title")
        if title_block is None:
            return ""
        raw = clean_text(title_block.get_text(" ", strip=True))
        raw = re.sub(r"^[A-Z]{1,2}\.\d+\s*(?:\((?:Act|Resolve)\s+\d+\))?\s*", "", raw, flags=re.IGNORECASE).strip()
        return raw

    @staticmethod
    def _summary_fields(soup: BeautifulSoup) -> dict[str, str]:
        summary: dict[str, str] = {}
        block = soup.select_one("dl.summary-table")
        if block is None:
            return summary
        current_label = ""
        for child in block.children:
            if isinstance(child, Tag):
                if child.name == "dt":
                    current_label = clean_text(child.get_text(" ", strip=True))
                elif child.name == "dd" and current_label:
                    summary[current_label] = clean_text(child.get_text(" ", strip=True))
        return summary

    @staticmethod
    def _sponsors(raw_value: str) -> list[str]:
        if not raw_value:
            return []
        parts = [clean_text(part) for part in raw_value.split("\n")]
        sponsors = [part for part in parts if part]
        return sponsors or [clean_text(raw_value)]

    @staticmethod
    def _detailed_status_path(html_text: str) -> str:
        match = VERMONT_DETAIL_STATUS_PATH_PATTERN.search(html_text)
        if match is None:
            raise ValueError("Vermont bill detailed-status endpoint was not found")
        return match.group(0)

    def _fetch_detailed_status(self, path: str) -> list[dict[str, str]]:
        response = self.client.get(f"/{path.lstrip('/')}")
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", [])
        parsed: list[dict[str, str]] = []
        for row in sorted(rows, key=lambda entry: int(entry.get("Sequence") or 0)):
            status_message = " ".join(
                clean_text(html_to_text(str(row.get("FullStatus") or row.get("FullStatus1") or ""))).split()
            )
            parsed.append(
                {
                    "location": clean_text(str(row.get("Location") or "")),
                    "statusDate": parse_vermont_date(str(row.get("StatusDate") or "")),
                    "statusMessage": status_message,
                }
            )
        return parsed

    @staticmethod
    def _bill_text_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
        heading = soup.find("h5", string=lambda value: isinstance(value, str) and "Bill/Resolution Text" in value)
        if heading is None:
            return []
        container = heading.find_next("ul", class_="bill-path")
        if container is None:
            return []

        links: list[dict[str, str]] = []
        for item in container.find_all("li", recursive=False):
            link_text = clean_text(item.get_text(" ", strip=True))
            anchors = item.find_all("a", href=True)
            if not anchors:
                continue
            for anchor in anchors:
                label = clean_text(anchor.get_text(" ", strip=True))
                url = absolute_url(base_url, anchor.get("href"))
                if not url:
                    continue
                kind = "unofficial" if label.lower() == "unofficial" else "official"
                if not label or label.lower() in {"official", "unofficial"}:
                    label = link_text
                links.append({"label": label, "url": url, "kind": kind})
        return links

    @staticmethod
    def _act_number(soup: BeautifulSoup, item: dict[str, Any] | None) -> str:
        title = soup.find("h1")
        if title is not None:
            match = re.search(r"\((?:Act|Resolve)\s+(\d+)\)", title.get_text(" ", strip=True), re.IGNORECASE)
            if match is not None:
                return match.group(1)
        return clean_text(str((item or {}).get("actNo") or ""))
