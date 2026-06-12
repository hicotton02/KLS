from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


KANSAS_BILL_NUMBER_PATTERN = re.compile(r"^(?:HB|SB)\d+$", re.IGNORECASE)
KANSAS_DETAIL_PATH_PATTERN = re.compile(r"/li/(b\d+_\d+)/measures/((?:hb|sb)\d+)/", re.IGNORECASE)
KANSAS_CURRENT_DETAIL_PATH_PATTERN = re.compile(r"/(b\d+_\d+)/bills/((?:HB|SB)\d+)/", re.IGNORECASE)
KANSAS_PAGE_PATTERN = re.compile(r"[?&]page=(\d+)")


def kansas_session_slug(year: int) -> str:
    start_year = year if year % 2 == 1 else year - 1
    return f"b{start_year}_{(start_year + 1) % 100:02d}"


def parse_kansas_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%a, %b %d, %Y", "%A, %B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


class KansasApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.kansas_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_slug = kansas_session_slug(year)
        items = self._fetch_current_year_bills(session_slug)
        if items:
            return items

        response = self.client.get(f"/li/{session_slug}/measures/bills/")
        response.raise_for_status()
        return self._parse_legacy_bill_listing(response.text)

    def _fetch_current_year_bills(self, session_slug: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        page = 1
        last_page = 1

        while page <= last_page:
            response = self.client.get(
                f"/{session_slug}/measures/fragment/",
                params=[
                    ("types", "bill"),
                    ("chambers", "House"),
                    ("chambers", "Senate"),
                    ("per_page", "20"),
                    ("page", str(page)),
                ],
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            last_page = max(last_page, self._last_page_number(soup))

            for row in soup.select("table.site-table tr"):
                cells = row.find_all("td")
                if len(cells) < 2:
                    continue
                link = row.find("a", href=True)
                if link is None:
                    continue
                href = str(link.get("href") or "")
                match = KANSAS_CURRENT_DETAIL_PATH_PATTERN.search(href)
                if not match:
                    continue
                bill_num = match.group(2).upper()
                if not KANSAS_BILL_NUMBER_PATTERN.fullmatch(bill_num) or bill_num in seen_bill_nums:
                    continue
                seen_bill_nums.add(bill_num)

                title = cells[1].get_text(" ", strip=True)
                status = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": bill_num[:2],
                        "catchTitle": title or bill_num,
                        "billTitle": title or bill_num,
                        "sponsor": "",
                        "billStatus": status,
                        "lastAction": status,
                        "lastActionDate": "",
                        "detailPath": absolute_url(self.settings.kansas_site_base, href),
                    }
                )

            page += 1

        return sorted(items, key=lambda item: str(item["billNum"]))

    @staticmethod
    def _last_page_number(soup: BeautifulSoup) -> int:
        last_page = 1
        for link in soup.find_all("a", href=True):
            match = KANSAS_PAGE_PATTERN.search(str(link.get("href") or ""))
            if match:
                last_page = max(last_page, int(match.group(1)))
        return last_page

    def _parse_legacy_bill_listing(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")

        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        for link in soup.select("a.module-title[href]"):
            href = str(link.get("href") or "")
            match = KANSAS_DETAIL_PATH_PATTERN.search(href)
            if not match:
                continue
            bill_num = match.group(2).upper()
            if not KANSAS_BILL_NUMBER_PATTERN.fullmatch(bill_num) or bill_num in seen_bill_nums:
                continue
            seen_bill_nums.add(bill_num)

            text = link.get_text(" ", strip=True)
            prefix = f"{bill_num} -"
            catch_title = text
            if text.upper().startswith(prefix.upper()):
                catch_title = text[len(prefix):].strip()

            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": catch_title or bill_num,
                    "billTitle": catch_title or bill_num,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(self.settings.kansas_site_base, href),
                }
            )

        return sorted(items, key=lambda item: str(item["billNum"]))

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(soup, str(response.url))
        short_title = self._short_title(soup)
        versions = self._version_rows(soup)
        actions = self._history_rows(soup)
        if not actions:
            actions = self._fetch_current_history_rows(bill_num, str(response.url))
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}
        current_version = versions[0] if versions else {}
        introduced_version = versions[-1] if versions else {}

        sponsor_parts = [
            self._section_value(soup, "Current Sponsor"),
            self._section_value(soup, "Original Sponsor"),
            self._section_value(soup, "Requested for introduction by"),
        ]
        sponsor = ", ".join(dict.fromkeys(part for part in sponsor_parts if part))

        signed_date = ""
        for action in actions:
            if "approved by governor" in str(action.get("statusMessage") or "").lower():
                signed_date = str(action.get("statusDate") or "")
                break

        digest_url = (
            str(current_version.get("summary_url") or "")
            or str(current_version.get("supplemental_note_url") or "")
            or str(introduced_version.get("summary_url") or "")
            or str(introduced_version.get("supplemental_note_url") or "")
            or None
        )

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": short_title or bill_num,
            "sponsor": sponsor,
            "billTitle": short_title or bill_num,
            "billStatus": str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": str(current_version.get("version") or ""),
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": str(introduced_version.get("document_url") or "") or None,
            "digest": digest_url,
            "summary": str(response.url),
            "currentVersionPath": str(current_version.get("document_url") or "") or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    str(current_version.get("version") or ""),
                    str(current_version.get("document_url") or ""),
                    str(current_version.get("summary_url") or ""),
                    str(current_version.get("supplemental_note_url") or ""),
                ]
                if part
            ),
            "summaryHTML": self._paragraph_html(short_title or bill_num),
            "digestHTML": "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _bill_number(soup: BeautifulSoup, detail_url: str) -> str:
        for tag in soup.find_all(["h1", "h2", "h3", "title"]):
            text = tag.get_text(" ", strip=True)
            match = re.search(r"\b(HB|SB)\s*(\d+)\b", text, re.IGNORECASE)
            if match:
                return f"{match.group(1).upper()}{match.group(2)}"
        match = KANSAS_DETAIL_PATH_PATTERN.search(detail_url)
        if match:
            return match.group(2).upper()
        match = KANSAS_CURRENT_DETAIL_PATH_PATTERN.search(detail_url)
        if match:
            return match.group(2).upper()
        raise ValueError(f"Kansas bill number could not be parsed from {detail_url}")

    def _short_title(self, soup: BeautifulSoup) -> str:
        current_title = soup.select_one(".bill-hero-sub")
        if current_title is not None:
            return current_title.get_text(" ", strip=True)
        return self._heading_paragraph(soup, "Short Title")

    @staticmethod
    def _heading_paragraph(soup: BeautifulSoup, heading_text: str) -> str:
        heading = soup.find(lambda tag: tag.name in {"h2", "h3", "div"} and tag.get_text(" ", strip=True) == heading_text)
        if heading is None:
            return ""
        sibling = heading.find_next_sibling()
        while sibling is not None:
            if sibling.name == "p":
                return sibling.get_text(" ", strip=True)
            if sibling.name in {"h2", "h3", "div"}:
                break
            sibling = sibling.find_next_sibling()
        return ""

    @staticmethod
    def _section_value(soup: BeautifulSoup, label: str) -> str:
        heading = soup.find(lambda tag: tag.name == "div" and tag.get_text(" ", strip=True) == label)
        if heading is not None:
            sibling = heading.find_next_sibling("div")
            if sibling is not None:
                return sibling.get_text(" ", strip=True)

        current_heading = soup.find(lambda tag: tag.name == "h3" and tag.get_text(" ", strip=True) == label)
        if current_heading is None:
            return ""
        container = current_heading.find_parent(class_="bill-card")
        if container is None:
            return ""
        values = [
            text
            for text in container.stripped_strings
            if text != label and text not in {"expand_more", "picture_as_pdf"}
        ]
        return " ".join(values)

    def _version_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        current_rows = self._current_version_rows(soup)
        if current_rows:
            return current_rows

        table = None
        for candidate in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True) for th in candidate.find_all("th")]
            if "Version" in headers:
                table = candidate
                break
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            rows.append(
                {
                    "version": cells[0].get_text(" ", strip=True),
                    "document_url": self._first_link_url(cells[1]),
                    "supplemental_note_url": self._first_link_url(cells[2]) if len(cells) > 2 else "",
                    "fiscal_note_url": self._first_link_url(cells[3]) if len(cells) > 3 else "",
                    "summary_url": self._first_link_url(cells[4]) if len(cells) > 4 else "",
                }
            )
        return rows

    def _current_version_rows(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for row in soup.select(".version-row"):
            label = row.select_one(".label")
            version = label.get_text(" ", strip=True) if label is not None else ""
            document_url = ""
            supplemental_note_url = ""
            fiscal_note_url = ""

            for link in row.find_all("a", href=True):
                classes = set(link.get("class") or [])
                if "version-pdf-link" not in classes:
                    continue
                href = absolute_url(self.settings.kansas_site_base, str(link.get("href") or "")) or ""
                aria = str(link.get("aria-label") or "").lower()
                if "supplemental note" in aria:
                    supplemental_note_url = href
                elif "fiscal note" in aria:
                    fiscal_note_url = href
                elif not document_url:
                    document_url = href

            if version or document_url or supplemental_note_url or fiscal_note_url:
                rows.append(
                    {
                        "version": version,
                        "document_url": document_url,
                        "supplemental_note_url": supplemental_note_url,
                        "fiscal_note_url": fiscal_note_url,
                        "summary_url": "",
                    }
                )
        return rows

    def _first_link_url(self, cell: Any) -> str:
        link = cell.find("a", href=True) if cell is not None else None
        return absolute_url(self.settings.kansas_site_base, link.get("href") if link else None) or ""

    @staticmethod
    def _history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = None
        for candidate in soup.find_all("table"):
            headers = [th.get_text(" ", strip=True) for th in candidate.find_all("th")]
            if headers[:3] == ["Date", "Chamber", "Status"] or headers[:3] == ["Date", "Chamber", "Message"]:
                table = candidate
                break
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            rows.append(
                {
                    "statusDate": parse_kansas_date(cells[0].get_text(" ", strip=True)),
                    "location": cells[1].get_text(" ", strip=True),
                    "statusMessage": cells[2].get_text(" ", strip=True),
                }
            )
        return rows

    def _fetch_current_history_rows(self, bill_num: str, detail_url: str) -> list[dict[str, str]]:
        match = KANSAS_CURRENT_DETAIL_PATH_PATTERN.search(detail_url)
        if not match:
            return []
        session_slug = match.group(1)
        response = self.client.get(f"/{session_slug}/bills/{bill_num}/history/", params={"per_page": "100"})
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return self._history_rows(BeautifulSoup(response.text, "html.parser"))
