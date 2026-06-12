from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


OKLAHOMA_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HJR|SJR|HCR|SCR|HR|SR)\d+$", re.IGNORECASE)
OKLAHOMA_MEASURE_TYPES = ("HB", "HJR", "HCR", "HR", "SB", "SJR", "SCR", "SR")
OKLAHOMA_STATUS_REPORT_PATH = "/WebApplication3/WebForm1.aspx"


def normalize_oklahoma_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if OKLAHOMA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_oklahoma_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
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


class OklahomaApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.site_client = httpx.Client(
            base_url=self.settings.oklahoma_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.report_client = httpx.Client(
            base_url=self.settings.oklahoma_reports_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.site_client.close()
        self.report_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_id = self._session_id(year)
        form_soup = self._report_form_soup(OKLAHOMA_STATUS_REPORT_PATH)
        payload = self._report_payload(form_soup, session_id)
        response = self.report_client.post(OKLAHOMA_STATUS_REPORT_PATH, data=payload)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items_by_bill: dict[str, dict[str, Any]] = {}
        for row in soup.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 6:
                continue
            bill_link = cells[0].find("a", href=re.compile(r"BillInfo\.aspx\?Bill=", re.IGNORECASE))
            if bill_link is None:
                continue
            bill_num = normalize_oklahoma_bill_number(bill_link.get_text(" ", strip=True))
            if not bill_num:
                continue

            detail_path = self._site_url(bill_link.get("href"))
            title = clean_text(cells[5].get_text(" ", strip=True)) or bill_num
            last_action = clean_text(cells[3].get_text(" ", strip=True))
            last_action_date = parse_oklahoma_date(cells[4].get_text(" ", strip=True))

            items_by_bill[bill_num] = {
                "billNum": bill_num,
                "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                "catchTitle": title,
                "billTitle": title,
                "sponsor": "",
                "billStatus": last_action,
                "lastAction": last_action,
                "lastActionDate": last_action_date,
                "detailPath": detail_path,
                "currentVersionPath": None,
                "currentVersionFingerprint": "|".join(part for part in (detail_path, last_action, last_action_date, title) if part),
            }

        return sorted(items_by_bill.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.site_client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = first_non_empty(
            normalize_oklahoma_bill_number((item or {}).get("billNum")),
            normalize_oklahoma_bill_number(self._node_text(soup.find(id="ctl00_ContentPlaceHolder1_lblBillDisplay"))),
            self._bill_number_from_url(str(response.url)),
        )
        if not bill_num:
            raise ValueError(f"Oklahoma bill number could not be determined from {detail_path}")

        catch_title = first_non_empty(
            self._node_text(soup.find(id="ctl00_ContentPlaceHolder1_txtST")),
            str((item or {}).get("catchTitle") or ""),
            str((item or {}).get("billTitle") or ""),
            bill_num,
        )

        primary_author = self._node_text(soup.find(id="ctl00_ContentPlaceHolder1_lnkAuth"))
        other_author = self._node_text(soup.find(id="ctl00_ContentPlaceHolder1_lnkOtherAuth"))
        sponsor = first_non_empty(primary_author, other_author)
        sponsor_house, sponsor_senate = self._primary_sponsors(bill_num, primary_author, other_author)

        history_actions = self._history_actions(soup)
        last_action_entry = history_actions[-1] if history_actions else {}
        last_action = first_non_empty(
            clean_text(last_action_entry.get("statusMessage")),
            str((item or {}).get("lastAction") or ""),
            str((item or {}).get("billStatus") or ""),
        )
        last_action_date = first_non_empty(
            clean_text(last_action_entry.get("statusDate")),
            str((item or {}).get("lastActionDate") or ""),
        )
        signed_date = self._signed_date(history_actions)

        amendment_rows = self._document_rows(
            soup=soup,
            table_id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel2_tblAmendments",
            stop_prefixes=(),
        )
        summary_rows = self._document_rows(
            soup=soup,
            table_id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel3_tblBillSum",
            stop_prefixes=(),
        )
        version_rows = self._document_rows(
            soup=soup,
            table_id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel4_tblVersions",
            stop_prefixes=("Committee Reports for", "Conference Committee Reports for"),
        )
        vote_rows = self._document_rows(
            soup=soup,
            table_id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel5_tblVotes",
            stop_prefixes=(),
        )
        coauthor_rows = self._coauthor_entries(soup)

        summary_path = first_non_empty(
            next(
                (
                    row["url"]
                    for row in summary_rows
                    if row["url"] and "bill summaries" in row["section"].lower()
                ),
                "",
            ),
            None,
        )
        digest_path = first_non_empty(
            next(
                (
                    row["url"]
                    for row in summary_rows
                    if row["url"]
                    and (
                        row["section"].lower().startswith("fiscal impact")
                        or "actuarial" in row["section"].lower()
                    )
                ),
                "",
            ),
            summary_path,
            None,
        )
        introduced_path = first_non_empty(
            next((row["url"] for row in version_rows if row["url"] and row["label"].lower().startswith("introduced")), ""),
            self._introduced_link(soup),
            None,
        )
        current_version_path = first_non_empty(
            next(
                (
                    row["url"]
                    for row in version_rows
                    if row["url"] and "enrolled" in row["label"].lower()
                ),
                "",
            ),
            next((row["url"] for row in reversed(version_rows) if row["url"]), ""),
            introduced_path,
            None,
        )

        amendments = [
            {
                "amendmentNumber": row["label"],
                "adoptedDate": row["date"],
                "documentUrl": row["url"],
                "summaryText": f"{row['section']}: {row['label']}".strip(": "),
                "source": "Oklahoma Legislature",
            }
            for row in amendment_rows
            if row["label"] and row["url"]
        ]

        summary_parts = [f"<p>{html.escape(catch_title)}</p>"] if catch_title else []
        if summary_rows:
            summary_parts.append("<ul>")
            for row in summary_rows:
                label = html.escape(row["label"])
                meta = html.escape(first_non_empty(row["meta"], row["date"]))
                section = html.escape(row["section"])
                if meta:
                    summary_parts.append(f"<li>{section}: {label} ({meta})</li>")
                else:
                    summary_parts.append(f"<li>{section}: {label}</li>")
            summary_parts.append("</ul>")

        digest_parts: list[str] = []
        if history_actions:
            digest_parts.append("<p>Recent official actions:</p><ul>")
            for action in history_actions[-8:]:
                digest_parts.append(
                    "<li>"
                    + html.escape(
                        " ".join(
                            part
                            for part in (
                                action.get("statusDate"),
                                action.get("location"),
                                action.get("statusMessage"),
                            )
                            if clean_text(str(part))
                        )
                    )
                    + "</li>"
                )
            digest_parts.append("</ul>")
        if amendment_rows:
            digest_parts.append("<p>Amendments and related filings:</p><ul>")
            for row in amendment_rows:
                label = html.escape(row["label"])
                section = html.escape(row["section"])
                meta = html.escape(first_non_empty(row["meta"], row["date"]))
                if meta:
                    digest_parts.append(f"<li>{section}: {label} ({meta})</li>")
                else:
                    digest_parts.append(f"<li>{section}: {label}</li>")
            digest_parts.append("</ul>")
        if vote_rows:
            digest_parts.append("<p>Official vote files:</p><ul>")
            for row in vote_rows:
                label = html.escape(row["label"])
                meta = html.escape(first_non_empty(row["meta"], row["date"]))
                if meta:
                    digest_parts.append(f"<li>{label} ({meta})</li>")
                else:
                    digest_parts.append(f"<li>{label}</li>")
            digest_parts.append("</ul>")

        current_version_fingerprint = "|".join(
            part
            for part in (
                current_version_path,
                introduced_path,
                summary_path,
                digest_path,
                last_action,
                last_action_date,
                signed_date,
                str(len(version_rows)),
                str(len(amendments)),
                str(len(coauthor_rows)),
            )
            if clean_text(str(part))
        )

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": catch_title,
            "sponsor": sponsor,
            "billTitle": catch_title,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": "",
            "sponsorStringHouse": sponsor_house,
            "sponsorStringSenate": sponsor_senate,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": summary_path,
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": "".join(summary_parts),
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": "",
            "billActions": history_actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.site_client, url)

    @staticmethod
    def _session_id(year: int) -> str:
        return f"{year % 100:02d}00"

    def _report_form_soup(self, path: str) -> BeautifulSoup:
        response = self.report_client.get(path)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def _report_payload(self, soup: BeautifulSoup, session_id: str) -> dict[str, Any]:
        def field(name: str) -> str:
            node = soup.find(attrs={"name": name})
            return str(node.get("value") or "") if node is not None else ""

        return {
            "__VIEWSTATE": field("__VIEWSTATE"),
            "__VIEWSTATEGENERATOR": field("__VIEWSTATEGENERATOR"),
            "__EVENTVALIDATION": field("__EVENTVALIDATION"),
            "cbxSessionId": session_id,
            "cbxActiveStatus": "All",
            "lbxTypes": list(OKLAHOMA_MEASURE_TYPES),
            "Button1": "Retrieve",
        }

    @staticmethod
    def _bill_number_from_url(detail_url: str) -> str:
        try:
            parsed = urlparse(detail_url)
        except ValueError:
            return ""
        return normalize_oklahoma_bill_number(parse_qs(parsed.query).get("Bill", [""])[0])

    @staticmethod
    def _node_text(node: Tag | None) -> str:
        if node is None:
            return ""
        return clean_text(node.get_text(" ", strip=True))

    def _history_actions(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        heading = soup.find("h1", string=re.compile(r"^\s*History\s+For\s+", re.IGNORECASE))
        if heading is None:
            return []
        table = heading.find_parent("table")
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        for row in table.find_all("tr", recursive=False):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 4:
                continue
            action = clean_text(cells[0].get_text(" ", strip=True))
            if not action or action.lower() == "action":
                continue
            rows.append(
                {
                    "statusMessage": action,
                    "journalPage": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusDate": parse_oklahoma_date(cells[2].get_text(" ", strip=True)),
                    "location": {"H": "House", "S": "Senate"}.get(
                        clean_text(cells[3].get_text(" ", strip=True)).upper(),
                        clean_text(cells[3].get_text(" ", strip=True)),
                    ),
                }
            )
        return rows

    def _document_rows(
        self,
        *,
        soup: BeautifulSoup,
        table_id: str,
        stop_prefixes: tuple[str, ...],
    ) -> list[dict[str, str]]:
        table = soup.find(id=table_id)
        if table is None:
            return []

        rows: list[dict[str, str]] = []
        section = ""
        for row in table.find_all("tr", recursive=False):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            first_text = clean_text(cells[0].get_text(" ", strip=True))
            if not first_text:
                continue
            if len(cells) == 1:
                if stop_prefixes and any(first_text.startswith(prefix) for prefix in stop_prefixes):
                    break
                section = first_text
                continue
            if first_text.lower() == "none":
                continue

            link = cells[0].find("a", href=True)
            meta = clean_text(cells[1].get_text(" ", strip=True)) if len(cells) > 1 else ""
            rows.append(
                {
                    "section": section,
                    "label": clean_text(link.get_text(" ", strip=True)) if link is not None else first_text,
                    "url": self._site_url(link.get("href")) if link is not None else "",
                    "date": self._normalized_date(meta),
                    "meta": meta,
                }
            )
        return rows

    def _coauthor_entries(self, soup: BeautifulSoup) -> list[dict[str, str]]:
        table = soup.find(id="ctl00_ContentPlaceHolder1_TabContainer1_TabPanel6_tblCoAuth")
        if table is None:
            return []

        entries: list[dict[str, str]] = []
        for row in table.find_all("tr", recursive=False):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            text = clean_text(cells[0].get_text(" ", strip=True))
            if not text or text.startswith("Authors/Co Authors") or text.startswith("***"):
                continue
            match = re.match(r"(?P<name>.+?)\s+\((?P<chamber>[HS])\)$", text)
            if match is None:
                continue
            entries.append(
                {
                    "name": clean_text(match.group("name")),
                    "chamber": {"H": "House", "S": "Senate"}[match.group("chamber")],
                }
            )
        return entries

    @staticmethod
    def _primary_sponsors(bill_num: str, primary_author: str, other_author: str) -> tuple[str | None, str | None]:
        if bill_num.startswith("H"):
            return (primary_author or None, other_author or None)
        if bill_num.startswith("S"):
            return (other_author or None, primary_author or None)
        return (None, None)

    @staticmethod
    def _signed_date(history_actions: list[dict[str, str]]) -> str:
        for action in reversed(history_actions):
            message = clean_text(action.get("statusMessage")).lower()
            if "approved by governor" in message:
                return clean_text(action.get("statusDate"))
        return ""

    @staticmethod
    def _normalized_date(value: str | None) -> str:
        normalized = parse_oklahoma_date(value)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", normalized):
            return normalized
        return ""

    def _introduced_link(self, soup: BeautifulSoup) -> str | None:
        link = soup.find(id="ctl00_ContentPlaceHolder1_lnkIntroduced")
        if link is None:
            return None
        return self._site_url(link.get("href")) or None

    def _site_url(self, href: str | None) -> str:
        url = absolute_url(self.settings.oklahoma_site_base, href) or ""
        if url.startswith("http://www.oklegislature.gov"):
            return "https://www.oklegislature.gov" + url[len("http://www.oklegislature.gov") :]
        return url
