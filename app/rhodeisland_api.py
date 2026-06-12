from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


RHODE_ISLAND_BILL_ID_PATTERN = re.compile(r"^(?P<base>[HS]\d{4})(?P<suffix>[A-Za-z]*)$")
RHODE_ISLAND_STATUS_DATE_PATTERN = re.compile(r"^(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<action>.+)$")
RHODE_ISLAND_CHAPTER_PATTERN = re.compile(r"^Chapter\s+(\d+)\b", re.IGNORECASE)
RHODE_ISLAND_HEADING_PATTERN = re.compile(
    r"^(House|Senate)\s+(Bill|Resolution)\s+No\.\s+(?P<number>\d{4})(?:\s+(?P<version>.+))?$",
    re.IGNORECASE,
)


def rhode_island_session_suffix(year: int) -> str:
    return f"{year % 100:02d}"


def parse_rhode_island_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_rhode_island_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    match = RHODE_ISLAND_BILL_ID_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return match.group("base")


def _variant_suffix(value: str) -> str:
    match = RHODE_ISLAND_BILL_ID_PATTERN.fullmatch(str(value or "").strip().upper())
    if match is None:
        return ""
    return match.group("suffix").upper()


def _variant_sort_key(variant: dict[str, str]) -> tuple[int, str]:
    suffix = _variant_suffix(variant.get("bill_id", ""))
    return (len(suffix), suffix)


def _chamber_from_bill_num(bill_num: str) -> str:
    return "H" if str(bill_num or "").upper().startswith("H") else "S"


def _variant_label(bill_num: str, variant_bill_id: str) -> str:
    suffix = _variant_suffix(variant_bill_id)
    if not suffix:
        return "Original text"
    if suffix == "A":
        return "Substitute A"
    if suffix == "AA":
        return "Amended text"
    if suffix.endswith("AA"):
        core = suffix[:-2]
        if core:
            return f"Substitute {core} as amended"
        return "Amended text"
    return f"Version {suffix}"


class RhodeIslandApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        headers = {"User-Agent": "keeping-law-simple/1.0"}
        self.text_client = httpx.Client(
            base_url=self.settings.rhode_island_site_base,
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.status_client = httpx.Client(
            base_url=self.settings.rhode_island_status_base,
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._status_form_state: dict[str, str] | None = None

    def close(self) -> None:
        self.text_client.close()
        self.status_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_suffix = rhode_island_session_suffix(year)
        items_by_bill: dict[str, dict[str, Any]] = {}

        for chamber_name, prefix in (("House", "H"), ("Senate", "S")):
            listing_path = f"/BillText{session_suffix}/{chamber_name}Text{session_suffix}/{chamber_name}Text{session_suffix}.html"
            response = self.text_client.get(listing_path)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            for row in soup.select("table.bill_data tr"):
                bill_cell = row.select_one("td.bill_col1")
                if bill_cell is None:
                    continue
                bill_id = " ".join(bill_cell.get_text(" ", strip=True).split()).upper()
                match = RHODE_ISLAND_BILL_ID_PATTERN.fullmatch(bill_id)
                if match is None or not bill_id.startswith(prefix):
                    continue
                base_bill_num = match.group("base").upper()

                links = row.find_all("a", href=True)
                pdf_url = ""
                html_url = ""
                for link in links:
                    label = link.get_text(" ", strip=True).upper()
                    target = absolute_url(str(response.url), link.get("href")) or ""
                    if label == "PDF":
                        pdf_url = target
                    elif label == "HTML":
                        html_url = target

                variant = {
                    "bill_id": bill_id,
                    "pdf_url": pdf_url,
                    "html_url": html_url,
                }
                item = items_by_bill.setdefault(
                    base_bill_num,
                    {
                        "billNum": base_bill_num,
                        "billType": prefix,
                        "catchTitle": base_bill_num,
                        "billTitle": base_bill_num,
                        "sponsor": "",
                        "billStatus": "",
                        "lastAction": "",
                        "lastActionDate": "",
                        "variants": [],
                    },
                )
                item["variants"].append(variant)

        items: list[dict[str, Any]] = []
        for bill_num in sorted(items_by_bill):
            item = items_by_bill[bill_num]
            variants = sorted(item["variants"], key=_variant_sort_key)
            introduced = variants[0]
            current = variants[-1]
            items.append(
                {
                    **item,
                    "detailPath": introduced.get("html_url") or introduced.get("pdf_url") or current.get("html_url") or current.get("pdf_url"),
                    "introducedPath": introduced.get("html_url") or introduced.get("pdf_url"),
                    "currentVersionPath": current.get("html_url") or current.get("pdf_url"),
                    "currentVersionFingerprint": "|".join(
                        part
                        for part in [
                            str(current.get("bill_id") or ""),
                            str(current.get("pdf_url") or ""),
                            str(current.get("html_url") or ""),
                        ]
                        if part
                    ),
                    "variants": variants,
                }
            )

        return items

    def fetch_bill_detail(self, year: int, item: dict[str, Any]) -> dict[str, Any]:
        bill_num = str(item.get("billNum") or "").strip().upper()
        if not bill_num:
            raise ValueError("Rhode Island bill item is missing bill number")

        variants = [dict(variant) for variant in item.get("variants") or [] if isinstance(variant, dict)]
        introduced_variant = variants[0] if variants else {}
        current_variant = variants[-1] if variants else {}
        current_version_path = str(item.get("currentVersionPath") or current_variant.get("html_url") or current_variant.get("pdf_url") or "")
        current_version_fingerprint = str(item.get("currentVersionFingerprint") or "")

        status_report = self._fetch_status_report(year, bill_num)
        title = status_report.get("title") or bill_num
        sponsor = status_report.get("sponsor") or ""
        actions = status_report.get("actions") or []
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": "", "location": ""}
        chapter_no = status_report.get("chapter") or ""
        version_label = status_report.get("version_label") or _variant_label(bill_num, str(current_variant.get("bill_id") or bill_num))
        current_pdf_url = status_report.get("current_pdf_url") or str(current_variant.get("pdf_url") or "")

        signed_date = ""
        for action in reversed(actions):
            if "signed by governor" in str(action.get("statusMessage") or "").lower():
                signed_date = str(action.get("statusDate") or "")
                break

        amendments = self._build_variant_amendments(bill_num, variants)

        return {
            "bill": bill_num,
            "billType": str(item.get("billType") or _chamber_from_bill_num(bill_num)),
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter_no,
            "enrolledNumber": version_label,
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": str(item.get("introducedPath") or introduced_variant.get("html_url") or introduced_variant.get("pdf_url") or "") or None,
            "digest": current_pdf_url or None,
            "summary": current_pdf_url or current_version_path or None,
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    current_version_fingerprint,
                    current_pdf_url,
                    version_label,
                    chapter_no,
                ]
                if part
            ),
            "summaryHTML": self._paragraph_html(title),
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": current_pdf_url or current_version_path or str(item.get("detailPath") or ""),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.text_client, url)

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return f"<p>{text}</p>"

    def _fetch_status_report(self, year: int, bill_num: str) -> dict[str, Any]:
        form_state = self._status_form_state or self._refresh_status_form_state()
        numeric_part = str(bill_num or "").strip().upper()[1:]
        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "__VIEWSTATE": form_state["__VIEWSTATE"],
            "__VIEWSTATEGENERATOR": form_state["__VIEWSTATEGENERATOR"],
            "__EVENTVALIDATION": form_state["__EVENTVALIDATION"],
            "ctl00$rilinContent$cbYear": str(year),
            "ctl00$rilinContent$cbCommittee": "",
            "ctl00$rilinContent$comm": "cbxIn",
            "ctl00$rilinContent$cbCategory": "",
            "ctl00$rilinContent$cbSponsor": "",
            "ctl00$rilinContent$txtBills": "",
            "ctl00$rilinContent$cbxSortNumeric": "",
            "ctl00$rilinContent$txtBillFrom": numeric_part,
            "ctl00$rilinContent$txtBillTo": numeric_part,
            "ctl00$rilinContent$cbAction": "",
            "ctl00$rilinContent$cbxLastAction": "",
            "ctl00$rilinContent$cmdReport": "Enter",
            "ctl00$rilinContent$hfQuery": "",
        }
        response = self.status_client.post("/", data=payload)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        report = soup.select_one("#lblBills")
        if report is None:
            self._status_form_state = self._refresh_status_form_state()
            payload["__VIEWSTATE"] = self._status_form_state["__VIEWSTATE"]
            payload["__VIEWSTATEGENERATOR"] = self._status_form_state["__VIEWSTATEGENERATOR"]
            payload["__EVENTVALIDATION"] = self._status_form_state["__EVENTVALIDATION"]
            response = self.status_client.post("/", data=payload)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            report = soup.select_one("#lblBills")
        if report is None:
            raise ValueError(f"Rhode Island status report not found for {bill_num} ({year})")

        heading = ""
        version_label = ""
        title = ""
        sponsor = ""
        chapter_no = ""
        current_pdf_url = ""
        actions: list[dict[str, str]] = []

        for div in report.find_all("div", recursive=False):
            text = " ".join(div.get_text(" ", strip=True).split())
            if not text:
                continue
            heading_match = RHODE_ISLAND_HEADING_PATTERN.match(text)
            if heading_match:
                heading = text
                version_label = " ".join(str(heading_match.group("version") or "").split())
                link = div.find("a", href=True)
                current_pdf_url = absolute_url(str(response.url), link.get("href") if link else None) or ""
                continue
            chapter_match = RHODE_ISLAND_CHAPTER_PATTERN.match(text)
            if chapter_match:
                chapter_no = chapter_match.group(1)
                continue
            if text.startswith("BY "):
                sponsor = text[3:].strip()
                continue
            if text.startswith("ENTITLED,"):
                title = text[len("ENTITLED,") :].strip()
                continue
            action_match = RHODE_ISLAND_STATUS_DATE_PATTERN.match(text)
            if action_match:
                action_text = action_match.group("action").strip()
                actions.append(
                    {
                        "statusDate": parse_rhode_island_date(action_match.group("date")),
                        "statusMessage": action_text,
                        "location": self._action_location(action_text),
                    }
                )

        if not title:
            title = heading or bill_num

        return {
            "heading": heading,
            "version_label": version_label,
            "title": title,
            "sponsor": sponsor,
            "chapter": chapter_no,
            "current_pdf_url": current_pdf_url,
            "actions": actions,
        }

    def _refresh_status_form_state(self) -> dict[str, str]:
        response = self.status_client.get("/")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        state = {
            "__VIEWSTATE": self._required_value(soup, "__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": self._required_value(soup, "__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": self._required_value(soup, "__EVENTVALIDATION"),
        }
        self._status_form_state = state
        return state

    @staticmethod
    def _required_value(soup: BeautifulSoup, element_id: str) -> str:
        element = soup.select_one(f"#{element_id}")
        if element is None or not element.get("value"):
            raise ValueError(f"Rhode Island status form field {element_id} was not found")
        return str(element.get("value") or "")

    @staticmethod
    def _action_location(action_text: str) -> str:
        lowered = str(action_text or "").lower()
        if "governor" in lowered:
            return "Governor"
        if lowered.startswith("house"):
            return "House"
        if lowered.startswith("senate"):
            return "Senate"
        if "committee" in lowered:
            return "Committee"
        return ""

    @staticmethod
    def _build_variant_amendments(bill_num: str, variants: list[dict[str, str]]) -> list[dict[str, str]]:
        amendments: list[dict[str, str]] = []
        if len(variants) <= 1:
            return amendments
        for order, variant in enumerate(variants[1:], start=1):
            label = _variant_label(bill_num, str(variant.get("bill_id") or ""))
            amendments.append(
                {
                    "amendmentNumber": label,
                    "house": _chamber_from_bill_num(bill_num),
                    "order": str(order),
                    "sequence": str(order),
                    "status": "Published version",
                    "sponsor": "",
                    "documentUrl": str(variant.get("html_url") or variant.get("pdf_url") or ""),
                }
            )
        return amendments
