from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_fingerprint, fetch_document_text
from app.settings import Settings


TENNESSEE_BILL_RANGE_PATTERN = re.compile(r"^(?:HB|SB)\d{4}\s*-\s*(?:HB|SB)\d{4}$")
TENNESSEE_BILL_NUMBER_PATTERN = re.compile(r"^(?:HB|SB)\d{4}$")


def tennessee_general_assembly_for_year(year: int) -> int:
    return 111 + ((year - 2019) // 2)


def parse_tennessee_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    month, day, year = (item.zfill(2) for item in parts)
    return f"{year}-{month}-{day}"


class TennesseeApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.tennessee_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        general_assembly = tennessee_general_assembly_for_year(year)
        response = self.client.get("/apps/Indexes/BillsByIndex", params={"ga": general_assembly})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        range_links = [
            absolute_url(self.settings.tennessee_site_base, anchor.get("href"))
            for anchor in soup.find_all("a")
            if TENNESSEE_BILL_RANGE_PATTERN.match(anchor.get_text(" ", strip=True))
        ]

        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for url in range_links:
            if not url:
                continue
            range_response = self.client.get(url)
            range_response.raise_for_status()
            range_soup = BeautifulSoup(range_response.text, "html.parser")
            for anchor in range_soup.find_all("a"):
                bill_num = anchor.get_text(" ", strip=True).replace(" ", "")
                if not TENNESSEE_BILL_NUMBER_PATTERN.match(bill_num):
                    continue
                detail_url = absolute_url(str(range_response.url), anchor.get("href"))
                if not detail_url or bill_num in seen:
                    continue
                seen.add(bill_num)
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": bill_num[:2],
                        "detailPath": detail_url,
                        "generalAssembly": general_assembly,
                    }
                )
        return items

    def fetch_bill_detail(self, year: int, bill_num: str) -> dict[str, Any]:
        general_assembly = tennessee_general_assembly_for_year(year)
        response = self.client.get("/apps/BillInfo/Default", params={"BillNumber": bill_num, "GA": general_assembly})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_heading = soup.find("h2")
        sponsors = [anchor.get_text(" ", strip=True).lstrip("*") for anchor in soup.select("#udpBillInfo a[href*='LegislatorInfo/Member']")]
        summary_tab = soup.find(id="tabpanel-summary")
        bill_summary_html = ""
        bill_summary_text = ""
        fiscal_summary_text = ""
        if summary_tab is not None:
            summary_divs = summary_tab.find_all("div", recursive=False)
            headings = summary_tab.find_all("h3")
            if len(headings) >= 1:
                fiscal_header = headings[0]
                fiscal_parts: list[str] = []
                for sibling in fiscal_header.next_siblings:
                    if getattr(sibling, "name", None) == "h3":
                        break
                    text = getattr(sibling, "get_text", lambda *args, **kwargs: str(sibling))(" ", strip=True)
                    if text:
                        fiscal_parts.append(text)
                fiscal_summary_text = " ".join(fiscal_parts).strip()
            if len(headings) >= 2:
                bill_header = headings[1]
                bill_parts: list[str] = []
                for sibling in bill_header.next_siblings:
                    if getattr(sibling, "name", None) == "h3":
                        break
                    if getattr(sibling, "name", None) == "div":
                        bill_summary_html = str(sibling)
                    text = getattr(sibling, "get_text", lambda *args, **kwargs: str(sibling))(" ", strip=True)
                    if text:
                        bill_parts.append(text)
                bill_summary_text = " ".join(bill_parts).strip()

        caption = soup.find(id="divCaptionText")
        caption_text = caption.get_text(" ", strip=True) if caption is not None else ""
        abstract_block = soup.select_one(".abstract-container")
        abstract_text = abstract_block.get_text(" ", strip=True) if abstract_block is not None else ""
        actions = self._history_rows(soup, bill_num)
        amendments = self._amendment_rows(soup)

        current_bill_link = None
        if bill_heading is not None:
            heading_link = bill_heading.find("a", href=True)
            if heading_link is not None:
                current_bill_link = heading_link.get("href")
        current_bill_url = absolute_url(self.settings.tennessee_bill_base, current_bill_link) if current_bill_link else None
        current_bill_fingerprint = fetch_document_fingerprint(self.client, current_bill_url)

        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}
        signed_date = ""
        last_action_text = str(latest_action.get("statusMessage") or "")
        if "pub. ch." in last_action_text.lower() or "governor signed" in last_action_text.lower():
            signed_date = str(latest_action.get("statusDate") or "")

        catch_title = abstract_text.split(" - ", 1)[0].strip() if " - " in abstract_text else abstract_text

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": catch_title or bill_num,
            "sponsor": ", ".join(dict.fromkeys(name for name in sponsors if name)),
            "billTitle": caption_text or abstract_text or catch_title or bill_num,
            "billStatus": last_action_text,
            "lastAction": last_action_text,
            "lastActionDate": latest_action.get("statusDate") or "",
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": "",
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": current_bill_url,
            "digest": None,
            "summary": str(response.url),
            "currentVersionPath": current_bill_url,
            "currentVersionFingerprint": current_bill_fingerprint,
            "summaryHTML": bill_summary_html,
            "digestHTML": f"<p>{fiscal_summary_text}</p><p>{abstract_text}</p>",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
            "billSummaryText": bill_summary_text,
            "abstractText": abstract_text,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _history_rows(soup: BeautifulSoup, bill_num: str) -> list[dict[str, str]]:
        block = soup.find(id="tabpanel-bill-history")
        if block is None:
            return []
        rows: list[dict[str, str]] = []
        for row in block.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
            if len(cells) != 2:
                continue
            if cells[0].replace(" ", "") == bill_num.replace(" ", "") and cells[1].lower() == "date":
                continue
            rows.append(
                {
                    "statusDate": parse_tennessee_date(cells[1]),
                    "location": "",
                    "statusMessage": cells[0],
                }
            )
        return rows

    def _amendment_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        block = soup.find(id="tabpanel-amendments")
        if block is None:
            return []
        rows: list[dict[str, str]] = []
        for table in block.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                values = [cell.get_text(" ", strip=True) for cell in cells]
                if len(values) >= 2 and values[1].lower() == "amendments":
                    continue
                if not values[0]:
                    continue
                link = row.find("a", href=True)
                rows.append(
                    {
                        "amendmentNumber": values[0],
                        "house": "",
                        "order": "",
                        "sequence": "",
                        "status": values[1] if len(values) > 1 else "",
                        "sponsor": "",
                        "documentUrl": absolute_url(self.settings.tennessee_site_base, link.get("href") if link else None) or "",
                    }
                )
        return rows
