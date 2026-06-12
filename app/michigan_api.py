from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


MICHIGAN_OBJECT_NAME_PATTERN = re.compile(r"(?P<session>\d{4})-(?P<kind>HB|SB)-(?P<number>\d+)", re.IGNORECASE)
MICHIGAN_BILL_TEXT_PATTERN = re.compile(
    r"^(?P<chamber>House|Senate)\s+Bill\s+(?P<number>\d+)\s+of\s+(?P<session>\d{4})",
    re.IGNORECASE,
)
MICHIGAN_PUBLIC_ACT_PATTERN = re.compile(r"\((?:Public Act|PA)\s+(?P<number>\d+)\s+of\s+(?P<year>\d{4})\)", re.IGNORECASE)
MICHIGAN_AMENDMENT_PATTERN = re.compile(r"\(([HS])-(\d+)\)", re.IGNORECASE)


def parse_michigan_date(value: str | None) -> str:
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


def normalize_michigan_bill_number(value: str | None) -> str:
    raw = clean_text(str(value or "")).upper()
    object_name_match = MICHIGAN_OBJECT_NAME_PATTERN.search(raw)
    if object_name_match is not None:
        return f"{object_name_match.group('kind').upper()}{int(object_name_match.group('number'))}"
    raw = raw.replace("HOUSE BILL", "HB ").replace("SENATE BILL", "SB ")
    raw = re.sub(r"\bOF\s+\d{4}\b", "", raw)
    raw = raw.replace("NO.", "").replace("NO", "")
    match = re.search(r"\b(HB|SB)\s*0*(\d+)\b", raw)
    if match is None:
        return ""
    return f"{match.group(1)}{int(match.group(2))}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class MichiganApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.michigan_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_label = self._session_label(year)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for doc_type in ("House Bill", "Senate Bill"):
            response = self.client.get(
                "/Search/ExecuteSearch",
                params={"sessions": session_label, "docTypes": doc_type},
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            table = soup.find("table")
            if table is None:
                continue
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 3:
                    continue
                anchor = cells[0].find("a", href=True)
                if anchor is None:
                    continue
                object_name = self._object_name_from_href(anchor.get("href"))
                bill_num = normalize_michigan_bill_number(anchor.get_text(" ", strip=True))
                if not object_name or not bill_num or bill_num in seen:
                    continue
                seen.add(bill_num)
                detail_path = absolute_url(
                    self.settings.michigan_site_base,
                    f"/Bills/Bill?ObjectName={object_name}",
                )
                title, last_action = self._search_result_description(cells[2])
                public_act = self._public_act(cells[0].get_text(" ", strip=True))
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": bill_num[:2],
                        "catchTitle": title or bill_num,
                        "billTitle": title or bill_num,
                        "sponsor": "",
                        "billStatus": first_non_empty(last_action, public_act),
                        "lastAction": last_action,
                        "lastActionDate": "",
                        "signedDate": "",
                        "effectiveDate": "",
                        "chapter": public_act,
                        "enrolledNumber": public_act,
                        "detailPath": detail_path,
                        "objectName": object_name,
                    }
                )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        page_heading = self._page_heading(soup)
        bill_num = normalize_michigan_bill_number(page_heading) or normalize_michigan_bill_number((item or {}).get("billNum"))
        if not bill_num:
            raise ValueError("Michigan bill number could not be parsed")

        sponsors = self._sponsor_names(soup)
        categories = self._categories_text(soup)
        catch_title = first_non_empty(
            clean_text(str((item or {}).get("catchTitle") or "")),
            self._description_text(soup),
            bill_num,
        )
        bill_title = first_non_empty(catch_title, clean_text(str((item or {}).get("billTitle") or "")), bill_num)
        actions = self._history_rows(soup)
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": ""}
        last_action = first_non_empty(clean_text(str(latest_action.get("statusMessage") or "")), clean_text(str((item or {}).get("lastAction") or "")))
        last_action_date = first_non_empty(parse_michigan_date(latest_action.get("statusDate")), clean_text(str((item or {}).get("lastActionDate") or "")))
        document_links = self._document_links(soup, str(response.url))
        current_document = self._pick_current_document(document_links)
        introduced_document = self._document_by_label(document_links, "Introduced Bill")
        digest_document = self._latest_analysis_link(soup, str(response.url))
        public_act = first_non_empty(
            self._public_act(page_heading),
            clean_text(str((item or {}).get("chapter") or "")),
        )
        signed_date = last_action_date if public_act else ""
        amendments = self._amendments_from_actions(actions)
        sponsor = ", ".join(sponsors)

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": catch_title,
            "sponsor": sponsor,
            "billTitle": bill_title,
            "billStatus": first_non_empty(last_action, clean_text(str((item or {}).get("billStatus") or ""))),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": public_act,
            "enrolledNumber": public_act,
            "sponsorStringHouse": sponsor if bill_num.startswith("HB") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("SB") else None,
            "introduced": introduced_document,
            "digest": digest_document,
            "summary": str(response.url),
            "currentVersionPath": current_document.get("url") if current_document else introduced_document,
            "currentVersionFingerprint": "|".join(link["url"] for link in document_links if link.get("url")),
            "summaryHTML": self._paragraph_html(catch_title),
            "digestHTML": self._paragraph_html(categories or bill_title),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _session_label(year: int) -> str:
        return f"{year - 1}-{year}"

    @staticmethod
    def _object_name_from_href(href: str | None) -> str:
        match = MICHIGAN_OBJECT_NAME_PATTERN.search(str(href or ""))
        if match is None:
            object_name_match = re.search(r"objectName=([^&]+)", str(href or ""), re.IGNORECASE)
            return clean_text(object_name_match.group(1)) if object_name_match else ""
        return match.group(0)

    @staticmethod
    def _search_result_description(cell: Tag) -> tuple[str, str]:
        pieces: list[str] = []
        for content in cell.contents:
            if isinstance(content, Tag) and content.name == "br":
                continue
            pieces.append(clean_text(getattr(content, "get_text", lambda *args, **kwargs: str(content))(" ", strip=True) if isinstance(content, Tag) else str(content)))
        text = " ".join(piece for piece in pieces if piece)
        if "Last Action:" in text:
            description, last_action = text.split("Last Action:", 1)
            return clean_text(description), clean_text(last_action)
        return clean_text(text), ""

    @staticmethod
    def _public_act(value: str | None) -> str:
        match = MICHIGAN_PUBLIC_ACT_PATTERN.search(clean_text(str(value or "")))
        if match is None:
            return ""
        return f"PA {int(match.group('number'))} of {match.group('year')}"

    @staticmethod
    def _page_heading(soup: BeautifulSoup) -> str:
        main = soup.find("main")
        if main is None:
            return ""
        text = clean_text(main.get_text(" ", strip=True))
        match = MICHIGAN_BILL_TEXT_PATTERN.search(text)
        if match is None:
            return ""
        return f"{match.group('chamber').title()} Bill {match.group('number')} of {match.group('session')}"

    @staticmethod
    def _sponsor_names(soup: BeautifulSoup) -> list[str]:
        header = next((tag for tag in soup.find_all("h2") if clean_text(tag.get_text(" ", strip=True)).lower() == "sponsors"), None)
        if header is None:
            return []
        container = header.find_next_sibling("div")
        if container is None:
            return []
        combined = clean_text(container.get_text(" ", strip=True))
        matches = re.findall(r"[A-Z][A-Za-z.\-'\s]+?\(District \d+\)", combined)
        if matches:
            return [clean_text(match) for match in matches]
        sponsors: list[str] = []
        for line in container.stripped_strings:
            cleaned = clean_text(line)
            if cleaned:
                sponsors.append(cleaned)
        return sponsors

    @staticmethod
    def _categories_text(soup: BeautifulSoup) -> str:
        header = next((tag for tag in soup.find_all("h2") if clean_text(tag.get_text(" ", strip=True)).lower() == "categories"), None)
        if header is None:
            return ""
        container = header.find_next_sibling("div")
        return clean_text(container.get_text(" ", strip=True)) if container is not None else ""

    @staticmethod
    def _description_text(soup: BeautifulSoup) -> str:
        header = next((tag for tag in soup.find_all("h2") if clean_text(tag.get_text(" ", strip=True)).lower() == "categories"), None)
        if header is None:
            return ""
        description_parts: list[str] = []
        skipped_category = False
        for sibling in header.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "h2":
                break
            if isinstance(sibling, Tag):
                text = clean_text(sibling.get_text(" ", strip=True))
                if not skipped_category:
                    skipped_category = True
                    continue
                if text and not text.startswith("Bill Document Formatting Information"):
                    description_parts.append(text)
        return clean_text(" ".join(description_parts))

    @staticmethod
    def _document_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
        header = next((tag for tag in soup.find_all("h2") if clean_text(tag.get_text(" ", strip=True)).lower() == "documents"), None)
        if header is None:
            return []
        links: list[dict[str, str]] = []
        seen: set[str] = set()
        for sibling in header.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "h2":
                break
            if not isinstance(sibling, Tag):
                continue
            for row in sibling.select(".billDocRow"):
                label_node = row.select_one(".text")
                strong_label = clean_text(label_node.find("strong").get_text(" ", strip=True)) if label_node and label_node.find("strong") else ""
                label = strong_label or (clean_text(" ".join(label_node.stripped_strings)) if label_node else "")
                hrefs = [absolute_url(base_url, anchor.get("href")) for anchor in row.find_all("a", href=True)]
                url = next((href for href in hrefs if href and href.lower().endswith((".pdf", ".htm", ".html"))), None)
                short_label = clean_text(label)
                if not url or url in seen:
                    continue
                seen.add(url)
                links.append({"label": short_label, "url": url})
        return links

    @staticmethod
    def _pick_current_document(links: list[dict[str, str]]) -> dict[str, str] | None:
        priorities = (
            "Public Act",
            "Enrolled Bill",
            "Concurred Bill",
            "As Passed by the Senate",
            "As Passed by the House",
            "Senate Introduced Bill",
            "House Introduced Bill",
            "Introduced Bill",
        )
        for label in priorities:
            for link in links:
                if clean_text(link.get("label")).lower().startswith(label.lower()):
                    return link
        return links[-1] if links else None

    @staticmethod
    def _document_by_label(links: list[dict[str, str]], label: str) -> str | None:
        normalized = label.lower()
        for link in links:
            if normalized in clean_text(link.get("label")).lower():
                return link.get("url")
        return None

    @staticmethod
    def _latest_analysis_link(soup: BeautifulSoup, base_url: str) -> str | None:
        header = next((tag for tag in soup.find_all("h2") if clean_text(tag.get_text(" ", strip=True)).lower() == "analysis"), None)
        if header is None:
            return None
        latest: str | None = None
        for sibling in header.next_siblings:
            if isinstance(sibling, Tag) and sibling.name == "h2":
                break
            if not isinstance(sibling, Tag):
                continue
            for anchor in sibling.find_all("a", href=True):
                url = absolute_url(base_url, anchor.get("href"))
                if url and url.lower().endswith(".pdf"):
                    latest = url
        return latest

    @staticmethod
    def _history_rows(soup: BeautifulSoup) -> list[dict[str, str]]:
        container = soup.find("div", id="History")
        table = container.find("table") if container is not None else None
        if table is None:
            return []
        parsed: list[dict[str, str]] = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            action_text = clean_text(cells[2].get_text(" ", strip=True))
            action_url = None
            action_link = cells[2].find("a", href=True)
            if action_link is not None:
                action_url = absolute_url("https://www.legislature.mi.gov", action_link.get("href"))
            parsed.append(
                {
                    "location": clean_text(cells[1].get_text(" ", strip=True)),
                    "statusDate": parse_michigan_date(cells[0].get_text(" ", strip=True)),
                    "statusMessage": action_text,
                    "documentUrl": action_url or "",
                }
            )
        return parsed

    @staticmethod
    def _amendments_from_actions(actions: list[dict[str, str]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for sequence, action in enumerate(actions, start=1):
            status_message = clean_text(str(action.get("statusMessage") or ""))
            match = MICHIGAN_AMENDMENT_PATTERN.search(status_message)
            if match is None:
                continue
            amendment_number = f"{match.group(1).upper()}-{int(match.group(2))}"
            if amendment_number in seen:
                continue
            seen.add(amendment_number)
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": match.group(1).upper(),
                    "order": sequence,
                    "sequence": sequence,
                    "status": status_message,
                    "sponsor": "",
                    "documentUrl": clean_text(str(action.get("documentUrl") or "")) or None,
                }
            )
        return amendments

    @staticmethod
    def _paragraph_html(text: str) -> str:
        cleaned = clean_text(text)
        return f"<p>{cleaned}</p>" if cleaned else ""
