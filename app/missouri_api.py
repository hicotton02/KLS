from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


MISSOURI_HOUSE_BILL_PATTERN = re.compile(r"^(HB|HC|HCR|HJR|HR)\d+$", re.IGNORECASE)
MISSOURI_SENATE_BILL_PATTERN = re.compile(r"^(SB|SCR|SJR|SR)\s*(\d+)$", re.IGNORECASE)
MISSOURI_BILL_PATTERN = re.compile(r"^(HB|HC|HCR|HJR|HR|SB|SCR|SJR|SR)\d+$", re.IGNORECASE)
MISSOURI_CHAPTER_PATTERN = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)


def parse_missouri_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_missouri_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper().replace(" ", "")
    if MISSOURI_BILL_PATTERN.fullmatch(raw):
        return raw
    senate_match = MISSOURI_SENATE_BILL_PATTERN.fullmatch(str(value or "").strip().upper())
    if senate_match is not None:
        return f"{senate_match.group(1)}{senate_match.group(2)}"
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def _query_param(url: str | None, name: str) -> str:
    parsed = parse_qs(urlparse(str(url or "")).query)
    values = parsed.get(name) or []
    return str(values[0]).strip() if values else ""


class MissouriApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        headers = {"User-Agent": "keeping-law-simple/1.0"}
        self.house_client = httpx.Client(
            base_url=self.settings.missouri_house_base,
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.senate_client = httpx.Client(
            base_url=self.settings.missouri_senate_base,
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.document_client = httpx.Client(
            headers=headers,
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.house_client.close()
        self.senate_client.close()
        self.document_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        items.extend(self._fetch_house_year_bills(year))
        items.extend(self._fetch_senate_year_bills(year))
        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str) -> dict[str, Any]:
        if "senate.mo.gov" in str(detail_path or "").lower():
            return self._fetch_senate_bill_detail(detail_path)
        return self._fetch_house_bill_detail(detail_path)

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.document_client, url)

    def _fetch_house_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.house_client.get("/billlist.aspx")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        table = soup.find("table", id="reportgrid")
        if table is None:
            raise ValueError("Missouri House bill list table was not found")

        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        candidate_years: set[int] = set()
        rows = table.find_all("tr")
        for index, row in enumerate(rows):
            classes = row.get("class") or []
            if "reportbillinfo" not in classes:
                continue
            link = row.find("a", href=re.compile(r"Bill\.aspx\?bill=", re.IGNORECASE))
            if link is None:
                continue
            link_href = str(link.get("href") or "")
            link_year_raw = _query_param(link_href, "year")
            link_code = _query_param(link_href, "code") or "R"
            try:
                link_year = int(link_year_raw) if link_year_raw else year
            except ValueError:
                continue
            candidate_years.add(link_year)
            if link_year != year:
                continue
            bill_num = normalize_missouri_bill_number(link.get_text(" ", strip=True))
            if not bill_num or bill_num in seen_bill_nums:
                continue
            if not MISSOURI_HOUSE_BILL_PATTERN.fullmatch(bill_num):
                continue
            seen_bill_nums.add(bill_num)

            cells = row.find_all("td", recursive=False)
            sponsor = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
            bill_status = cells[4].get_text(" ", strip=True) if len(cells) > 4 else ""
            next_row = rows[index + 1] if index + 1 < len(rows) else None
            catch_title = ""
            if next_row is not None and "reportlongtitle" in (next_row.get("class") or []):
                long_cells = next_row.find_all("td", recursive=False)
                if len(long_cells) > 1:
                    catch_title = " ".join(long_cells[1].get_text(" ", strip=True).split())
            if not catch_title:
                catch_title = bill_num

            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"^[A-Z]+", bill_num).group(0) if re.match(r"^[A-Z]+", bill_num) else bill_num,
                    "catchTitle": catch_title,
                    "billTitle": catch_title,
                    "sponsor": sponsor,
                    "billStatus": bill_status,
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": f"{self.settings.missouri_house_base}/BillContentMobile.aspx?bill={bill_num}&code={link_code}+&year={link_year}",
                }
            )

        if candidate_years and year not in candidate_years:
            raise ValueError(f"Missouri House listing did not contain requested year {year}")
        return items

    def _fetch_senate_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.senate_client.get("/BillTracking/Bills/BillList", params={"year": str(year), "session": "R"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        cards = soup.select("div.card div.card__body")
        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        candidate_years: set[int] = set()

        for body in cards:
            bill_link = body.find("a", href=re.compile(r"BillInformation\?year=\d+&billid=\d+", re.IGNORECASE))
            if bill_link is None:
                continue
            detail_path = absolute_url(str(response.url), bill_link.get("href")) or ""
            if not detail_path:
                continue
            detail_year_raw = _query_param(detail_path, "year")
            try:
                detail_year = int(detail_year_raw) if detail_year_raw else year
            except ValueError:
                continue
            candidate_years.add(detail_year)
            if detail_year != year:
                continue
            bill_num = normalize_missouri_bill_number(bill_link.get_text(" ", strip=True))
            if not bill_num or bill_num in seen_bill_nums:
                continue
            if not bill_num.startswith("S"):
                continue
            seen_bill_nums.add(bill_num)
            title_tag = body.find("div", class_="bill-title")
            sponsor_link = None
            sponsor_label = body.find("strong", string=lambda value: isinstance(value, str) and value.strip() == "Sponsor:")
            if sponsor_label is not None and sponsor_label.parent is not None:
                sponsor_link = sponsor_label.parent.find("a", href=True)
            sponsor = sponsor_link.get_text(" ", strip=True) if sponsor_link is not None else ""
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"^[A-Z]+", bill_num).group(0) if re.match(r"^[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title_tag.get_text(" ", strip=True) if title_tag is not None else bill_num,
                    "billTitle": title_tag.get_text(" ", strip=True) if title_tag is not None else bill_num,
                    "sponsor": sponsor,
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": detail_path,
                }
            )

        if candidate_years and year not in candidate_years:
            raise ValueError(f"Missouri Senate listing did not contain requested year {year}")
        return items

    def _fetch_house_bill_detail(self, detail_path: str) -> dict[str, Any]:
        detail_response = self.house_client.get(detail_path)
        detail_response.raise_for_status()
        detail_soup = BeautifulSoup(detail_response.text, "html.parser")

        bill_num = self._house_bill_number(detail_soup)
        year = self._year_from_query(detail_response.url, fallback=datetime.utcnow().year)
        document_path = f"/BillDocumentMobile.aspx?bill={bill_num}&code=R+&year={year}"
        actions_path = f"/BillActions.aspx?bill={bill_num}&code=R+&sortDesc=true&year={year}"

        document_response = self.house_client.get(document_path)
        document_response.raise_for_status()
        document_soup = BeautifulSoup(document_response.text, "html.parser")

        actions_response = self.house_client.get(actions_path)
        actions_response.raise_for_status()
        actions_soup = BeautifulSoup(actions_response.text, "html.parser")

        description = self._house_description(detail_soup)
        sponsor = self._house_text_after_label(detail_soup, "Sponsor:")
        effective_date = parse_missouri_date(self._house_text_after_label(detail_soup, "Proposed Effective Date:"))
        lr_number = self._house_text_after_label(detail_soup, "LR Number:")
        last_action_full = self._house_text_after_label(detail_soup, "Last Action:")
        bill_string = self._house_text_after_label(detail_soup, "Bill String:")

        actions = self._house_action_rows(actions_soup)
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": last_action_full, "location": ""}

        versions = self._house_section_links(document_soup, "Bill Text")
        summaries = self._house_section_links(document_soup, "Bill Summary")
        fiscal_notes = self._house_section_links(document_soup, "Fiscal Note")
        amendments = self._house_amendments(document_soup)

        current_version = versions[-1] if versions else {}
        introduced_version = versions[0] if versions else {}
        current_summary = summaries[-1] if summaries else {}
        current_fiscal = fiscal_notes[-1] if fiscal_notes else {}

        signed_date = ""
        chapter_no = ""
        for action in actions:
            action_text = str(action.get("statusMessage") or "")
            lowered = action_text.lower()
            if not signed_date and "approved by governor" in lowered:
                signed_date = str(action.get("statusDate") or "")
            if not chapter_no:
                chapter_match = MISSOURI_CHAPTER_PATTERN.search(action_text)
                if chapter_match is not None:
                    chapter_no = chapter_match.group(1)
            if signed_date and chapter_no:
                break

        return {
            "bill": bill_num,
            "billType": re.match(r"^[A-Z]+", bill_num).group(0) if re.match(r"^[A-Z]+", bill_num) else bill_num,
            "catchTitle": description or bill_num,
            "sponsor": sponsor,
            "billTitle": description or bill_num,
            "billStatus": last_action_full or str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter_no,
            "enrolledNumber": str(current_version.get("code") or lr_number or bill_string),
            "sponsorStringHouse": sponsor,
            "sponsorStringSenate": None,
            "introduced": str(introduced_version.get("document_url") or "") or None,
            "digest": str(current_fiscal.get("document_url") or "") or None,
            "summary": str(current_summary.get("document_url") or "") or None,
            "currentVersionPath": str(current_version.get("document_url") or "") or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    str(current_version.get("code") or ""),
                    str(current_version.get("label") or ""),
                    str(current_version.get("document_url") or ""),
                    str(latest_action.get("statusDate") or ""),
                    str(latest_action.get("statusMessage") or ""),
                ]
                if part
            ),
            "summaryHTML": self._paragraph_html(description or bill_num),
            "digestHTML": self._paragraph_html(str(current_fiscal.get("code") or "")) if current_fiscal else "",
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(detail_response.url),
        }

    def _fetch_senate_bill_detail(self, detail_path: str) -> dict[str, Any]:
        detail_response = self.senate_client.get(detail_path)
        detail_response.raise_for_status()
        detail_soup = BeautifulSoup(detail_response.text, "html.parser")

        bill_num = self._senate_bill_number(detail_soup)
        year = self._year_from_query(detail_response.url, fallback=datetime.utcnow().year)
        bill_id = self._bill_id_from_query(detail_response.url)
        bill_prefix, bill_suffix = self._split_senate_bill(bill_num)

        summary_html = self._senate_handler_html(year, bill_id, "Summaries", bill_prefix=bill_prefix, bill_suffix=bill_suffix)
        actions_html = self._senate_handler_html(year, bill_id, "Actions", bill_prefix=bill_prefix, bill_suffix=bill_suffix)
        bill_text_html = self._senate_handler_html(year, bill_id, "BillText")
        amendments_html = self._senate_handler_html(year, bill_id, "Amendments")
        fiscal_notes_html = self._senate_handler_html(year, bill_id, "FiscalNotes", session_type="R")

        actions = self._senate_action_rows(actions_html)
        versions = self._senate_bill_text_versions(bill_text_html)
        amendments = self._senate_amendments(amendments_html)
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": "", "location": ""}
        current_version = versions[0] if versions else {}
        introduced_version = versions[-1] if versions else {}

        sponsor = self._senate_detail_value(detail_soup, "Sponsor")
        house_handler = self._senate_detail_value(detail_soup, "House Handler")
        effective_date = parse_missouri_date(self._senate_detail_value(detail_soup, "Effective Date"))
        lr_number = self._senate_detail_value(detail_soup, "LR Number")
        title_value = self._senate_detail_value(detail_soup, "Title")
        committee = self._senate_detail_value(detail_soup, "Committee")
        current_status = self._senate_detail_value(detail_soup, "Current Status")
        description = self._senate_description(detail_soup)

        signed_date = ""
        chapter_no = ""
        for action in actions:
            action_text = str(action.get("statusMessage") or "")
            lowered = action_text.lower()
            if not signed_date and "approved by governor" in lowered:
                signed_date = str(action.get("statusDate") or "")
            if not chapter_no:
                chapter_match = MISSOURI_CHAPTER_PATTERN.search(action_text)
                if chapter_match is not None:
                    chapter_no = chapter_match.group(1)
            if signed_date and chapter_no:
                break

        sponsor_text = sponsor
        if house_handler:
            sponsor_text = f"{sponsor_text}; House handler: {house_handler}" if sponsor_text else house_handler

        digest_html = self._paragraph_html(committee) if committee else ""

        return {
            "bill": bill_num,
            "billType": bill_prefix,
            "catchTitle": description or bill_num,
            "sponsor": sponsor_text,
            "billTitle": description or bill_num,
            "billStatus": current_status or str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter_no,
            "enrolledNumber": str(current_version.get("code") or lr_number or title_value),
            "sponsorStringHouse": None,
            "sponsorStringSenate": sponsor,
            "introduced": str(introduced_version.get("document_url") or "") or None,
            "digest": None,
            "summary": str(detail_response.url),
            "currentVersionPath": str(current_version.get("document_url") or "") or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    str(current_version.get("code") or ""),
                    str(current_version.get("label") or ""),
                    str(current_version.get("document_url") or ""),
                    str(latest_action.get("statusDate") or ""),
                    str(latest_action.get("statusMessage") or ""),
                ]
                if part
            ),
            "summaryHTML": summary_html,
            "digestHTML": digest_html,
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(detail_response.url),
        }

    def _senate_handler_html(
        self,
        year: int,
        bill_id: int,
        handler: str,
        *,
        bill_prefix: str | None = None,
        bill_suffix: str | None = None,
        session_type: str | None = None,
    ) -> str:
        params: dict[str, str] = {"year": str(year), "billId": str(bill_id), "handler": handler}
        if bill_prefix:
            params["billPrefix"] = bill_prefix
        if bill_suffix:
            params["billSuffix"] = bill_suffix
        if session_type:
            params["sessionType"] = session_type
        response = self.senate_client.get("/BillTracking/Bills/BillInformation", params=params)
        response.raise_for_status()
        return response.text

    @staticmethod
    def _house_bill_number(soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        if heading is None:
            raise ValueError("Missouri House bill number could not be parsed")
        bill_num = normalize_missouri_bill_number(heading.get_text(" ", strip=True))
        if not bill_num:
            raise ValueError("Missouri House bill number could not be parsed")
        return bill_num

    @staticmethod
    def _senate_bill_number(soup: BeautifulSoup) -> str:
        header = soup.find("div", class_="main-header-text")
        if header is None:
            raise ValueError("Missouri Senate bill number could not be parsed")
        match = re.match(r"([A-Z]+)\s*(\d+)", header.get_text(" ", strip=True).upper())
        if match is None:
            raise ValueError("Missouri Senate bill number could not be parsed")
        return f"{match.group(1)}{match.group(2)}"

    @staticmethod
    def _year_from_query(url: httpx.URL, *, fallback: int) -> int:
        parsed = parse_qs(url.query.decode("utf-8"))
        values = parsed.get("year") or []
        if values:
            try:
                return int(values[0])
            except ValueError:
                pass
        return fallback

    @staticmethod
    def _bill_id_from_query(url: httpx.URL) -> int:
        parsed = parse_qs(url.query.decode("utf-8"))
        values = parsed.get("billid") or parsed.get("billId") or []
        if not values:
            raise ValueError("Missouri Senate bill id could not be parsed")
        return int(values[0])

    @staticmethod
    def _split_senate_bill(bill_num: str) -> tuple[str, str]:
        match = re.fullmatch(r"([A-Z]+)(\d+)", bill_num)
        if match is None:
            raise ValueError(f"Invalid Missouri Senate bill number: {bill_num}")
        return match.group(1), match.group(2)

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _house_description(soup: BeautifulSoup) -> str:
        tag = soup.find("div", class_="BillDescription")
        return "" if tag is None else " ".join(tag.get_text(" ", strip=True).split())

    @staticmethod
    def _house_text_after_label(soup: BeautifulSoup, label: str) -> str:
        heading = soup.find("th", string=lambda value: isinstance(value, str) and value.strip() == label)
        if heading is None:
            return ""
        value_cell = heading.find_next_sibling("td")
        if value_cell is None:
            return ""
        return " ".join(value_cell.get_text(" ", strip=True).split())

    @classmethod
    def _house_section_links(cls, soup: BeautifulSoup, heading_text: str) -> list[dict[str, str]]:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and value.strip() == heading_text)
        if heading is None:
            return []

        rows: list[dict[str, str]] = []
        for sibling in heading.parent.find_next_siblings("div", class_="DocRow"):
            previous_header = sibling.find_previous_sibling("div", class_="DocHeaderRow")
            if previous_header is None or previous_header.find("h2") != heading:
                break
            info_cell = sibling.find("div", class_="DocInfoCell")
            if info_cell is None:
                continue
            code_tag = info_cell.find("div", class_="textLR")
            link_tag = info_cell.find("a", href=True)
            label_tag = info_cell.find("div", class_="textType")
            code = code_tag.get_text(" ", strip=True) if code_tag is not None else ""
            label = label_tag.get_text(" ", strip=True) if label_tag is not None else ""
            document_url = absolute_url(str(soup.base.get("href") if soup.base else ""), link_tag.get("href")) if link_tag is not None else ""
            if not document_url and link_tag is not None:
                document_url = link_tag.get("href") or ""
            rows.append({"code": code, "label": label, "document_url": document_url})
        return rows

    @staticmethod
    def _house_action_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find("table", id="actionTable")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            date_text = cells[0].get_text(" ", strip=True)
            action_text = cells[2].get_text(" ", strip=True)
            if not date_text or not action_text:
                continue
            rows.append(
                {
                    "statusDate": parse_missouri_date(date_text),
                    "statusMessage": action_text,
                    "location": "",
                }
            )
        return rows

    @staticmethod
    def _house_amendments(soup: BeautifulSoup) -> list[dict[str, str]]:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and value.strip() == "Amendments")
        if heading is None:
            return []
        rows: list[dict[str, str]] = []
        seen_numbers: set[str] = set()
        for sibling in heading.parent.find_next_siblings("div", class_="DocRow"):
            previous_header = sibling.find_previous_sibling("div", class_="DocHeaderRow")
            if previous_header is None or previous_header.find("h2") != heading:
                break
            info_cell = sibling.find("div", class_="DocInfoCell")
            if info_cell is None:
                continue
            document_link = info_cell.find("a", href=True)
            if document_link is None:
                continue
            sponsor_links = info_cell.find_all("a", href=True)
            sponsor = sponsor_links[-1].get_text(" ", strip=True) if sponsor_links else ""
            label_spans = info_cell.find_all("span")
            short_label = ""
            for span in label_spans:
                text = " ".join(span.get_text(" ", strip=True).split())
                if text.startswith("HA") or text.startswith("SA") or text.startswith("SS") or text.startswith("CCS") or text.startswith("CCR"):
                    short_label = text
                    break
            amendment_number = short_label or document_link.get_text(" ", strip=True)
            amendment_number = " ".join(amendment_number.split())
            if not amendment_number or amendment_number in seen_numbers:
                continue
            seen_numbers.add(amendment_number)
            status_icon = sibling.find("img")
            status = status_icon.get("alt", "").strip() if status_icon is not None else ""
            order = document_link.get_text(" ", strip=True)
            rows.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": "H",
                    "order": order,
                    "sequence": order,
                    "status": status,
                    "sponsor": sponsor,
                    "documentUrl": document_link.get("href") or "",
                }
            )
        return rows

    @staticmethod
    def _senate_detail_value(soup: BeautifulSoup, label: str) -> str:
        label_tag = soup.find("span", class_="detail-grid__label", string=lambda value: isinstance(value, str) and value.strip() == label)
        if label_tag is None:
            return ""
        item = label_tag.find_parent("div", class_="detail-grid__item")
        if item is None:
            return ""
        value_tag = item.find("div", class_="detail-grid__value")
        if value_tag is None:
            return ""
        return " ".join(value_tag.get_text(" ", strip=True).split())

    @staticmethod
    def _senate_description(soup: BeautifulSoup) -> str:
        description = soup.find("div", class_="main-header-description")
        return "" if description is None else " ".join(description.get_text(" ", strip=True).split())

    @staticmethod
    def _senate_action_rows(html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, str]] = []
        for row in soup.select("tbody tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_text = cells[0].get_text(" ", strip=True)
            action_text = cells[1].get_text(" ", strip=True)
            journal_text = cells[2].get_text(" ", strip=True) if len(cells) > 2 else ""
            if not date_text or not action_text:
                continue
            rows.append(
                {
                    "statusDate": parse_missouri_date(date_text),
                    "statusMessage": action_text,
                    "location": journal_text,
                }
            )
        return rows

    @staticmethod
    def _senate_bill_text_versions(html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, str]] = []
        for link in soup.find_all("a", href=True):
            label_text = " ".join(link.get_text(" ", strip=True).split())
            if " - " not in label_text:
                continue
            code, label = label_text.split(" - ", 1)
            rows.append(
                {
                    "code": code.strip(),
                    "label": label.strip(),
                    "document_url": link.get("href") or "",
                }
            )
        return rows

    @staticmethod
    def _senate_amendments(html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        rows: list[dict[str, str]] = []
        seen_numbers: set[str] = set()
        for link in soup.find_all("a", href=True):
            if "AmendmentPdf" not in str(link.get("href") or ""):
                continue
            title = " ".join(str(link.get("title") or "").split())
            code = ""
            label = ""
            if " - " in title:
                code, label = title.split(" - ", 1)
            left_text = " ".join(link.find("span").get_text(" ", strip=True).split()) if link.find("span") else ""
            chip = link.find_all("span")[-1].get_text(" ", strip=True) if link.find_all("span") else ""
            chip = " - ".join(chip.split(" - ")[1:]).strip() if " - " in chip else chip.strip()
            sponsor_match = re.search(r"\(([^()]+)\)\s*--", left_text)
            sponsor = sponsor_match.group(1).strip() if sponsor_match is not None else ""
            reading_order = re.sub(r"\s*--\([^()]+\)\s*$", "", left_text).strip()
            amendment_number = label.strip() or code.strip() or left_text.strip()
            if not amendment_number or amendment_number in seen_numbers:
                continue
            seen_numbers.add(amendment_number)
            rows.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": "S",
                    "order": reading_order,
                    "sequence": code.strip(),
                    "status": chip or "Filed",
                    "sponsor": sponsor,
                    "documentUrl": link.get("href") or "",
                }
            )
        return rows
