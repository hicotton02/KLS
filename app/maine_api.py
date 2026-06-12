from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


MAINE_BILL_NUMBER_PATTERN = re.compile(r"^LD\s*0*(\d+)$", re.IGNORECASE)
MAINE_PAPER_DISPLAY_PATTERN = re.compile(r"^([A-Z]{2})\s*0*(\d+)$", re.IGNORECASE)
MAINE_DIRECTORY_ROW_PATTERN = re.compile(r"LD\s+(\d+),\s*([A-Z]{2})\s*(\d+)", re.IGNORECASE)
MAINE_CHAPTER_PATTERN = re.compile(r"\bChapter\s+(\d+)\b", re.IGNORECASE)
MAINE_DATE_PREFIX_PATTERN = re.compile(r"^(?P<date>\d{1,2}/\d{1,2}/\d{4})\s*-\s*(?P<text>.+)$")
MAINE_COMMITTEE_ACTION_PATTERN = re.compile(
    r"^(?P<action>.+?),\s*(?P<date>[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})(?:,\s*(?P<result>.+))?$"
)


def normalize_maine_bill_number(value: str | None) -> str:
    match = MAINE_BILL_NUMBER_PATTERN.fullmatch(clean_text(value))
    if match is None:
        return ""
    return f"LD{int(match.group(1))}"


def normalize_maine_paper_number(value: str | None) -> str:
    match = MAINE_PAPER_DISPLAY_PATTERN.fullmatch(clean_text(value))
    if match is None:
        return clean_text(value).upper().replace(" ", "")
    return f"{match.group(1).upper()}{int(match.group(2)):04d}"


def parse_maine_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = MAINE_BILL_NUMBER_PATTERN.fullmatch(str(bill_num or ""))
    if match is None:
        return (str(bill_num or "").upper(), 0)
    return ("LD", int(match.group(1)))


def _session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 1 else value - 1


