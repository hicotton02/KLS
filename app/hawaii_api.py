from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


HAWAII_BILLS_DIRECTORY_TEMPLATE = "/sessions/session{year}/Bills/"
HAWAII_DETAIL_TEMPLATE = "/session/measure_indiv.aspx?billtype={bill_type}&billnumber={bill_number}&year={year}"
HAWAII_SUPPORTED_TYPES = {"HB", "SB", "HCR", "SCR", "HR", "SR"}
HAWAII_FILE_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z]+)(?P<number>\d+)(?:_(?P<suffix>[A-Z0-9_]+))?_\.(?P<ext>HTM|PDF)$",
    re.IGNORECASE,
)
HAWAII_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HR|SR)\s*0*(\d+)$", re.IGNORECASE)


def parse_hawaii_date(value: str | None) -> str:
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


def normalize_hawaii_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper()
    match = HAWAII_BILL_NUMBER_PATTERN.fullmatch(raw)
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


def _file_sort_key(entry: dict[str, str]) -> tuple[int, int, str]:
    label = clean_text(entry.get("label")).upper()
    if not label:
        return (0, 0, "")
    rank = 0
    if "CD" in label:
        rank = 4
    elif "SD" in label:
        rank = 3
    elif "HD" in label:
        rank = 2
    elif "FA" in label or "PROPOSED" in label:
        rank = 1
    digits = [int(value) for value in re.findall(r"\d+", label)]
    return (rank, digits[-1] if digits else 0, label)


