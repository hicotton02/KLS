from __future__ import annotations

import math
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url
from app.settings import Settings


COLORADO_RESULTS_PER_PAGE = 25
COLORADO_COUNT_PATTERN = re.compile(r"Showing\s+\d+\s*-\s*\d+\s+of\s+(\d+)")


def parse_colorado_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    month, day, year = (item.zfill(2) for item in parts)
    return f"{year}-{month}-{day}"


class ColoradoApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.colorado_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_label = f"{year} Regular Session"
        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        first_response = self.client.get(
            "/bills/bill-search",
            params=[
                ("sessions[]", session_label),
                ("measures[]", "Bill"),
                ("sort", "Bill # Ascending"),
                ("page", "1"),
            ],
        )
        first_response.raise_for_status()
        total_pages = self._page_count(first_response.text)

        for page in range(1, total_pages + 1):
            response = first_response if page == 1 else self.client.get(
                "/bills/bill-search",
                params=[
                    ("sessions[]", session_label),
                    ("measures[]", "Bill"),
                    ("sort", "Bill # Ascending"),
                    ("page", str(page)),
                ],
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            rows = soup.select(".bill-result")
            if not rows:
                break
            for row in rows:
                bill_num = row.select_one(".sponsor-bill-or-resolution-tag")
                detail_link = row.select_one(".all-bills-data-heading")
                if bill_num is None or detail_link is None:
                    continue
                normalized_bill_num = bill_num.get_text(" ", strip=True)
                if not normalized_bill_num or normalized_bill_num in seen_bill_nums:
                    continue
                seen_bill_nums.add(normalized_bill_num)
                last_action_block = row.find(string=re.compile(r"LAST ACTION:", re.I))
                last_action_date = ""
                last_action = ""
                if last_action_block is not None:
                    parent = last_action_block.parent
                    if parent is not None:
                        text = parent.get_text(" ", strip=True).replace("LAST ACTION:", "", 1).strip()
                        date_text, _, action_text = text.partition("|")
                        last_action_date = parse_colorado_date(date_text)
                        last_action = action_text.strip() or text
                sponsors = [anchor.get_text(" ", strip=True) for anchor in row.select(".sponsors a")]
                long_title = ""
                long_title_label = row.find("span", string=re.compile(r"LONG TITLE:", re.I))
                if long_title_label is not None and long_title_label.parent is not None:
                    long_title = long_title_label.parent.get_text(" ", strip=True).replace("LONG TITLE:", "", 1).strip()
                items.append(
                    {
                        "billNum": normalized_bill_num,
                        "billType": normalized_bill_num[:2],
                        "catchTitle": detail_link.get_text(" ", strip=True),
                        "billTitle": long_title,
                        "sponsor": ", ".join(sponsors),
                        "billStatus": last_action,
                        "lastAction": last_action,
                        "lastActionDate": last_action_date,
                        "detailPath": detail_link.get("href"),
                    }
                )
        return items

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = soup.select_one(".bill-detail-bill-number-tag")
        catch_title = soup.select_one(".full-bill-topic h1")
        specs = self._specs_map(soup)
        actions = self._history_rows(soup)
        amendments = self._amendment_rows(soup)
        text_versions = self._text_version_rows(soup)
        sponsors = [anchor.get_text(" ", strip=True) for anchor in soup.select("#bill-sponsors a.link-primary")]
        if not sponsors:
            sponsors = [anchor.get_text(" ", strip=True) for anchor in soup.select(".prime-sponsor-block a.ps-link")]
        summary_block = soup.select_one(".bill-summary-content")
        official_summary_html = str(summary_block or "")
        official_summary_text = summary_block.get_text("\n", strip=True) if summary_block else ""

        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}
        current_text = text_versions[0] if text_versions else {}
        introduced_text = text_versions[-1] if text_versions else {}

        signed_date = ""
        for action in actions:
            if "governor signed" in str(action.get("statusMessage") or "").strip().lower():
                signed_date = str(action.get("statusDate") or "")
                break

        return {
            "bill": bill_num.get_text(" ", strip=True) if bill_num else "",
            "billType": (bill_num.get_text(" ", strip=True)[:2] if bill_num else ""),
            "catchTitle": catch_title.get_text(" ", strip=True) if catch_title else "",
            "sponsor": ", ".join(sponsors),
            "billTitle": specs.get("Long Title") or official_summary_text[:200] or (catch_title.get_text(" ", strip=True) if catch_title else ""),
            "billStatus": latest_action.get("statusMessage") or "",
            "lastAction": latest_action.get("statusMessage") or "",
            "lastActionDate": latest_action.get("statusDate") or "",
            "signedDate": signed_date,
            "effectiveDate": specs.get("Effective Date", ""),
            "chapter": specs.get("Session Law Chapter", ""),
            "enrolledNumber": current_text.get("version", ""),
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": introduced_text.get("document_url"),
            "digest": None,
            "summary": absolute_url(str(response.url), detail_path),
            "currentVersionPath": current_text.get("document_url"),
            "currentVersionFingerprint": "|".join(
                item
                for item in [
                    current_text.get("date", ""),
                    current_text.get("version", ""),
                    current_text.get("document_url", ""),
                ]
                if item
            ),
            "summaryHTML": official_summary_html,
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        from app.http_documents import fetch_document_text

        return fetch_document_text(self.client, url)

    @staticmethod
    def _page_count(body: str) -> int:
        match = COLORADO_COUNT_PATTERN.search(body)
        if not match:
            return 1
        total = int(match.group(1))
        return max(1, math.ceil(total / COLORADO_RESULTS_PER_PAGE))

    @staticmethod
    def _specs_map(soup: BeautifulSoup) -> dict[str, str]:
        rows: dict[str, str] = {}
        for row in soup.select(".bill-specs-table tr"):
            cells = row.find_all(["th", "td"])
            if len(cells) != 2:
                continue
            rows[cells[0].get_text(" ", strip=True)] = cells[1].get_text(" ", strip=True)
        return rows

    @staticmethod
    def _history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        block = soup.find(id="bill-activity-bill-history")
        if block is None:
            return []
        rows: list[dict[str, str]] = []
        for row in block.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
            if len(cells) != 3:
                continue
            rows.append(
                {
                    "statusDate": parse_colorado_date(cells[0]),
                    "location": cells[1],
                    "statusMessage": cells[2],
                }
            )
        return rows

    def _text_version_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        block = soup.find(id="bill-activity-bill-text")
        if block is None:
            return []
        rows: list[dict[str, str]] = []
        for row in block.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 3:
                continue
            link = cells[2].find("a")
            rows.append(
                {
                    "date": parse_colorado_date(cells[0].get_text(" ", strip=True)),
                    "version": cells[1].get_text(" ", strip=True),
                    "document_url": absolute_url(self.settings.colorado_site_base, link.get("href") if link else None) or "",
                }
            )
        return rows

    def _amendment_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        block = soup.find(id="bill-activity-amendments")
        if block is None:
            return []
        rows: list[dict[str, str]] = []
        for row in block.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 5:
                continue
            link = cells[4].find("a")
            rows.append(
                {
                    "amendmentNumber": cells[1].get_text(" ", strip=True),
                    "house": "",
                    "order": cells[2].get_text(" ", strip=True),
                    "sequence": parse_colorado_date(cells[0].get_text(" ", strip=True)),
                    "status": cells[3].get_text(" ", strip=True),
                    "sponsor": "",
                    "documentUrl": absolute_url(self.settings.colorado_site_base, link.get("href") if link else None) or "",
                }
            )
        return rows
