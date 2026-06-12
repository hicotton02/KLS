from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


ALASKA_BILL_NUMBER_PATTERN = re.compile(r"^(?:HB|SB)\d+$", re.IGNORECASE)


def alaska_legislature_for_year(year: int) -> int:
    session_year = year if year % 2 == 0 else year + 1
    return 31 + ((session_year - 2020) // 2)


def parse_alaska_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    month, day, year = (item.zfill(2) for item in parts)
    return f"{year}-{month}-{day}"


class AlaskaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.alaska_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        legislature = alaska_legislature_for_year(year)
        response = self.client.get(
            f"/basis/Bill/Range/{legislature}",
            params={"bill1": "", "bill2": ""},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            bill_num = cells[0].get_text(" ", strip=True).replace(" ", "").upper()
            if not ALASKA_BILL_NUMBER_PATTERN.fullmatch(bill_num) or bill_num in seen_bill_nums:
                continue
            seen_bill_nums.add(bill_num)
            detail_link = cells[0].find("a", href=True)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": cells[1].get_text(" ", strip=True),
                    "billTitle": cells[1].get_text(" ", strip=True),
                    "sponsor": cells[2].get_text(" ", strip=True),
                    "billStatus": cells[4].get_text(" ", strip=True),
                    "lastAction": cells[4].get_text(" ", strip=True),
                    "lastActionDate": parse_alaska_date(cells[5].get_text(" ", strip=True)),
                    "detailPath": absolute_url(str(response.url), detail_link.get("href") if detail_link else None),
                }
            )
        return items

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        info = self._information_map(soup)
        actions = self._action_rows(soup)
        versions = self._version_rows(soup)
        amendments = self._amendment_rows(soup)
        current_version = versions[0] if versions else {}
        introduced_version = versions[-1] if versions else {}
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}

        bill_num = str(info.get("Bill") or "").replace(" ", "").upper()
        short_title = str(info.get("Short Title") or "").strip()
        long_title = str(info.get("Title") or "").strip().strip('"')
        sponsor_text = re.sub(r"\s+", " ", str(info.get("Sponsor(S)") or "")).strip()
        current_status = str(info.get("Current Status") or latest_action.get("statusMessage") or "").strip()
        status_date = parse_alaska_date(str(info.get("Status Date") or latest_action.get("statusDate") or ""))

        signed_date = ""
        chapter = ""
        for action in actions:
            message = str(action.get("statusMessage") or "").lower()
            if "governor signed" in message or "signed into law" in message:
                signed_date = str(action.get("statusDate") or "")
                break
            if "chapter no." in message and not chapter:
                chapter = str(action.get("statusMessage") or "")

        summary_html = ""
        if short_title:
            summary_html += f"<p>{short_title}</p>"
        if long_title:
            summary_html += f"<p>{long_title}</p>"

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": short_title or bill_num,
            "sponsor": sponsor_text,
            "billTitle": long_title or short_title or bill_num,
            "billStatus": current_status,
            "lastAction": str(latest_action.get("statusMessage") or current_status),
            "lastActionDate": str(latest_action.get("statusDate") or status_date),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": str(current_version.get("version") or info.get("Bill Version") or ""),
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": introduced_version.get("document_url"),
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": current_version.get("document_url"),
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    str(current_version.get("version") or ""),
                    str(current_version.get("date") or ""),
                    str(current_version.get("document_url") or ""),
                ]
                if part
            ),
            "summaryHTML": summary_html,
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _information_map(soup: BeautifulSoup) -> dict[str, str]:
        values: dict[str, str] = {}
        for item in soup.select("ul.information li"):
            label = item.find("span")
            value = item.find("strong")
            if label is None or value is None:
                continue
            key = label.get_text(" ", strip=True)
            values[key] = value.get_text(" ", strip=True)
        return values

    @staticmethod
    def _action_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in soup.select("#tab6_4 tr"):
            cells = row.find_all("td")
            if len(cells) != 3:
                continue
            rows.append(
                {
                    "statusDate": parse_alaska_date(cells[0].get_text(" ", strip=True)),
                    "location": cells[1].get_text(" ", strip=True),
                    "statusMessage": cells[2].get_text(" ", strip=True),
                }
            )
        rows.reverse()
        return rows

    def _version_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in soup.select("#tab1_4 tr"):
            cells = row.find_all("td")
            if len(cells) != 6:
                continue
            link = row.find("a", href=True, class_="pdf")
            rows.append(
                {
                    "version": cells[0].get_text(" ", strip=True),
                    "amendedName": cells[1].get_text(" ", strip=True),
                    "date": parse_alaska_date(cells[3].get_text(" ", strip=True)),
                    "document_url": absolute_url(str(self.settings.alaska_site_base), link.get("href") if link else None) or "",
                }
            )
        rows.reverse()
        return rows

    def _amendment_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        seen_amendments: set[str] = set()
        for row in soup.select("#tab3_4 tr"):
            cells = row.find_all("td")
            if len(cells) != 6:
                continue
            amendment_number = cells[0].get_text(" ", strip=True)
            normalized_amendment_number = amendment_number.upper()
            if not amendment_number or normalized_amendment_number in seen_amendments:
                continue
            seen_amendments.add(normalized_amendment_number)
            link = row.find("a", href=True, class_="pdf")
            rows.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": cells[1].get_text(" ", strip=True),
                    "order": cells[4].get_text(" ", strip=True),
                    "sequence": parse_alaska_date(cells[3].get_text(" ", strip=True)),
                    "status": cells[2].get_text(" ", strip=True),
                    "sponsor": "",
                    "documentUrl": absolute_url(str(self.settings.alaska_site_base), link.get("href") if link else None) or "",
                }
            )
        return rows
