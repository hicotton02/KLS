from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


IOWA_BILL_NUMBER_PATTERN = re.compile(r"^(HCR|HJR|HR|HSB|HF|SCR|SJR|SR|SSB|SF)\s*(\d+)$", re.IGNORECASE)
IOWA_GA_RANGE_PATTERN = re.compile(r"(?P<ga>\d+)\s+\((?P<start>\d{2}/\d{2}/(?P<start_year>\d{4}))\s*-\s*(?P<end>\d{2}/\d{2}/(?P<end_year>\d{4}))\)")


def normalize_iowa_bill_number(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = IOWA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return ""
    return f"{match.group(1)}{int(match.group(2))}"


def format_iowa_bill_label(value: str | None) -> str:
    raw = " ".join(str(value or "").split()).strip().upper()
    match = IOWA_BILL_NUMBER_PATTERN.fullmatch(raw)
    if match is None:
        return raw
    return f"{match.group(1)} {int(match.group(2))}"


def parse_iowa_date(value: str | None) -> str:
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


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class IowaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.iowa_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._general_assembly_by_year: dict[int, int] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        general_assembly = self.general_assembly_for_year(year)
        response = self.client.get("/legislation/findLegislation/allbills", params={"ga": str(general_assembly)})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = None
        for candidate in soup.find_all("table"):
            headers = [clean_text(th.get_text(" ", strip=True)) for th in candidate.find_all("th")]
            if headers[:6] == ["Bill_prefix", "Bill", "Bill Title", "Companion", "Similar", "Sponsor"]:
                table = candidate
                break
        if table is None:
            raise ValueError(f"Iowa all-bills table was not found for GA {general_assembly}")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 6:
                continue
            bill_link = cells[1].find("a", href=True)
            if bill_link is None:
                continue
            bill_num = normalize_iowa_bill_number(bill_link.get_text(" ", strip=True))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)

            title = clean_text(cells[2].get_text(" ", strip=True)) or format_iowa_bill_label(bill_num)
            sponsor = clean_text(cells[5].get_text(" ", strip=True))
            detail_path = absolute_url(str(response.url), bill_link.get("href"))
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": sponsor,
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": detail_path,
                    "generalAssembly": general_assembly,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        selected_bill_label = clean_text(
            (
                soup.find("input", attrs={"name": "selectedBill"}) or {}
            ).get("value")
            or ""
        ) or format_iowa_bill_label((item or {}).get("billNum"))
        bill_num = normalize_iowa_bill_number(selected_bill_label or (item or {}).get("billNum"))
        if not bill_num:
            parsed = parse_qs(urlparse(str(response.url)).query).get("ba", [""])[0]
            bill_num = normalize_iowa_bill_number(parsed)
        if not bill_num:
            raise ValueError(f"Iowa bill number could not be parsed from {detail_path}")

        general_assembly = int(
            clean_text(
                (
                    soup.find("input", attrs={"name": "ga"}) or {}
                ).get("value")
                or str((item or {}).get("generalAssembly") or "0")
            )
            or "0"
        )
        title = clean_text(str((item or {}).get("billTitle") or (item or {}).get("catchTitle") or "")) or bill_num
        sponsor = clean_text(str((item or {}).get("sponsor") or ""))

        iframe = soup.find("iframe", id="bbContextDoc")
        current_version_path = absolute_url(str(response.url), iframe.get("src")) if iframe is not None else ""
        pdf_link = ""
        for link in soup.select("li.doc.pdf a[href]"):
            href = absolute_url(str(response.url), link.get("href"))
            if href and "ADA" not in href.upper():
                pdf_link = href
                break
        version_select = soup.find("select", id="billVersions")
        version_label = ""
        if version_select is not None:
            selected_option = version_select.find("option", selected=True) or version_select.find("option")
            version_label = clean_text(selected_option.get_text(" ", strip=True) if selected_option else "")

        actions = self._action_rows(str(response.url), general_assembly, selected_bill_label)
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}

        chapter = self._chapter_from_actions(actions)
        signed_date = self._signed_date_from_actions(actions)
        last_action = clean_text(str(latest_action.get("statusMessage") or ""))
        last_action_date = parse_iowa_date(str(latest_action.get("statusDate") or ""))
        current_path = clean_text(current_version_path or pdf_link)
        fingerprint_parts = [selected_bill_label, version_label, current_path, pdf_link, last_action_date, last_action]

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": version_label,
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": pdf_link or None,
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": current_path or None,
            "currentVersionFingerprint": "|".join(part for part in fingerprint_parts if clean_text(part)),
            "summaryHTML": f"<p>{title}</p>" if title else "",
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
            "generalAssembly": general_assembly,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def general_assembly_for_year(self, year: int) -> int:
        cached = self._general_assembly_by_year.get(year)
        if cached is not None:
            return cached

        response = self.client.get("/legislation/BillBook")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        select = soup.find("select", attrs={"name": "gaList"})
        if select is None:
            raise ValueError("Iowa General Assembly selector was not found")

        selected_value = 0
        for option in select.find_all("option"):
            label = clean_text(option.get_text(" ", strip=True))
            match = IOWA_GA_RANGE_PATTERN.search(label)
            if match is None:
                continue
            ga = int(match.group("ga"))
            if option.has_attr("selected"):
                selected_value = ga
            start_year = int(match.group("start_year"))
            end_year = int(match.group("end_year"))
            if start_year <= year < end_year:
                self._general_assembly_by_year[year] = ga
                return ga

        if selected_value:
            self._general_assembly_by_year[year] = selected_value
            return selected_value
        raise ValueError(f"Iowa General Assembly could not be determined for {year}")

    def _action_rows(self, detail_url: str, general_assembly: int, selected_bill_label: str) -> list[dict[str, str]]:
        if not general_assembly or not selected_bill_label:
            return []

        response = self.client.post(
            detail_url,
            data={
                "ga": str(general_assembly),
                "billName": selected_bill_label,
                "action": "getBillAction",
                "bl": "false",
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table", class_="billActionTable")
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            rows.append(
                {
                    "location": "",
                    "statusDate": parse_iowa_date(cells[0].get_text(" ", strip=True)),
                    "statusMessage": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )
        return rows

    @staticmethod
    def _chapter_from_actions(actions: list[dict[str, str]]) -> str:
        for action in actions:
            match = re.search(r"\bchapter\s+(\d+)\b", str(action.get("statusMessage") or ""), re.IGNORECASE)
            if match is not None:
                return match.group(1)
        return ""

    @staticmethod
    def _signed_date_from_actions(actions: list[dict[str, str]]) -> str:
        for action in actions:
            lowered = str(action.get("statusMessage") or "").lower()
            if "signed by governor" in lowered or "approved by governor" in lowered:
                return parse_iowa_date(str(action.get("statusDate") or ""))
        return ""
