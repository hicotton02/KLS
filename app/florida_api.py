from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url
from app.settings import Settings
from app.text_utils import clean_text, html_to_text, pdf_bytes_to_text


FLORIDA_BILL_LABEL_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)\s+(?P<number>\d+)$")
FLORIDA_BILL_HEADING_PATTERN = re.compile(r"^(?P<label>[A-Z]+\s+\d+):\s*(?P<title>.+)$")


def parse_florida_date(value: str | None) -> str:
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


def normalize_florida_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).upper()
    match = FLORIDA_BILL_LABEL_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group('prefix')}{int(match.group('number')):04d}"


def _bill_type(value: str | None) -> str:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(value or "").strip().upper())
    if match is None:
        return str(value or "").strip().upper()
    return match.group(1)


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _paragraph_html(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    return f"<p>{text}</p>"


class FloridaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.florida_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._ranges_by_session: dict[str, list[str]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = str(year)
        bill_ranges = self._bill_ranges(session_code)
        items_by_bill: dict[str, dict[str, Any]] = {}

        for chamber in ("Senate", "House"):
            for bill_range in bill_ranges:
                response = self._get(
                    f"/Session/Bills/{session_code}",
                    params={
                        "chamber": chamber,
                        "searchOnlyCurrentVersion": "True",
                        "isIncludeAmendments": "False",
                        "isFirstReference": "False",
                        "billRange": bill_range,
                        "pageNumber": "1",
                    },
                )
                response.raise_for_status()
                for item in self._parse_bill_list_page(response.text, str(response.url)):
                    items_by_bill[item["billNum"]] = item

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self._get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num, title = self._heading_bill(soup)
        bill_type = _bill_type(bill_num)
        sponsor = self._sponsor(soup)
        summary = self._summary(soup, title)
        snapshot = self._snapshot(soup)
        actions = self._bill_history_rows(soup)
        versions = self._bill_text_rows(soup, str(response.url))
        amendments = self._amendment_rows(soup, bill_num, str(response.url))
        analyses = self._analysis_rows(soup, str(response.url))

        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": "", "location": ""}
        current_version = versions[-1] if versions else {}
        introduced_version = versions[0] if versions else {}
        current_version_path = str(
            current_version.get("html_url") or current_version.get("pdf_url") or snapshot.get("current_text_html") or ""
        ).strip()
        fingerprint_parts: list[str] = []
        for version in versions:
            fingerprint_parts.extend(
                [
                    str(version.get("label") or ""),
                    str(version.get("posted") or ""),
                    str(version.get("html_url") or ""),
                    str(version.get("pdf_url") or ""),
                ]
            )
        current_version_fingerprint = "|".join(part for part in fingerprint_parts if part)
        signed_date = self._signed_date(actions)
        effective_date = str(snapshot.get("effective_date") or "")
        last_action_date = str(snapshot.get("last_action_date") or latest_action.get("statusDate") or "")
        last_action = str(snapshot.get("last_action") or latest_action.get("statusMessage") or "")
        digest_url = str((analyses[:1] or [{}])[0].get("document_url") or "").strip() or None

        return {
            "bill": bill_num,
            "billType": bill_type,
            "catchTitle": title or bill_num,
            "sponsor": sponsor,
            "billTitle": title or bill_num,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": "",
            "enrolledNumber": str(current_version.get("label") or ""),
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": str(introduced_version.get("html_url") or introduced_version.get("pdf_url") or "") or None,
            "digest": digest_url,
            "summary": str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": _paragraph_html(summary),
            "digestHTML": _paragraph_html(str(snapshot.get("measure_type") or "")),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        if not url:
            return ""
        response = self._get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if str(response.url).lower().endswith(".pdf") or "pdf" in content_type:
            return pdf_bytes_to_text(response.content)
        return html_to_text(response.text)

    def _bill_ranges(self, session_code: str) -> list[str]:
        cached = self._ranges_by_session.get(session_code)
        if cached is not None:
            return cached

        response = self._get(f"/Session/Bills/{session_code}")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        bill_range_select = soup.find("select", id="bill-range")
        if bill_range_select is None:
            raise ValueError(f"Florida bill range selector was not found for session {session_code}")

        ranges = [
            clean_text(option.get("value"))
            for option in bill_range_select.find_all("option")
            if clean_text(option.get("value"))
        ]
        if not ranges:
            raise ValueError(f"Florida bill ranges were not found for session {session_code}")
        self._ranges_by_session[session_code] = ranges
        return ranges

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            response = self.client.get(url, **kwargs)
            if response.status_code != 429 or attempt >= 5:
                return response
            retry_after = clean_text(response.headers.get("retry-after"))
            try:
                delay = float(retry_after)
            except ValueError:
                delay = min(12.0, 1.0 * (2**attempt))
            time.sleep(max(1.0, delay))
            attempt += 1

    def _parse_bill_list_page(self, html_text: str, page_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        table = soup.select_one("table.tbl")
        if table is None:
            return []

        items: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            link = row.select_one("th a[href], td a[href]")
            if link is None:
                continue
            bill_num = normalize_florida_bill_number(link.get_text(" ", strip=True))
            if not bill_num:
                continue
            cells = row.find_all("td", recursive=False)
            title = clean_text(cells[0].get_text(" ", strip=True) if len(cells) > 0 else "") or bill_num
            sponsor = clean_text(cells[1].get_text(" ", strip=True) if len(cells) > 1 else "")
            last_action_cell = clean_text(cells[2].get_text(" ", strip=True) if len(cells) > 2 else "")
            last_action_date, last_action = self._split_last_action(last_action_cell)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": _bill_type(bill_num),
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": last_action,
                    "lastAction": last_action,
                    "lastActionDate": last_action_date,
                    "detailPath": absolute_url(page_url, link.get("href")),
                }
            )
        return items

    @staticmethod
    def _heading_bill(soup: BeautifulSoup) -> tuple[str, str]:
        heading = soup.find("h2")
        heading_text = clean_text(heading.get_text(" ", strip=True) if heading else "")
        match = FLORIDA_BILL_HEADING_PATTERN.fullmatch(heading_text)
        if match is not None:
            bill_num = normalize_florida_bill_number(match.group("label"))
            if bill_num:
                return (bill_num, clean_text(match.group("title")))
        title_tag = soup.find("title")
        title_text = clean_text(title_tag.get_text(" ", strip=True) if title_tag else "")
        match = re.search(r"([A-Z]+\s+\d+)", title_text)
        bill_num = normalize_florida_bill_number(match.group(1) if match else "")
        if bill_num:
            return (bill_num, heading_text or bill_num)
        raise ValueError("Florida bill number could not be parsed from the bill page")

    @staticmethod
    def _sponsor(soup: BeautifulSoup) -> str:
        heading = soup.find("h2")
        if heading is None:
            return ""
        summary_type = heading.find_next_sibling("p")
        if summary_type is None:
            return ""
        sponsor_link = summary_type.find("a")
        if sponsor_link is not None:
            return clean_text(sponsor_link.get_text(" ", strip=True))
        text = clean_text(summary_type.get_text(" ", strip=True))
        if " by " in text.lower():
            parts = re.split(r"\bby\b", text, maxsplit=1, flags=re.IGNORECASE)
            if len(parts) == 2:
                return clean_text(parts[1])
        return ""

    @staticmethod
    def _summary(soup: BeautifulSoup, fallback_title: str) -> str:
        heading = soup.find("h2")
        if heading is None:
            return fallback_title
        summary_type = heading.find_next_sibling("p")
        summary_body = summary_type.find_next_sibling("p") if summary_type is not None else None
        if summary_body is None:
            return fallback_title
        return clean_text(summary_body.get_text(" ", strip=True)) or fallback_title

    @staticmethod
    def _snapshot(soup: BeautifulSoup) -> dict[str, str]:
        snapshot = soup.find("div", id="snapshot")
        if snapshot is None:
            return {
                "measure_type": "",
                "effective_date": "",
                "last_action_date": "",
                "last_action": "",
                "current_text_html": "",
                "current_text_pdf": "",
            }

        heading = soup.find("h2")
        summary_type = heading.find_next_sibling("p") if heading is not None else None
        measure_type = ""
        if summary_type is not None:
            text = clean_text(summary_type.get_text(" ", strip=True))
            if " by " in text.lower():
                measure_type = clean_text(re.split(r"\bby\b", text, maxsplit=1, flags=re.IGNORECASE)[0])
            else:
                measure_type = text

        left_column = snapshot.find("div", class_=lambda value: isinstance(value, list) and "grid-60" in value)
        current_text_html = ""
        current_text_pdf = ""
        if left_column is not None:
            for link in left_column.find_all("a", href=True):
                label = clean_text(link.get_text(" ", strip=True)).lower()
                absolute = absolute_url("https://www.flsenate.gov", link.get("href")) or ""
                if label == "web page":
                    current_text_html = absolute
                elif label == "pdf":
                    current_text_pdf = absolute

        effective_date = parse_florida_date(FloridaApiClient._snapshot_label_value(snapshot, "Effective Date:"))
        last_action_raw = FloridaApiClient._snapshot_value_after_label(snapshot, "Last Action:")
        last_action_date, last_action = FloridaApiClient._split_last_action(last_action_raw)
        return {
            "measure_type": measure_type,
            "effective_date": effective_date,
            "last_action_date": last_action_date,
            "last_action": last_action,
            "current_text_html": current_text_html,
            "current_text_pdf": current_text_pdf,
        }

    @staticmethod
    def _snapshot_label_value(snapshot: Tag, label: str) -> str:
        label_node = snapshot.find("span", class_="bold", string=lambda value: isinstance(value, str) and label in value)
        if label_node is None:
            return ""
        next_span = label_node.find_next_sibling("span")
        if next_span is not None:
            return clean_text(next_span.get_text(" ", strip=True))
        return ""

    @staticmethod
    def _snapshot_value_after_label(snapshot: Tag, label: str) -> str:
        label_node = snapshot.find("span", class_="bold", string=lambda value: isinstance(value, str) and label in value)
        if label_node is None:
            return ""
        values: list[str] = []
        for sibling in label_node.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "span" and "bold" in (sibling.get("class") or []):
                break
            if isinstance(sibling, Tag) and sibling.name == "br":
                break
            text = clean_text(getattr(sibling, "get_text", lambda *args, **kwargs: str(sibling))(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
            if text:
                values.append(text)
        return clean_text(" ".join(values))

    @staticmethod
    def _split_last_action(value: str | None) -> tuple[str, str]:
        raw = clean_text(value)
        if not raw:
            return ("", "")
        raw = raw.replace("\n", " ")
        raw = re.sub(r"^Last Action:\s*", "", raw, flags=re.IGNORECASE)
        date_match = re.match(r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<rest>.+)$", raw)
        if date_match is None:
            return ("", raw)
        rest = clean_text(date_match.group("rest")).lstrip("- ").strip()
        rest = re.sub(r"^[HS]\s+-?\s*", "", rest)
        return (parse_florida_date(date_match.group("date")), rest)

    @staticmethod
    def _bill_history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        container = soup.find("div", id="tabBodyBillHistory")
        if container is None:
            return []
        table = container.find("table")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            rows.append(
                {
                    "statusDate": parse_florida_date(cells[0].get_text(" ", strip=True)),
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[2].get_text(" ", strip=True)),
                }
            )
        return rows

    @staticmethod
    def _bill_text_rows(soup: BeautifulSoup, detail_url: str) -> list[dict[str, str]]:
        container = soup.find("div", id="tabBodyBillText")
        if container is None:
            return []
        table = container.find("table")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True))
            posted = parse_florida_date(cells[1].get_text(" ", strip=True))
            html_url = ""
            pdf_url = ""
            for link in cells[2].find_all("a", href=True):
                link_text = clean_text(link.get_text(" ", strip=True)).lower()
                absolute = absolute_url(detail_url, link.get("href")) or ""
                if link_text == "web page":
                    html_url = absolute
                elif link_text == "pdf":
                    pdf_url = absolute
            rows.append(
                {
                    "label": label,
                    "posted": posted,
                    "html_url": html_url,
                    "pdf_url": pdf_url,
                }
            )
        return rows

    @staticmethod
    def _analysis_rows(soup: BeautifulSoup, detail_url: str) -> list[dict[str, str]]:
        container = soup.find("div", id="tabBodyAnalyses")
        if container is None:
            return []
        table = container.find("table")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 5:
                continue
            link = cells[4].find("a", href=True)
            rows.append(
                {
                    "type": clean_text(cells[0].get_text(" ", strip=True)),
                    "analysis": clean_text(cells[1].get_text(" ", strip=True)),
                    "author": clean_text(cells[2].get_text(" ", strip=True)),
                    "posted": parse_florida_date(cells[3].get_text(" ", strip=True)),
                    "document_url": absolute_url(detail_url, link.get("href")) if link is not None else "",
                }
            )
        return rows

    @staticmethod
    def _amendment_rows(soup: BeautifulSoup, bill_num: str, detail_url: str) -> list[dict[str, Any]]:
        container = soup.find("div", id="tabBodyAmendments")
        if container is None:
            return []
        amendments: list[dict[str, Any]] = []
        chamber = bill_num[:1]
        sequence = 0

        for section_id, label in (("CommitteeAmendment", "Committee amendment"), ("FloorAmendment", "Floor amendment")):
            section = container.find("div", id=section_id)
            if section is None:
                continue
            for table in section.find_all("table"):
                caption = clean_text(table.caption.get_text(" ", strip=True) if table.caption is not None else "")
                for row in table.find_all("tr"):
                    cells = row.find_all("td", recursive=False)
                    if len(cells) < 5:
                        continue
                    first_lines = [clean_text(text) for text in cells[0].stripped_strings if clean_text(text)]
                    if not first_lines:
                        continue
                    amendment_number = clean_text(first_lines[0].split("-", 1)[0])
                    if not amendment_number:
                        continue
                    html_url = ""
                    pdf_url = ""
                    for link in cells[4].find_all("a", href=True):
                        link_text = clean_text(link.get_text(" ", strip=True)).lower()
                        absolute = absolute_url(detail_url, link.get("href")) or ""
                        if link_text == "web page":
                            html_url = absolute
                        elif link_text == "pdf":
                            pdf_url = absolute
                    filed = clean_text(cells[2].get_text(" ", strip=True))
                    sequence += 1
                    amendments.append(
                        {
                            "amendmentNumber": amendment_number,
                            "house": chamber,
                            "order": clean_text(" ".join(part for part in [label, caption] if part)),
                            "sequence": f"{sequence:04d}",
                            "status": clean_text(cells[3].get_text(" ", strip=True)) or "Filed",
                            "sponsor": clean_text(cells[1].get_text(" ", strip=True)),
                            "documentUrl": html_url or pdf_url,
                            "filedDate": parse_florida_date(filed),
                        }
                    )
        return amendments

    @staticmethod
    def _signed_date(actions: list[dict[str, str]]) -> str:
        for action in reversed(actions):
            action_text = str(action.get("statusMessage") or "").lower()
            if "approved by governor" in action_text or "signed by officers and presented to governor" in action_text:
                return str(action.get("statusDate") or "")
        return ""
