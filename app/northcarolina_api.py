from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


NORTH_CAROLINA_BILL_NUMBER_PATTERN = re.compile(r"^(H|S)\d+$", re.IGNORECASE)


def parse_north_carolina_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    normalized = (
        raw.replace("a.m.", "AM")
        .replace("p.m.", "PM")
        .replace("a.m", "AM")
        .replace("p.m", "PM")
    )
    for fmt in (
        "%m/%d/%Y",
        "%m/%d/%Y %I:%M %p",
        "%b %d %Y",
        "%B %d %Y",
        "%b %d, %Y",
        "%B %d, %Y",
    ):
        try:
            return datetime.strptime(normalized, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_north_carolina_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if NORTH_CAROLINA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class NorthCarolinaApiClient:
    index_requires_detail_fetch = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.site_client = httpx.Client(
            base_url=self.settings.north_carolina_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.webservices_client = httpx.Client(
            base_url=self.settings.north_carolina_webservices_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.site_client.close()
        self.webservices_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.webservices_client.get(f"/AllBills/{year}")
        response.raise_for_status()

        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in response.json():
            chamber = clean_text(row.get("chamber")).upper()
            number = clean_text(str(row.get("billNumber") or ""))
            if chamber not in {"H", "S"} or not number.isdigit():
                continue
            bill_num = normalize_north_carolina_bill_number(f"{chamber}{int(number)}")
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": chamber,
                    "catchTitle": bill_num,
                    "billTitle": bill_num,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(self.settings.north_carolina_site_base, f"/BillLookUp/{year}/{bill_num}"),
                    "summaryPath": absolute_url(
                        self.settings.north_carolina_site_base,
                        f"/Legislation/Bills/Summaries/{year}/{bill_num}",
                    ),
                    "digestPath": absolute_url(
                        self.settings.north_carolina_webservices_base,
                        f"/BillDigests/{year}/{bill_num}",
                    ),
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.site_client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        main = soup.find("main") or soup

        bill_num = self._bill_number(str(response.url), item)
        catch_title = self._catch_title(main, bill_num)
        misc_nodes = self._misc_info_nodes(main)
        sponsor = self._sponsor_names(misc_nodes.get("Sponsors"))

        history_rows = self._history_rows(main)
        latest_action = history_rows[0] if history_rows else {"statusDate": "", "statusMessage": "", "location": ""}

        summary_url = first_non_empty(
            (item or {}).get("summaryPath"),
            self._named_link_url(main, "View Available Bill Summaries"),
        )
        digest_url = first_non_empty(
            (item or {}).get("digestPath"),
            self._named_link_url(main, "View Bill Digest"),
        )
        version_rows = self._version_rows(main)
        summary_rows = self._summary_rows(summary_url)
        digest_entries = self._digest_entries(digest_url)

        digest_text = " ".join(entry["text"] for entry in digest_entries if entry["text"])
        last_action = first_non_empty(
            latest_action.get("statusMessage"),
            clean_text(misc_nodes.get("Last Action").get_text(" ", strip=True)) if misc_nodes.get("Last Action") else "",
        )
        last_action_date = first_non_empty(
            latest_action.get("statusDate"),
            self._action_date_from_text(last_action),
            self._latest_summary_date(summary_rows),
        )

        chapter_no = self._chapter_from_text(digest_text)
        signed_date = first_non_empty(
            self._enacted_date_from_text(digest_text),
            self._signed_date_from_actions(history_rows),
        )
        effective_date = self._effective_date_from_text(digest_text)

        amendments = [
            {
                "amendmentNumber": row["summary_code"],
                "adoptedDate": row["last_updated"],
                "documentUrl": row["url"],
                "summaryText": row["description"],
                "source": "North Carolina bill summaries",
            }
            for row in summary_rows
            if row["summary_code"] and row["description"]
        ]

        current_version_path = str(version_rows[-1]["url"]) if version_rows else ""
        introduced_path = str(version_rows[0]["url"]) if version_rows else current_version_path

        return {
            "bill": bill_num,
            "billType": bill_num[:1],
            "catchTitle": catch_title or bill_num,
            "sponsor": sponsor,
            "billTitle": catch_title or bill_num,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter_no,
            "enrolledNumber": version_rows[-1]["label"] if version_rows else "",
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path or None,
            "digest": digest_url or None,
            "summary": summary_url or None,
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    version_rows[-1]["label"] if version_rows else "",
                    last_action,
                    last_action_date,
                    chapter_no,
                    str(len(summary_rows)),
                    str(len(digest_entries)),
                )
                if part
            ),
            "summaryHTML": self._summary_html(summary_rows, catch_title or bill_num),
            "digestHTML": self._digest_html(digest_entries),
            "currentBillHTML": "",
            "billActions": history_rows,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        if url and "webservices.ncleg.gov" in url:
            return fetch_document_text(self.webservices_client, url)
        return fetch_document_text(self.site_client, url)

    @staticmethod
    def _bill_number(detail_url: str, item: dict[str, Any] | None = None) -> str:
        match = re.search(r"/BillLookUp/\d+/([A-Z]\d+)$", detail_url, re.IGNORECASE)
        if match:
            bill_num = normalize_north_carolina_bill_number(match.group(1))
            if bill_num:
                return bill_num
        fallback = normalize_north_carolina_bill_number((item or {}).get("billNum"))
        if fallback:
            return fallback
        raise ValueError(f"North Carolina bill number could not be parsed from {detail_url}")

    @staticmethod
    def _catch_title(main: Tag, bill_num: str) -> str:
        session_label = main.select_one("div.titleSub")
        if session_label is not None:
            row = session_label.find_parent("div", class_="row")
            if isinstance(row, Tag):
                for anchor in row.find_all("a", href=True):
                    text = clean_text(anchor.get_text(" ", strip=True))
                    if text and text.upper().replace(" ", "") != bill_num:
                        return text
        for anchor in main.find_all("a", href=True):
            href = anchor.get("href") or ""
            text = clean_text(anchor.get_text(" ", strip=True))
            if not text or text.upper().replace(" ", "") == bill_num:
                continue
            if "/BillLookUp/" in href and href.rstrip("/").upper().endswith(f"/{bill_num}"):
                return text
        title = main.find("title")
        title_text = clean_text(title.get_text(" ", strip=True)) if title else ""
        if title_text:
            return title_text
        return bill_num

    @staticmethod
    def _misc_info_nodes(main: Tag) -> dict[str, Tag]:
        nodes: dict[str, Tag] = {}
        for label_div in main.select("div.misc-info-label"):
            label = clean_text(label_div.get_text(" ", strip=True)).rstrip(":")
            value_div = label_div.find_next_sibling("div")
            if label and isinstance(value_div, Tag):
                nodes[label] = value_div
        return nodes

    @staticmethod
    def _sponsor_names(node: Tag | None) -> str:
        if node is None:
            return ""
        names = [clean_text(anchor.get_text(" ", strip=True)) for anchor in node.find_all("a", href=True)]
        names = [name for name in names if name]
        return ", ".join(names) if names else clean_text(node.get_text(" ", strip=True))

    def _history_rows(self, main: Tag) -> list[dict[str, str]]:
        header = next(
            (
                tag
                for tag in main.select("div.card-header")
                if clean_text(tag.get_text(" ", strip=True)).startswith("History")
            ),
            None,
        )
        if header is None:
            return []
        card = header.find_parent("div", class_="card")
        if card is None:
            return []
        rows: list[dict[str, str]] = []
        body = card.select_one("div.card-body")
        if body is None:
            return rows
        for row in body.select("div.row.avoid-break-inside"):
            values = self._labeled_row_values(row)
            action = clean_text(values.get("Action"))
            if not action:
                continue
            rows.append(
                {
                    "statusDate": parse_north_carolina_date(values.get("Date")),
                    "location": clean_text(values.get("Chamber")),
                    "statusMessage": action,
                }
            )
        return rows

    @staticmethod
    def _labeled_row_values(row: Tag) -> dict[str, str]:
        values: dict[str, str] = {}
        children = row.find_all("div", recursive=False)
        for index, child in enumerate(children[:-1]):
            classes = child.get("class") or []
            if "font-weight-bold" not in classes:
                continue
            label = clean_text(child.get_text(" ", strip=True)).rstrip(":")
            values[label] = clean_text(children[index + 1].get_text(" ", strip=True))
        return values

    @staticmethod
    def _named_link_url(main: Tag, link_text: str) -> str:
        link = main.find("a", string=lambda value: isinstance(value, str) and clean_text(value) == link_text)
        return clean_text(link.get("href")) if isinstance(link, Tag) else ""

    def _version_rows(self, main: Tag) -> list[dict[str, str]]:
        summary_header = next(
            (
                tag
                for tag in main.select("div.card-header")
                if "View Available Bill Summaries" in clean_text(tag.get_text(" ", strip=True))
            ),
            None,
        )
        if summary_header is None:
            return []
        card = summary_header.find_parent("div", class_="card")
        if card is None:
            return []
        body = card.select_one("div.card-body")
        if body is None:
            return []
        rows: list[dict[str, str]] = []
        for row in body.find_all("div", class_="row", recursive=False):
            anchors = row.find_all("a", href=True)
            if not anchors:
                continue
            display_anchor = next(
                (anchor for anchor in anchors if "sr-only" not in (anchor.get("class") or [])),
                anchors[0],
            )
            label = clean_text(display_anchor.get_text(" ", strip=True))
            url = absolute_url(self.settings.north_carolina_site_base, display_anchor.get("href")) or ""
            if not label or not url:
                continue
            rows.append({"label": label, "url": url})
        return rows

    def _summary_rows(self, summary_url: str | None) -> list[dict[str, str]]:
        if not summary_url:
            return []
        try:
            response = self.site_client.get(summary_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(response.text, "html.parser")

        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for row in soup.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            summary_code = clean_text(cells[0].get_text(" ", strip=True))
            if not summary_code or summary_code in seen:
                continue
            seen.add(summary_code)
            link = cells[0].find("a", href=True)
            rows.append(
                {
                    "summary_code": summary_code,
                    "url": absolute_url(self.settings.north_carolina_site_base, link.get("href")) if link else "",
                    "description": clean_text(cells[2].get_text(" ", strip=True)),
                    "last_updated": parse_north_carolina_date(cells[3].get_text(" ", strip=True)),
                }
            )
        return rows

    def _digest_entries(self, digest_url: str | None) -> list[dict[str, str]]:
        if not digest_url:
            return []
        try:
            response = self.webservices_client.get(digest_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return []
        soup = BeautifulSoup(response.text, "html.parser")

        entries: list[dict[str, str]] = []
        for row in soup.select("div.view-content div.item-list ul > li"):
            paragraphs = [clean_text(paragraph.get_text(" ", strip=True)) for paragraph in row.find_all("p")]
            paragraphs = [paragraph for paragraph in paragraphs if paragraph]
            text = " ".join(paragraphs) if paragraphs else clean_text(row.get_text(" ", strip=True))
            if not text:
                continue
            date_match = re.search(r"Summary date:\s*([A-Za-z]{3,9}\s+\d{1,2}\s+\d{4})", clean_text(row.get_text(" ", strip=True)))
            link = row.find("a", href=True)
            entries.append(
                {
                    "summaryDate": parse_north_carolina_date(date_match.group(1)) if date_match else "",
                    "text": text,
                    "url": absolute_url(self.settings.north_carolina_webservices_base, link.get("href")) if link else "",
                }
            )
        return entries

    @staticmethod
    def _summary_html(summary_rows: list[dict[str, str]], fallback_title: str) -> str:
        if not summary_rows:
            return f"<p>{html.escape(fallback_title)}</p>" if fallback_title else ""
        return "".join(
            f"<p><strong>{html.escape(row['summary_code'])}</strong> ({html.escape(row['last_updated'] or 'undated')}): "
            f"{html.escape(row['description'])}</p>"
            for row in summary_rows
            if row["description"]
        )

    @staticmethod
    def _digest_html(digest_entries: list[dict[str, str]]) -> str:
        if not digest_entries:
            return ""
        return "".join(
            f"<p><strong>{html.escape(entry['summaryDate'] or 'Summary')}</strong>: {html.escape(entry['text'])}</p>"
            for entry in digest_entries[:6]
        )

    @staticmethod
    def _latest_summary_date(summary_rows: list[dict[str, str]]) -> str:
        for row in reversed(summary_rows):
            if row["last_updated"]:
                return row["last_updated"]
        return ""

    @staticmethod
    def _action_date_from_text(last_action: str) -> str:
        match = re.search(r"\bon\s+(\d{1,2}/\d{1,2}/\d{4})\b", clean_text(last_action), re.IGNORECASE)
        return parse_north_carolina_date(match.group(1)) if match else ""

    @staticmethod
    def _chapter_from_text(text: str) -> str:
        match = re.search(r"\bSL\s+\d{4}-(\d+)\b", text, re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    @staticmethod
    def _enacted_date_from_text(text: str) -> str:
        match = re.search(r"\bEnacted\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b", text, re.IGNORECASE)
        return parse_north_carolina_date(match.group(1)) if match else ""

    @staticmethod
    def _effective_date_from_text(text: str) -> str:
        match = re.search(r"\bEffective\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})\b", text, re.IGNORECASE)
        return parse_north_carolina_date(match.group(1)) if match else ""

    @staticmethod
    def _signed_date_from_actions(actions: list[dict[str, str]]) -> str:
        for action in actions:
            action_text = clean_text(action.get("statusMessage"))
            if "ratified" in action_text.lower() or "signed" in action_text.lower():
                return clean_text(action.get("statusDate"))
        return ""