def maine_legislature_number(year: int) -> int:
    session_start = _session_start_year(year)
    return 132 + ((session_start - 2025) // 2)


def maine_session_id(year: int) -> int:
    return maine_legislature_number(year) - 116


class MaineApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.maine_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        legislature = maine_legislature_number(year)
        range_starts = self._directory_ranges(legislature)
        items: dict[str, dict[str, Any]] = {}

        for range_start in range_starts:
            response = self.client.get(
                "/legis/bills/billdirectory_ps.asp",
                params={"snum": legislature, "ldFrom": range_start},
            )
            response.raise_for_status()
            for item in self._parse_directory_page(response.text):
                item.setdefault("year", _session_start_year(year))
                items[item["billNum"]] = item

        return sorted(items.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        year = int((item or {}).get("year") or 0) if item else 0
        legislature = self._legislature_from_path(detail_path, year)
        paper = first_non_empty(
            self._paper_from_path(detail_path),
            normalize_maine_paper_number((item or {}).get("paper")),
        )
        bill_num = first_non_empty(
            normalize_maine_bill_number((item or {}).get("billNum")),
            self._bill_number_from_display(soup),
        )
        if not bill_num:
            raise ValueError(f"Maine bill number could not be determined from {detail_path}")

        summary_url = self._summary_url(soup, detail_path, year, legislature, paper)
        summary_html = ""
        summary_soup = None
        if summary_url:
            try:
                summary_response = self.client.get(summary_url)
                summary_response.raise_for_status()
                summary_html = summary_response.text
                summary_soup = BeautifulSoup(summary_html, "html.parser")
            except Exception:  # noqa: BLE001
                summary_html = ""
                summary_soup = None

        title = first_non_empty(
            self._title_from_display(soup),
            self._title_from_summary(summary_soup) if summary_soup is not None else "",
            clean_text((item or {}).get("catchTitle")),
            bill_num,
        )
        sponsor = first_non_empty(
            self._sponsor_from_summary(summary_soup) if summary_soup is not None else "",
            clean_text((item or {}).get("sponsor")),
        )
        summary_fields = self._summary_fields(summary_soup) if summary_soup is not None else {}
        committee_info = self._committee_info(soup)
        actions = self._actions_from_display_and_summary(soup, summary_fields, committee_info)
        house_action_text = self._summary_action_text(summary_fields.get("Last House Action"))
        senate_action_text = self._summary_action_text(summary_fields.get("Last Senate Action"))

        last_action = ""
        last_action_date = ""
        if actions:
            last_action = clean_text(actions[-1].get("statusMessage"))
            last_action_date = clean_text(actions[-1].get("statusDate"))

        governor_action = clean_text(summary_fields.get("Governor Action"))
        final_date = parse_maine_date(summary_fields.get("Date"))
        chapter = clean_text(summary_fields.get("Chapter")) or self._chapter_from_display(soup)
        final_law_type = clean_text(summary_fields.get("Final Law Type"))
        if governor_action and final_date:
            last_action = governor_action
            last_action_date = final_date

        bill_status = first_non_empty(
            governor_action,
            senate_action_text,
            house_action_text,
            committee_info.get("latest_action_text"),
            committee_info.get("latest_report_text"),
            final_law_type,
            last_action,
        )

        introduced_path, current_version_path, digest_path, amendments = self._documents_from_display(
            soup, str(response.url), paper
        )
        signed_date = final_date if chapter else ""
        committee_name = committee_info.get("committee")

        return {
            "bill": bill_num,
            "billType": "LD",
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": bill_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": final_law_type or clean_text(summary_fields.get("Final Law Type")),
            "sponsorStringHouse": sponsor if paper.startswith("HP") else None,
            "sponsorStringSenate": sponsor if paper.startswith("SP") else None,
            "introduced": introduced_path or None,
            "digest": digest_path or None,
            "summary": summary_url or str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    introduced_path,
                    current_version_path,
                    digest_path,
                    bill_status,
                    last_action,
                    last_action_date,
                    chapter,
                    final_law_type,
                    committee_name,
                    str(len(amendments)),
                )
                if part
            ),
            "summaryHTML": self._summary_html(title, sponsor, committee_name, summary_fields, committee_info),
            "digestHTML": self._actions_html(actions),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _directory_ranges(self, legislature: int) -> list[int]:
        response = self.client.get(
            "/legis/bills/billdirectory_ps.asp",
            params={"snum": legislature, "ldFrom": 1},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        range_starts = {1}
        for anchor in soup.select("ul.paperList a[href]"):
            match = re.search(r"[?&]ldFrom=(\d+)", str(anchor.get("href") or ""), re.IGNORECASE)
            if match is not None:
                range_starts.add(int(match.group(1)))
        return sorted(range_starts)

    def _parse_directory_page(self, html_text: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        rows = soup.select("#search-results tr")
        items: list[dict[str, Any]] = []

        index = 0
        while index < len(rows):
            row = rows[index]
            numbers_cell = row.find("td", class_="RecordNumbers")
            title_cell = row.find("td", class_="RecordTitle")
            if numbers_cell is None or title_cell is None:
                index += 1
                continue

            detail_row = rows[index + 1] if index + 1 < len(rows) else None
            detail_links = detail_row.find("td", class_="RecordLinks") if detail_row is not None else None
            numbers_text = clean_text(numbers_cell.get_text(" ", strip=True))
            match = MAINE_DIRECTORY_ROW_PATTERN.search(numbers_text)
            if match is None:
                index += 2
                continue

            ld_number = int(match.group(1))
            bill_num = f"LD{ld_number}"
            detail_path = ""
            paper_code = ""
            if detail_links is not None:
                for anchor in detail_links.find_all("a", href=True):
                    label = clean_text(anchor.get_text(" ", strip=True))
                    href = absolute_url(self.settings.maine_site_base, anchor.get("href"))
                    if not detail_path and "Bill & Fiscal Information" in label:
                        detail_path = href or ""
                    if not paper_code:
                        paper_code = self._paper_from_path(href)
            if not paper_code:
                paper_code = normalize_maine_paper_number(f"{match.group(2)} {match.group(3)}")

            items.append(
                {
                    "billNum": bill_num,
                    "billType": "LD",
                    "catchTitle": clean_text(title_cell.get_text(" ", strip=True)) or bill_num,
                    "billTitle": clean_text(title_cell.get_text(" ", strip=True)) or bill_num,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "signedDate": "",
                    "effectiveDate": "",
                    "chapter": "",
                    "detailPath": detail_path,
                    "paper": paper_code,
                    "currentVersionPath": "",
                    "currentVersionFingerprint": "|".join(part for part in (detail_path, paper_code) if part),
                }
            )
            index += 2

        return items

    @staticmethod
    def _paper_from_path(path: str | None) -> str:
        params = parse_qs(urlparse(str(path or "")).query)
        paper_value = first_non_empty(*(values[0] for key, values in params.items() if key.lower() == "paper"))
        return normalize_maine_paper_number(paper_value)

    @staticmethod
    def _legislature_from_path(path: str, fallback_year: int) -> int:
        params = parse_qs(urlparse(path).query)
        raw = first_non_empty(*(values[0] for key, values in params.items() if key.lower() == "snum"))
        if raw.isdigit():
            return int(raw)
        return maine_legislature_number(fallback_year)

    @staticmethod
    def _bill_number_from_display(soup: BeautifulSoup) -> str:
        section = soup.find(id="sec0")
        if section is None:
            return ""
        text = clean_text(section.get_text(" ", strip=True))
        match = re.search(r"\bLD\s+(\d+)\b", text, re.IGNORECASE)
        if match is None:
            return ""
        return f"LD{int(match.group(1))}"

    @staticmethod
    def _title_from_display(soup: BeautifulSoup) -> str:
        title = soup.find("h2", class_="ldTitle")
        return clean_text(title.get_text(" ", strip=True) if title is not None else "")

    @staticmethod
    def _title_from_summary(soup: BeautifulSoup | None) -> str:
        if soup is None:
            return ""
        for table in soup.find_all("table"):
            text = clean_text(table.get_text(" ", strip=True))
            if "Bill Info" not in text:
                continue
            centered = table.find("td", align="center")
            if centered is None:
                continue
            bold = centered.find("b")
            if bold is not None:
                return clean_text(bold.get_text(" ", strip=True))
        return ""

    @staticmethod
    def _sponsor_from_summary(soup: BeautifulSoup | None) -> str:
        if soup is None:
            return ""
        for row in soup.find_all("tr"):
            text = clean_text(row.get_text(" ", strip=True))
            if "Sponsored by" not in text:
                continue
            bold = row.find("b")
            if bold is not None:
                return clean_text(bold.get_text(" ", strip=True))
        return ""

    @staticmethod
    def _summary_fields(soup: BeautifulSoup | None) -> dict[str, str]:
        if soup is None:
            return {}
        fields: dict[str, str] = {}
        for table in soup.find_all("table"):
            heading = clean_text(table.get_text(" ", strip=True))
            if "Status Summary" not in heading:
                continue
            for row in table.find_all("tr"):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 2:
                    continue
                label = clean_text(cells[0].get_text(" ", strip=True)).rstrip(":")
                value = clean_text(cells[1].get_text(" ", strip=True))
                if label:
                    fields[label] = value
        return fields

    @staticmethod
    def _committee_info(soup: BeautifulSoup) -> dict[str, str]:
        section = soup.find(id="sec3")
        if section is None:
            return {}

        text = section.get_text(" ", strip=True)
        committee = ""
        referred_date = ""
        latest_action_text = ""
        latest_action_date = ""
        latest_report_text = ""
        latest_report_date = ""

        referred_match = re.search(
            r"Referred to\s+Committee on\s+(.+?)\s+on\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})\.",
            text,
            re.IGNORECASE,
        )
        if referred_match is not None:
            committee = clean_text(referred_match.group(1))
            referred_date = parse_maine_date(referred_match.group(2))

        latest_action_match = re.search(
            r"Latest Committee Action:\s+(.+?)\s+Latest Committee Report:",
            text,
            re.IGNORECASE,
        )
        if latest_action_match is not None:
            action_text = clean_text(latest_action_match.group(1))
            parsed = MAINE_COMMITTEE_ACTION_PATTERN.fullmatch(action_text)
            if parsed is not None:
                latest_action_text = clean_text(
                    "; ".join(part for part in (parsed.group("action"), parsed.group("result")) if part)
                )
                latest_action_date = parse_maine_date(parsed.group("date"))
            else:
                latest_action_text = action_text

        latest_report_match = re.search(
            r"Latest Committee Report:\s+([A-Za-z]{3,9}\s+\d{1,2},\s+\d{4});\s+(.+?)\s+Committee Docket",
            text,
            re.IGNORECASE,
        )
        if latest_report_match is not None:
            latest_report_date = parse_maine_date(latest_report_match.group(1))
            latest_report_text = clean_text(latest_report_match.group(2))

        return {
            "committee": committee,
            "referred_date": referred_date,
            "latest_action_text": latest_action_text,
            "latest_action_date": latest_action_date,
            "latest_report_text": latest_report_text,
            "latest_report_date": latest_report_date,
        }

    def _summary_url(
        self,
        display_soup: BeautifulSoup,
        detail_path: str,
        year: int,
        legislature: int,
        paper: str,
    ) -> str:
        for anchor in display_soup.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            if label.startswith("Chamber Status"):
                return absolute_url(self.settings.maine_site_base, anchor.get("href")) or ""
        if not paper:
            paper = self._paper_from_path(detail_path)
        if not paper:
            return ""
        session_year = year or _session_start_year(datetime.utcnow().year)
        return absolute_url(
            self.settings.maine_site_base,
            f"/LawMakerWeb/summary.asp?paper={paper}&SessionID={maine_session_id(session_year)}",
        ) or ""

    def _documents_from_display(
        self,
        soup: BeautifulSoup,
        detail_url: str,
        paper: str,
    ) -> tuple[str, str, str, list[dict[str, Any]]]:
        section = soup.find(id="sec0")
        amendments_section = soup.find(id="sec1")
        introduced_path = ""
        current_version_path = ""
        digest_path = ""
        chapter_path = ""

        if section is not None:
            for anchor in section.find_all("a", href=True):
                label = clean_text(anchor.get_text(" ", strip=True))
                href = absolute_url(detail_url, anchor.get("href")) or ""
                if "Printed Document PDF" in label and not introduced_path:
                    introduced_path = href
                    continue
                if label == "Fiscal Note" and not digest_path:
                    digest_path = href
                    continue
                if "Printed Chapter PDF" in label:
                    chapter_path = href
                    continue

        current_version_path = chapter_path or introduced_path

        amendments: list[dict[str, Any]] = []
        amendment_root = amendments_section or section
        if amendment_root is not None:
            for index, block in enumerate(amendment_root.find_all("span", class_="tlnk-amdblk"), start=1):
                code_tag = block.find("span", class_="story_subhead")
                code = clean_text(code_tag.get_text(" ", strip=True) if code_tag is not None else "")
                if not code:
                    continue
                status_tag = block.find("span", class_="tlnk-amd")
                note_tag = block.find("span", class_="infoText")
                doc_url = ""
                for anchor in block.find_all("a", href=True):
                    label = clean_text(anchor.get_text(" ", strip=True))
                    if "Printed Document PDF" in label:
                        doc_url = absolute_url(detail_url, anchor.get("href")) or ""
                        break
                amendments.append(
                    {
                        "amendmentNumber": code,
                        "house": "",
                        "order": index,
                        "sequence": index,
                        "status": clean_text(status_tag.get_text(" ", strip=True) if status_tag is not None else ""),
                        "sponsor": "",
                        "documentUrl": doc_url,
                        "note": clean_text(note_tag.get_text(" ", strip=True) if note_tag is not None else ""),
                    }
                )

        return introduced_path, current_version_path, digest_path, amendments

    @staticmethod
    def _actions_from_display_and_summary(
        display_soup: BeautifulSoup,
        summary_fields: dict[str, str],
        committee_info: dict[str, str],
    ) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []

        if committee_info.get("committee") and committee_info.get("referred_date"):
            actions.append(
                {
                    "statusDate": clean_text(committee_info["referred_date"]),
                    "location": "Committee",
                    "statusMessage": f"Referred to Committee on {committee_info['committee']}.",
                }
            )

        section = display_soup.find(id="sec3")
        if section is not None:
            table = section.find("table", attrs={"name": "CDtab"})
            if table is not None:
                for row in table.find_all("tr"):
                    cells = row.find_all("td", recursive=False)
                    if len(cells) < 2:
                        continue
                    date_text = parse_maine_date(cells[0].get_text(" ", strip=True))
                    action_text = clean_text(cells[1].get_text(" ", strip=True))
                    result_text = clean_text(cells[2].get_text(" ", strip=True)) if len(cells) > 2 else ""
                    message = "; ".join(part for part in (action_text, result_text) if part)
                    if not date_text or not message:
                        continue
                    actions.append(
                        {
                            "statusDate": date_text,
                            "location": "Committee",
                            "statusMessage": message,
                        }
                    )

        for label, chamber in (("Last House Action", "House"), ("Last Senate Action", "Senate")):
            raw_value = clean_text(summary_fields.get(label))
            if not raw_value:
                continue
            match = MAINE_DATE_PREFIX_PATTERN.fullmatch(raw_value)
            if match is not None:
                date_text = parse_maine_date(match.group("date"))
                action_text = clean_text(match.group("text"))
            else:
                date_text = ""
                action_text = raw_value
            actions.append(
                {
                    "statusDate": date_text,
                    "location": chamber,
                    "statusMessage": action_text,
                }
            )

        governor_action = clean_text(summary_fields.get("Governor Action"))
        final_date = parse_maine_date(summary_fields.get("Date"))
        if governor_action:
            actions.append(
                {
                    "statusDate": final_date,
                    "location": "Governor",
                    "statusMessage": governor_action,
                }
            )

        def action_key(action: dict[str, str]) -> tuple[str, int]:
            return (clean_text(action.get("statusDate")), len(action.get("location") or ""))

        normalized = [
            {
                "statusDate": clean_text(action.get("statusDate")),
                "location": clean_text(action.get("location")),
                "statusMessage": clean_text(action.get("statusMessage")),
            }
            for action in actions
            if clean_text(action.get("statusMessage"))
        ]
        return sorted(normalized, key=action_key)

    @staticmethod
    def _summary_action_text(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""
        match = MAINE_DATE_PREFIX_PATTERN.fullmatch(raw)
        if match is None:
            return raw
        return clean_text(match.group("text"))

    @staticmethod
    def _summary_html(
        title: str,
        sponsor: str,
        committee_name: str,
        summary_fields: dict[str, str],
        committee_info: dict[str, str],
    ) -> str:
        parts: list[str] = []
        if title:
            parts.append(f"<p>{html.escape(title)}</p>")
        if sponsor:
            parts.append(f"<p><strong>Sponsor:</strong> {html.escape(sponsor)}</p>")
        if committee_name:
            parts.append(f"<p><strong>Reference committee:</strong> {html.escape(committee_name)}</p>")
        if clean_text(summary_fields.get("Governor Action")):
            parts.append(f"<p><strong>Governor action:</strong> {html.escape(clean_text(summary_fields['Governor Action']))}</p>")
        elif committee_info.get("latest_action_text"):
            parts.append(
                f"<p><strong>Latest committee action:</strong> {html.escape(committee_info['latest_action_text'])}</p>"
            )
        return "".join(parts)

    @staticmethod
    def _actions_html(actions: list[dict[str, str]]) -> str:
        return "".join(
            f"<p><strong>{html.escape(action['statusDate'])}</strong>: {html.escape(action['statusMessage'])}</p>"
            for action in actions[-6:]
            if action.get("statusMessage")
        )

    @staticmethod
    def _chapter_from_display(soup: BeautifulSoup) -> str:
        section = soup.find(id="sec0")
        if section is None:
            return ""
        text = clean_text(section.get_text(" ", strip=True))
        match = MAINE_CHAPTER_PATTERN.search(text)
        if match is None:
            return ""
        return match.group(1)
