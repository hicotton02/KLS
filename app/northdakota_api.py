from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


NORTH_DAKOTA_BILL_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HR|SR)\s+(\d{4})$", re.IGNORECASE)


def normalize_north_dakota_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = NORTH_DAKOTA_BILL_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1)}{match.group(2)}"


def north_dakota_assembly_slug(year: int) -> str:
    session_start = year if year % 2 == 1 else year - 1
    assembly_number = ((session_start - 1889) // 2) + 1
    return f"{assembly_number}-{session_start}"


def parse_north_dakota_date(value: str | None, year: int) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    match = re.fullmatch(r"(\d{2})/(\d{2})", raw)
    if match is not None:
        return f"{year:04d}-{match.group(1)}-{match.group(2)}"
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class NorthDakotaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.north_dakota_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        assembly_slug = north_dakota_assembly_slug(year)
        response = self.client.get(f"/assembly/{assembly_slug}/regular/documents/bill-download.html")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in soup.find_all("a", href=True, class_="bold"):
            bill_label = " ".join(link.get_text(" ", strip=True).split())
            bill_num = normalize_north_dakota_bill_number(bill_label)
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)

            card = link.find_parent("ul", class_=re.compile(r"\blist-group\b"))
            versions = self._version_entries(card, str(response.url))
            current_version = self._pick_current_version(versions)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": bill_label,
                    "billTitle": bill_label,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(str(response.url), link.get("href")),
                    "currentVersionPath": current_version.get("documentUrl") if current_version else None,
                    "currentVersionFingerprint": "|".join(entry.get("documentUrl", "") for entry in versions if entry.get("documentUrl")),
                    "versionEntries": versions,
                    "sourceYear": year,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        source_item = item or {}
        year = int(source_item.get("sourceYear") or 0)
        if year <= 0:
            year = north_dakota_assembly_start_year_from_path(detail_path)

        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        content = soup.find("div", class_="tab-content")
        if content is None:
            raise ValueError("North Dakota overview page content was not found")

        title = self._section_value(content, "Title")
        sponsor = self._section_value(content, "Sponsors")
        measure_status_lines = self._measure_status_lines(content)
        actions_url = self._actions_url(soup, str(response.url))
        actions = self._fetch_actions(actions_url, year)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": ""}
        last_action = clean_text(str(latest_action.get("statusMessage") or ""))
        last_action_date = clean_text(str(latest_action.get("statusDate") or ""))
        signed_date = ""
        for action in reversed(actions):
            message = clean_text(str(action.get("statusMessage") or ""))
            if "signed by governor" in message.lower():
                signed_date = clean_text(str(action.get("statusDate") or ""))
                break
        status_label = self._page_status(content)
        amendments = self._amendments_from_versions(source_item.get("versionEntries") or [])
        current_version = self._pick_current_version(source_item.get("versionEntries") or [])
        history_pdf = self._history_pdf_url(content, str(response.url))

        return {
            "bill": str(source_item.get("billNum") or normalize_north_dakota_bill_number(self._page_heading(soup))),
            "billType": re.match(r"[A-Z]+", str(source_item.get("billNum") or "")).group(0)
            if re.match(r"[A-Z]+", str(source_item.get("billNum") or ""))
            else "",
            "catchTitle": title or str(source_item.get("billNum") or ""),
            "sponsor": sponsor.removeprefix("Introduced by ").strip(),
            "billTitle": title or str(source_item.get("billNum") or ""),
            "billStatus": first_non_empty(status_label, last_action),
            "lastAction": first_non_empty(last_action, status_label),
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": clean_text(str(current_version.get("versionCode") or "")) if current_version else "",
            "sponsorStringHouse": sponsor if str(source_item.get("billNum") or "").startswith("H") else None,
            "sponsorStringSenate": sponsor if str(source_item.get("billNum") or "").startswith("S") else None,
            "introduced": self._document_by_kind(source_item.get("versionEntries") or [], "I"),
            "digest": history_pdf or str(response.url),
            "summary": str(response.url),
            "currentVersionPath": current_version.get("documentUrl") if current_version else None,
            "currentVersionFingerprint": first_non_empty(
                clean_text(str(source_item.get("currentVersionFingerprint") or "")),
                history_pdf,
                str(response.url),
            ),
            "summaryHTML": self._paragraph_html(title or str(source_item.get("billNum") or "")),
            "digestHTML": self._paragraph_html(" ".join(measure_status_lines)),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _version_entries(card: Tag | None, base_url: str) -> list[dict[str, str]]:
        if card is None:
            return []
        entries: list[dict[str, str]] = []
        for row in card.find_all("li", class_=re.compile(r"\blist-group-item\b")):
            title = clean_text(str(row.get("title") or ""))
            anchor = row.find("a", href=True)
            code = ""
            kind = ""
            document_url = ""
            if anchor is not None:
                code = clean_text(anchor.get_text(" ", strip=True))
                badge = anchor.find("span")
                kind = clean_text(badge.get_text(" ", strip=True)) if badge is not None else ""
                if kind:
                    code = code.replace(kind, "").strip()
                document_url = absolute_url(base_url, anchor.get("href")) or ""
            else:
                code = clean_text(row.get_text(" ", strip=True))
            if not code:
                continue
            entries.append(
                {
                    "versionCode": code,
                    "kind": kind,
                    "documentUrl": document_url,
                    "title": title,
                }
            )
        return entries

    @staticmethod
    def _pick_current_version(entries: list[dict[str, str]]) -> dict[str, str] | None:
        if not entries:
            return None
        for kind in ("E", "I", ""):
            for entry in reversed(entries):
                if clean_text(entry.get("kind")) == kind and entry.get("documentUrl"):
                    return entry
        for entry in reversed(entries):
            if entry.get("documentUrl"):
                return entry
        return entries[-1]

    @staticmethod
    def _document_by_kind(entries: list[dict[str, str]], kind: str) -> str | None:
        normalized_kind = clean_text(kind)
        for entry in entries:
            if clean_text(entry.get("kind")) == normalized_kind and entry.get("documentUrl"):
                return str(entry.get("documentUrl"))
        return None

    @staticmethod
    def _amendments_from_versions(entries: list[dict[str, str]]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for order, entry in enumerate(entries, start=1):
            if clean_text(entry.get("kind")) != "A":
                continue
            amendment_number = clean_text(str(entry.get("versionCode") or ""))
            if not amendment_number:
                continue
            payloads.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": "",
                    "order": order,
                    "sequence": order,
                    "status": clean_text(str(entry.get("title") or "Amendment")),
                    "sponsor": "",
                    "documentUrl": clean_text(str(entry.get("documentUrl") or "")),
                }
            )
        return payloads

    @staticmethod
    def _page_heading(soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        return clean_text(heading.get_text(" ", strip=True)) if heading is not None else ""

    @staticmethod
    def _page_status(content: Tag) -> str:
        heading = content.find_previous("h1")
        if heading is None:
            return ""
        raw = clean_text(heading.get_text(" ", strip=True))
        match = re.search(r"\b(HB|SB|HCR|SCR|HR|SR)\s+\d{4}\s+(.+)$", raw, re.IGNORECASE)
        if match is None:
            return ""
        return clean_text(match.group(2))

    @staticmethod
    def _section_value(content: Tag, heading_text: str) -> str:
        heading = content.find("h5", string=lambda value: isinstance(value, str) and heading_text in value)
        if heading is None:
            return ""
        paragraph = heading.find_next("p")
        if paragraph is None:
            return ""
        return " ".join(paragraph.get_text(" ", strip=True).split())

    @staticmethod
    def _measure_status_lines(content: Tag) -> list[str]:
        box = content.find("div", class_="line_box")
        if box is None:
            return []
        lines: list[str] = []
        for circle in box.find_all("div", class_=re.compile(r"\btext_circle\b")):
            text = " ".join(circle.get_text(" ", strip=True).split())
            if text:
                lines.append(text)
        return lines

    @staticmethod
    def _actions_url(soup: BeautifulSoup, detail_url: str) -> str:
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            if "/bill-actions/" in href:
                return absolute_url(detail_url, href) or detail_url
        raise ValueError("North Dakota actions URL could not be determined")

    @staticmethod
    def _history_pdf_url(content: Tag, detail_url: str) -> str:
        for anchor in content.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
            href = str(anchor.get("href") or "")
            if "view history" in text:
                return absolute_url(detail_url, href) or href
        return ""

    def _fetch_actions(self, url: str, year: int) -> list[dict[str, str]]:
        response = self.client.get(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        tables = soup.find_all("table")
        if len(tables) < 2:
            return []
        rows: list[dict[str, str]] = []
        for row in tables[1].find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            rows.append(
                {
                    "statusDate": parse_north_dakota_date(cells[0].get_text(" ", strip=True), year),
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[2].get_text(" ", strip=True)),
                }
            )
        return rows

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = clean_text(value)
        if not text:
            return ""
        return f"<p>{text}</p>"


def north_dakota_assembly_start_year_from_path(path: str) -> int:
    match = re.search(r"/assembly/\d+-(\d{4})/", str(path))
    if match is None:
        return 0
    return int(match.group(1))