class HawaiiApiClient:
    index_requires_detail_fetch = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.hawaii_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(HAWAII_BILLS_DIRECTORY_TEMPLATE.format(year=year))
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items_by_bill: dict[str, dict[str, Any]] = {}
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            file_name = href.rsplit("/", 1)[-1]
            match = HAWAII_FILE_PATTERN.fullmatch(file_name)
            if match is None:
                continue

            bill_type = match.group("prefix").upper()
            if bill_type not in HAWAII_SUPPORTED_TYPES:
                continue

            bill_num = f"{bill_type}{int(match.group('number'))}"
            # Hawaii's public Bills directory currently includes a stale placeholder PDF
            # for SB9999 that does not resolve to a real measure page.
            if bill_num == "SB9999":
                continue
            suffix = clean_text(match.group("suffix")).upper()
            document_url = absolute_url(str(response.url), href) or ""

            item = items_by_bill.setdefault(
                bill_num,
                {
                    "billNum": bill_num,
                    "billType": bill_type,
                    "catchTitle": bill_num,
                    "billTitle": bill_num,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(
                        self.settings.hawaii_site_base,
                        HAWAII_DETAIL_TEMPLATE.format(
                            bill_type=bill_type,
                            bill_number=int(match.group("number")),
                            year=year,
                        ),
                    )
                    or "",
                    "currentVersionPath": None,
                    "currentVersionFingerprint": "",
                    "_fileEntries": [],
                },
            )
            item["_fileEntries"].append(
                {
                    "label": suffix,
                    "documentUrl": document_url,
                    "extension": match.group("ext").lower(),
                }
            )

        for item in items_by_bill.values():
            file_entries = list(item.get("_fileEntries") or [])
            item["currentVersionPath"] = self._pick_current_version_path(file_entries)
            item["currentVersionFingerprint"] = "|".join(
                sorted(
                    filter(
                        None,
                        (
                            f"{clean_text(entry.get('label')).upper()}:{clean_text(entry.get('documentUrl'))}"
                            for entry in file_entries
                        ),
                    )
                )
            )

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        if "measurenotfound" in str(response.url).lower():
            raise ValueError(f"Hawaii measure was not found for {detail_path}")

        soup = BeautifulSoup(response.text, "html.parser")
        measure_table = soup.find("table", id="measure-info")
        if measure_table is None:
            raise ValueError(f"Hawaii measure info was not found for {detail_path}")

        fields = self._measure_fields(measure_table)
        bill_num = first_non_empty(
            normalize_hawaii_bill_number((item or {}).get("billNum")),
            self._bill_num_from_detail_url(str(response.url)),
        )
        if not bill_num:
            raise ValueError(f"Hawaii bill number could not be determined from {detail_path}")

        bill_title = first_non_empty(fields.get("Measure Title"), fields.get("Report Title"), bill_num)
        report_title = clean_text(fields.get("Report Title"))
        description = clean_text(fields.get("Description"))
        sponsor = clean_text(fields.get("Introducer(s)"))
        companion = clean_text(fields.get("Companion"))
        current_referral = clean_text(fields.get("Current Referral"))
        act = clean_text(fields.get("Act"))

        actions = self._status_rows(soup)
        latest_action = actions[0] if actions else {}
        current_version_path = self._pick_current_version_path(
            self._detail_document_entries(str(response.url), soup, bill_num) or list((item or {}).get("_fileEntries") or [])
        )
        version_entries = list((item or {}).get("_fileEntries") or [])
        current_version_fingerprint = "|".join(
            part
            for part in (
                current_version_path or "",
                act,
                str(len(actions)),
                "|".join(sorted(clean_text(entry.get("label")).upper() for entry in version_entries if clean_text(entry.get("label")))),
                str(actions[0].get("statusMessage") or "") if actions else "",
            )
            if clean_text(part)
        )

        digest_bits = [
            f"Report title: {report_title}" if report_title else "",
            f"Description: {description}" if description else "",
            f"Introducer(s): {sponsor}" if sponsor else "",
            f"Current referral: {current_referral}" if current_referral else "",
            f"Companion: {companion}" if companion else "",
            f"Act: {act}" if act else "",
        ]
        digest_bits.extend(
            f"{action['statusDate']}: {action['statusMessage']}".strip(": ")
            for action in actions[:5]
            if action.get("statusMessage")
        )

        return {
            "bill": bill_num,
            "billType": _bill_type(bill_num),
            "catchTitle": bill_title,
            "sponsor": sponsor,
            "billTitle": bill_title,
            "billStatus": first_non_empty(
                latest_action.get("statusMessage"),
                f"Act {act}" if act else "",
                current_referral,
            ),
            "lastAction": first_non_empty(latest_action.get("statusMessage"), current_referral),
            "lastActionDate": clean_text(latest_action.get("statusDate")),
            "signedDate": clean_text(latest_action.get("statusDate")) if act else "",
            "effectiveDate": "",
            "chapter": act,
            "enrolledNumber": "",
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": current_version_path,
            "digest": str(response.url),
            "summary": str(response.url),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": self._paragraph_html(bill_title, report_title, description),
            "digestHTML": self._paragraph_html(*[bit for bit in digest_bits if bit]),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": self._build_amendments(version_entries),
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _pick_current_version_path(entries: list[dict[str, str]]) -> str:
        html_entries = [entry for entry in entries if clean_text(entry.get("label")) == "" and entry.get("extension") == "htm"]
        if html_entries:
            return clean_text(html_entries[0].get("documentUrl"))
        pdf_entries = [entry for entry in entries if clean_text(entry.get("label")) == "" and entry.get("extension") == "pdf"]
        if pdf_entries:
            return clean_text(pdf_entries[0].get("documentUrl"))
        html_entries = [entry for entry in entries if entry.get("extension") == "htm"]
        if html_entries:
            return clean_text(sorted(html_entries, key=_file_sort_key, reverse=True)[0].get("documentUrl"))
        if entries:
            return clean_text(sorted(entries, key=_file_sort_key, reverse=True)[0].get("documentUrl"))
        return ""

    @staticmethod
    def _measure_fields(table: Tag) -> dict[str, str]:
        fields: dict[str, str] = {}
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) < 2:
                continue
            label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
            value = clean_text(cells[1].get_text(" ", strip=True))
            if label:
                fields[label] = value
        return fields

    @staticmethod
    def _status_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table", id=re.compile(r"GridViewStatus$", re.IGNORECASE))
        if table is None:
            return []

        actions: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 3:
                continue
            status_date = parse_hawaii_date(cells[0].get_text(" ", strip=True))
            chamber = clean_text(cells[1].get_text(" ", strip=True))
            message = clean_text(cells[2].get_text(" ", strip=True))
            if not status_date or not message:
                continue
            actions.append(
                {
                    "statusDate": status_date,
                    "location": chamber,
                    "statusMessage": message,
                }
            )
        return actions

    @staticmethod
    def _detail_document_entries(detail_url: str, soup: BeautifulSoup, bill_num: str) -> list[dict[str, str]]:
        entries: list[dict[str, str]] = []
        bill_prefix = _bill_type(bill_num)
        bill_number = bill_num[len(bill_prefix) :]
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "")
            file_name = href.rsplit("/", 1)[-1]
            match = HAWAII_FILE_PATTERN.fullmatch(file_name)
            if match is None:
                continue
            if match.group("prefix").upper() != bill_prefix or str(int(match.group("number"))) != str(int(bill_number)):
                continue
            entries.append(
                {
                    "label": clean_text(match.group("suffix")).upper(),
                    "documentUrl": absolute_url(detail_url, href) or "",
                    "extension": match.group("ext").lower(),
                }
            )
        return entries

    @staticmethod
    def _bill_num_from_detail_url(detail_url: str) -> str:
        parsed = urlsplit(detail_url)
        params = parse_qs(parsed.query)
        bill_type = clean_text((params.get("billtype") or [""])[0]).upper()
        bill_number = clean_text((params.get("billnumber") or [""])[0])
        if bill_type in HAWAII_SUPPORTED_TYPES and bill_number.isdigit():
            return f"{bill_type}{int(bill_number)}"
        return ""

    @staticmethod
    def _build_amendments(version_entries: list[dict[str, str]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        seen: set[str] = set()
        ordered_entries = sorted(
            (entry for entry in version_entries if clean_text(entry.get("label"))),
            key=_file_sort_key,
        )
        for index, entry in enumerate(ordered_entries, start=1):
            label = clean_text(entry.get("label")).upper()
            if not label or label in seen:
                continue
            seen.add(label)
            amendments.append(
                {
                    "amendmentNumber": label,
                    "house": label[:1],
                    "order": str(index),
                    "sequence": str(index),
                    "status": f"Hawaii published version {label}",
                    "sponsor": "",
                    "documentUrl": clean_text(entry.get("documentUrl")) or None,
                }
            )
        return amendments

    @staticmethod
    def _paragraph_html(*values: str) -> str:
        parts = [clean_text(value) for value in values if clean_text(value)]
        return "".join(f"<p>{html.escape(part)}</p>" for part in parts)
