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


NEW_HAMPSHIRE_RESULTS_TEMPLATE = "/bill_status/legacy/bs2016/results.aspx?lsr=&sy=&txtsessionyear={year}&sortoption="
NEW_HAMPSHIRE_STATUS_TEMPLATE = (
    "/bill_status/legacy/bs2016/bill_status.aspx?lsr={lsr}&sy={year}&txtsessionyear={year}&sortoption="
)
NEW_HAMPSHIRE_DOCKET_TEMPLATE = (
    "/bill_status/legacy/bs2016/bill_docket.aspx?lsr={lsr}&sy={year}&txtsessionyear={year}&sortoption="
)
NEW_HAMPSHIRE_BILL_NUMBER_PATTERN = re.compile(r"^([A-Z]+)\s*0*(\d+)")
NEW_HAMPSHIRE_HEARING_PATTERN = re.compile(
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+at\s+(?P<time>\d{1,2}:\d{2}\s+[AP]M)\s+(?P<place>.+)",
    re.IGNORECASE,
)
NEW_HAMPSHIRE_AMENDMENT_PATTERN = re.compile(r"Amendment\s+(#?[0-9]{4}-[0-9A-Z]+)", re.IGNORECASE)


def parse_new_hampshire_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_new_hampshire_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = NEW_HAMPSHIRE_BILL_NUMBER_PATTERN.match(raw)
    if match is None:
        return ""
    return f"{match.group(1)}{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _bill_type(bill_num: str) -> str:
    match = re.fullmatch(r"([A-Z]+)\d+", str(bill_num or "").strip().upper())
    return match.group(1) if match else ""


def _row_texts(row: Tag) -> list[str]:
    return [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]


class NewHampshireApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.new_hampshire_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(NEW_HAMPSHIRE_RESULTS_TEMPLATE.format(year=year))
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items_by_bill: dict[str, dict[str, Any]] = {}
        for status_link in soup.find_all("a", href=re.compile(r"bill_status\.aspx\?", re.IGNORECASE)):
            left_cell = status_link.find_parent("td")
            if left_cell is None:
                continue
            right_cell = left_cell.find_next_sibling("td")
            if right_cell is None:
                continue

            big_tag = left_cell.find("big")
            raw_bill = clean_text(big_tag.get_text(" ", strip=True) if big_tag is not None else status_link.get_text(" ", strip=True))
            bill_num = normalize_new_hampshire_bill_number(raw_bill)
            if not bill_num or bill_num in items_by_bill:
                continue

            title = self._result_title(right_cell) or bill_num
            status_map = self._result_status_map(right_cell)
            detail_path = absolute_url(str(response.url), status_link.get("href")) or ""
            docket_path = absolute_url(str(response.url), self._find_link(left_cell, "bill_docket.aspx"))
            current_version_path = absolute_url(str(response.url), self._find_bill_text_link(left_cell, "html"))
            pdf_path = absolute_url(str(response.url), self._find_bill_text_link(left_cell, "pdf"))
            hearing_value = clean_text(status_map.get("Next/Last Hearing"))

            items_by_bill[bill_num] = {
                "billNum": bill_num,
                "billType": _bill_type(bill_num),
                "catchTitle": title,
                "billTitle": title,
                "sponsor": "",
                "billStatus": self._primary_status(
                    status_map.get("G-Status"),
                    status_map.get("House Status"),
                    status_map.get("Senate Status"),
                ),
                "lastAction": self._primary_status(
                    status_map.get("House Status"),
                    status_map.get("Senate Status"),
                    status_map.get("G-Status"),
                ),
                "lastActionDate": "",
                "signedDate": "",
                "effectiveDate": "",
                "chapter": "",
                "enrolledNumber": raw_bill,
                "detailPath": detail_path,
                "docketPath": docket_path,
                "currentVersionPath": current_version_path or pdf_path,
                "currentVersionFingerprint": "|".join(
                    part
                    for part in (
                        raw_bill,
                        title,
                        status_map.get("G-Status"),
                        status_map.get("House Status"),
                        status_map.get("Senate Status"),
                        status_map.get("Next/Last Comm"),
                        hearing_value,
                        current_version_path or pdf_path or "",
                    )
                    if clean_text(str(part))
                ),
            }

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = soup.find("table", id="Table1")
        if table is None:
            raise ValueError(f"New Hampshire bill detail table was not found for {detail_path}")

        rows = table.find_all("tr")
        if not rows:
            raise ValueError(f"New Hampshire bill detail rows were missing for {detail_path}")

        bill_num = normalize_new_hampshire_bill_number(_row_texts(rows[0])[0] if rows else "")
        bill_num = first_non_empty(bill_num, normalize_new_hampshire_bill_number((item or {}).get("billNum")))
        if not bill_num:
            raise ValueError(f"New Hampshire bill number could not be determined from {detail_path}")

        bill_title = self._label_value(_row_texts(rows[1])[0], "Bill Title")
        summary_fields = self._summary_fields(rows)
        house_fields = self._section_fields(rows, "House Status", "Senate Status")
        senate_fields = self._section_fields(rows, "Senate Status", "Sponsors")
        sponsors = self._sponsors(rows)
        hearing_info = self._hearing_info(rows)

        docket_path = absolute_url(str(response.url), self._first_matching_link(soup, "bill_docket.aspx"))
        current_version_path = absolute_url(str(response.url), self._find_bill_text_link(soup, "html"))
        pdf_path = absolute_url(str(response.url), self._find_bill_text_link(soup, "pdf"))
        roll_call_path = absolute_url(str(response.url), self._first_matching_link(soup, "billrollcalls.aspx"))
        docket_actions, amendments = self._fetch_docket(docket_path)

        gen_status = summary_fields.get("Gen Status")
        house_status = house_fields.get("Status")
        senate_status = senate_fields.get("Status")
        bill_status = self._primary_status(gen_status, house_status, senate_status)
        last_action = docket_actions[-1]["statusMessage"] if docket_actions else self._primary_status(house_status, senate_status, gen_status)
        last_action_date = docket_actions[-1]["statusDate"] if docket_actions else ""
        chapter = clean_text(summary_fields.get("Chapter#"))
        if chapter.lower() == "none":
            chapter = ""
        signed_date = last_action_date if chapter and last_action_date else ""

        digest_bits = [
            f"General status: {gen_status}" if gen_status else "",
            f"House status: {house_status}" if house_status else "",
            f"Senate status: {senate_status}" if senate_status else "",
            f"House committee: {house_fields.get('Current Committee')}" if house_fields.get("Current Committee") else "",
            f"Senate committee: {senate_fields.get('Current Committee')}" if senate_fields.get("Current Committee") else "",
            f"Sponsors: {', '.join(sponsors)}" if sponsors else "",
            f"Next or last hearing: {hearing_info['headline']}" if hearing_info.get("headline") else "",
            f"Hearing date: {hearing_info['date']}" if hearing_info.get("date") else "",
            f"Hearing time: {hearing_info['time']}" if hearing_info.get("time") else "",
            f"Hearing place: {hearing_info['place']}" if hearing_info.get("place") else "",
        ]
        if docket_actions:
            digest_bits.extend(
                f"{action['statusDate']}: {action['statusMessage']}".strip(": ")
                for action in docket_actions[-3:]
                if action.get("statusMessage")
            )

        return {
            "bill": bill_num,
            "billType": _bill_type(bill_num),
            "catchTitle": first_non_empty(bill_title, clean_text((item or {}).get("catchTitle")), bill_num),
            "sponsor": ", ".join(sponsors),
            "billTitle": first_non_empty(bill_title, clean_text((item or {}).get("billTitle")), bill_num),
            "billStatus": bill_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(summary_fields.get("LSR#")) or clean_text((item or {}).get("enrolledNumber")) or bill_num,
            "sponsorStringHouse": ", ".join(sponsors) if bill_num.startswith("H") else None,
            "sponsorStringSenate": ", ".join(sponsors) if bill_num.startswith("S") else None,
            "introduced": current_version_path or pdf_path,
            "digest": docket_path or roll_call_path or detail_path,
            "summary": detail_path,
            "currentVersionPath": current_version_path or pdf_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path or pdf_path or "",
                    bill_status,
                    last_action,
                    last_action_date,
                    chapter,
                    str(len(amendments)),
                )
                if clean_text(str(part))
            ),
            "summaryHTML": self._paragraph_html(first_non_empty(bill_title, bill_num)),
            "digestHTML": self._paragraph_html(*[bit for bit in digest_bits if bit]),
            "currentBillHTML": "",
            "billActions": docket_actions,
            "amendments": amendments,
            "officialPage": detail_path,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _result_title(cell: Tag) -> str:
        label = cell.find("b")
        if label is None:
            return clean_text(cell.get_text(" ", strip=True))
        parts: list[str] = []
        for sibling in label.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "table":
                break
            text = clean_text(sibling.get_text(" ", strip=True) if isinstance(sibling, Tag) else str(sibling))
            if text:
                parts.append(text)
        return clean_text(" ".join(parts))

    @staticmethod
    def _result_status_map(cell: Tag) -> dict[str, str]:
        nested = cell.find("table")
        if nested is None:
            return {}
        data: dict[str, str] = {}
        for row in nested.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
            value = clean_text(cells[1].get_text(" ", strip=True))
            if label:
                data[label] = value
        return data

    @staticmethod
    def _find_link(container: Tag, fragment: str) -> str | None:
        link = container.find("a", href=re.compile(re.escape(fragment), re.IGNORECASE))
        return link.get("href") if link is not None else None

    @staticmethod
    def _find_bill_text_link(container: Tag, fmt: str) -> str | None:
        for link in container.find_all("a", href=True):
            href = str(link.get("href") or "")
            if "billText.aspx" not in href:
                continue
            if f"txtFormat={fmt}".lower() in href.lower():
                return href
        return None

    @staticmethod
    def _primary_status(*values: str | None) -> str:
        for value in values:
            text = clean_text(value)
            if text:
                return text
        return ""

    @staticmethod
    def _label_value(text: str, label: str) -> str:
        raw = clean_text(text)
        prefix = f"{label}:"
        if raw.startswith(prefix):
            return clean_text(raw[len(prefix) :])
        return raw

    @staticmethod
    def _summary_fields(rows: list[Tag]) -> dict[str, str]:
        data: dict[str, str] = {}
        for row in rows:
            texts = _row_texts(row)
            if len(texts) >= 5 and any(part.startswith("LSR#:") for part in texts):
                for part in texts:
                    if ":" not in part:
                        continue
                    label, value = part.split(":", 1)
                    data[clean_text(label)] = clean_text(value)
                break
        return data

    @staticmethod
    def _section_fields(rows: list[Tag], section_name: str, next_section: str) -> dict[str, str]:
        data: dict[str, str] = {}
        capture = False
        for row in rows:
            texts = _row_texts(row)
            if not texts:
                continue
            first = texts[0]
            if first == section_name:
                capture = True
                continue
            if capture and first == next_section:
                break
            if capture and len(texts) >= 3:
                label = clean_text(texts[1]).rstrip(":")
                value = clean_text(texts[2])
                if label:
                    data[label] = value
        return data

    @staticmethod
    def _sponsors(rows: list[Tag]) -> list[str]:
        sponsors: list[str] = []
        capture = False
        for row in rows:
            texts = _row_texts(row)
            if not texts:
                continue
            if texts[0] == "Sponsors":
                capture = True
                continue
            if capture and texts[0].startswith("Next/Last Hearing"):
                break
            if not capture:
                continue
            for text in texts:
                normalized = clean_text(text)
                if not normalized or normalized in {"Sponsors"}:
                    continue
                if normalized.startswith("Next/Last Hearing"):
                    continue
                for part in re.split(r"\s{2,}", normalized):
                    candidate = clean_text(part)
                    if candidate and candidate not in sponsors and "(" in candidate and candidate.count("(") <= 1:
                        sponsors.append(candidate)
        return sponsors

    @staticmethod
    def _hearing_info(rows: list[Tag]) -> dict[str, str]:
        info = {"headline": "", "date": "", "time": "", "place": ""}
        for index, row in enumerate(rows):
            texts = _row_texts(row)
            if not texts:
                continue
            headline = texts[0]
            if not headline.startswith("Next/Last Hearing"):
                continue
            info["headline"] = headline.replace("Next/Last Hearing:", "", 1).strip()
            if index + 3 < len(rows):
                values = _row_texts(rows[index + 3])
                if len(values) >= 3:
                    info["date"] = parse_new_hampshire_date(values[0])
                    info["time"] = clean_text(values[1])
                    info["place"] = clean_text(values[2])
            if not info["date"]:
                match = NEW_HAMPSHIRE_HEARING_PATTERN.search(headline)
                if match is not None:
                    info["date"] = parse_new_hampshire_date(match.group("date"))
                    info["time"] = clean_text(match.group("time"))
                    info["place"] = clean_text(match.group("place"))
            break
        return info

    def _fetch_docket(self, docket_path: str | None) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        if not docket_path:
            return [], []
        response = self.client.get(docket_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", id="Table1")
        if table is None:
            return [], []

        actions: list[dict[str, str]] = []
        amendments: list[dict[str, Any]] = []
        for row in table.find_all("tr"):
            texts = _row_texts(row)
            if len(texts) != 3:
                continue
            date_text, body, description = texts
            parsed_date = parse_new_hampshire_date(date_text)
            if date_text == "Date" or not date_text or not description or not parsed_date or not date_text[0].isdigit():
                continue
            action = {
                "statusDate": parsed_date,
                "location": clean_text(body),
                "statusMessage": description,
            }
            actions.append(action)

            amendment_match = (
                NEW_HAMPSHIRE_AMENDMENT_PATTERN.search(description)
                if description.strip().lower().startswith("amendment")
                else None
            )
            if amendment_match is None:
                continue
            amendment_number = clean_text(amendment_match.group(1)).lstrip("#").upper()
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": clean_text(body),
                    "order": str(len(amendments) + 1),
                    "sequence": str(len(amendments) + 1),
                    "status": description,
                    "sponsor": "",
                    "documentUrl": None,
                }
            )

        return actions, amendments

    @staticmethod
    def _first_matching_link(soup: BeautifulSoup, fragment: str) -> str | None:
        link = soup.find("a", href=re.compile(re.escape(fragment), re.IGNORECASE))
        return link.get("href") if link is not None else None

    @staticmethod
    def _paragraph_html(*values: str) -> str:
        parts = [clean_text(value) for value in values if clean_text(value)]
        return "".join(f"<p>{html.escape(part)}</p>" for part in parts)
