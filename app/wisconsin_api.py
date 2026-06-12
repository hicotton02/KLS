from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


WISCONSIN_BILL_NUMBER_PATTERN = re.compile(r"^(AB|AJR|AR|SB|SJR|SR)\d+$", re.IGNORECASE)


def parse_wisconsin_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_wisconsin_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if WISCONSIN_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class WisconsinApiClient:
    index_requires_detail_fetch = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.wisconsin_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get(f"/{year}/related/proposals")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        pattern = re.compile(rf"/document/proposaltext/{year}/REG/([A-Z]+\d+)$", re.IGNORECASE)

        for anchor in soup.find_all("a", href=True):
            match = pattern.search(anchor.get("href") or "")
            if match is None:
                continue
            bill_num = normalize_wisconsin_bill_number(match.group(1))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            proposal_url = absolute_url(self.settings.wisconsin_site_base, anchor.get("href")) or ""
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": bill_num,
                    "billTitle": bill_num,
                    "sponsor": "",
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": absolute_url(
                        self.settings.wisconsin_site_base,
                        f"/document/session/{year}/REG/{bill_num}",
                    ),
                    "currentVersionPath": proposal_url,
                    "currentVersionFingerprint": proposal_url,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        bill_num = self._bill_number(str(response.url), item)
        status_value = self._status_value(soup)
        important_actions = self._table_actions(soup, "Important Actions")
        history_actions = self._table_actions(soup, "History")
        links = self._links_block(soup, "Links")

        proposal_url = links.get("Bill Text") or clean_text((item or {}).get("currentVersionPath")) or ""
        proposal_title = bill_num
        sponsor = ""
        if proposal_url:
            proposal_response = self.client.get(proposal_url)
            proposal_response.raise_for_status()
            proposal_soup = BeautifulSoup(proposal_response.text, "html.parser")
            proposal_title = self._proposal_title(proposal_soup, bill_num)
            sponsor = self._proposal_sponsors(proposal_soup)

        last_action_entry = important_actions[0] if important_actions else (history_actions[-1] if history_actions else {})
        last_action = clean_text(last_action_entry.get("statusMessage") or status_value)
        last_action_date = clean_text(last_action_entry.get("statusDate"))
        chapter_no = self._chapter_number(status_value, important_actions, history_actions)
        signed_date = self._signed_date(history_actions)

        current_version_path = links.get("Text as Enrolled") or proposal_url
        introduced_path = proposal_url or current_version_path

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": proposal_title,
            "sponsor": sponsor,
            "billTitle": proposal_title,
            "billStatus": status_value,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter_no,
            "enrolledNumber": "Text as Enrolled" if links.get("Text as Enrolled") else "",
            "sponsorStringHouse": sponsor if bill_num.startswith("A") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path or None,
            "digest": links.get("Fiscal Estimates and Reports") or None,
            "summary": str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    status_value,
                    last_action,
                    last_action_date,
                    chapter_no,
                    str(len(history_actions)),
                )
                if part
            ),
            "summaryHTML": f"<p>{html.escape(proposal_title)}</p><p>Status: {html.escape(status_value)}</p>" if proposal_title else "",
            "digestHTML": self._actions_html(important_actions),
            "currentBillHTML": "",
            "billActions": history_actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _bill_number(detail_url: str, item: dict[str, Any] | None = None) -> str:
        match = re.search(r"/document/session/\d+/REG/([A-Z]+\d+)$", detail_url, re.IGNORECASE)
        if match:
            bill_num = normalize_wisconsin_bill_number(match.group(1))
            if bill_num:
                return bill_num
        fallback = normalize_wisconsin_bill_number((item or {}).get("billNum"))
        if fallback:
            return fallback
        raise ValueError(f"Wisconsin bill number could not be parsed from {detail_url}")

    @staticmethod
    def _status_value(soup: BeautifulSoup) -> str:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and clean_text(value).startswith("Status:"))
        if heading is None:
            return ""
        return clean_text(heading.get_text(" ", strip=True)).replace("Status:", "", 1).strip()

    def _table_actions(self, soup: BeautifulSoup, heading_prefix: str) -> list[dict[str, str]]:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and clean_text(value).startswith(heading_prefix))
        if heading is None:
            return []
        table = heading.find_next("table")
        if table is None:
            return []
        rows: list[dict[str, str]] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            date_cell = clean_text(cells[0].get_text(" ", strip=True))
            date_parts = date_cell.split(" ", 1)
            rows.append(
                {
                    "statusDate": parse_wisconsin_date(date_parts[0]),
                    "location": clean_text(date_parts[1] if len(date_parts) > 1 else ""),
                    "statusMessage": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )
        return rows

    def _links_block(self, soup: BeautifulSoup, heading_text: str) -> dict[str, str]:
        heading = soup.find("h2", string=lambda value: isinstance(value, str) and clean_text(value) == heading_text)
        if heading is None:
            return {}
        links: dict[str, str] = {}
        node = heading.find_next_sibling()
        while isinstance(node, Tag) and node.name != "h2":
            for anchor in node.find_all("a", href=True):
                text = clean_text(anchor.get_text(" ", strip=True))
                if text:
                    links[text] = absolute_url(self.settings.wisconsin_site_base, anchor.get("href")) or ""
            node = node.find_next_sibling()
        return links

    @staticmethod
    def _proposal_title(soup: BeautifulSoup, bill_num: str) -> str:
        meta = soup.find("meta", attrs={"name": "Description"})
        description = clean_text(meta.get("content")) if isinstance(meta, Tag) else ""
        if description:
            return re.sub(r"^Relating to:\s*", "", description, flags=re.IGNORECASE).strip().rstrip(".")
        return bill_num

    @staticmethod
    def _proposal_sponsors(soup: BeautifulSoup) -> str:
        text = clean_text(soup.get_text(" ", strip=True))
        match = re.search(r"Introduced by (.+?)\. Referred to Committee on", text, re.IGNORECASE)
        if not match:
            return ""
        sponsor_text = clean_text(match.group(1))
        sponsor_text = sponsor_text.replace(" ;", ";").replace(" ,", ",")
        return sponsor_text

    @staticmethod
    def _actions_html(actions: list[dict[str, str]]) -> str:
        if not actions:
            return ""
        return "".join(
            f"<p><strong>{html.escape(action['statusDate'])}</strong>: {html.escape(action['statusMessage'])}</p>"
            for action in actions[:6]
            if action["statusMessage"]
        )

    @staticmethod
    def _chapter_number(status_value: str, important_actions: list[dict[str, str]], history_actions: list[dict[str, str]]) -> str:
        combined = " ".join(
            [clean_text(status_value)]
            + [clean_text(action["statusMessage"]) for action in important_actions]
            + [clean_text(action["statusMessage"]) for action in history_actions]
        )
        match = re.search(r"\bAct\s+(\d+)\b", combined, re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    @staticmethod
    def _signed_date(history_actions: list[dict[str, str]]) -> str:
        for action in history_actions:
            text = clean_text(action["statusMessage"]).lower()
            if "approved by the governor" in text or "published as" in text:
                return clean_text(action["statusDate"])
        return ""
