from __future__ import annotations

import re
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


WASHINGTON_XML_NS = {"wa": "http://WSLWebServices.leg.wa.gov/"}
WASHINGTON_BASE_PREFIXES = (
    "HCR",
    "SCR",
    "HJR",
    "SJR",
    "HJM",
    "SJM",
    "SGA",
    "HI",
    "SI",
    "HR",
    "SR",
    "HB",
    "SB",
)


def washington_biennium_start(year: int) -> int:
    return year if year % 2 == 1 else year - 1


def washington_biennium(year: int) -> str:
    start_year = washington_biennium_start(year)
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def washington_legislature_number(year: int) -> int:
    start_year = washington_biennium_start(year)
    return ((start_year - 1889) // 2) + 1


def canonical_washington_prefix(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    for prefix in WASHINGTON_BASE_PREFIXES:
        if raw.endswith(prefix):
            return prefix
    return raw


def normalize_washington_bill_number(prefix: str | None, number: str | int | None) -> str:
    canonical_prefix = canonical_washington_prefix(prefix)
    raw_number = clean_text(str(number or ""))
    if not canonical_prefix or not raw_number.isdigit():
        return ""
    return f"{canonical_prefix}{int(raw_number)}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


def parse_washington_date(value: str | None) -> str:
    raw = clean_text(value)
    if "T" in raw:
        return raw.split("T", 1)[0]
    return raw[:10] if len(raw) >= 10 and "-" in raw else raw


class WashingtonApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.washington_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self.service_client = httpx.Client(
            base_url="https://wslwebservices.leg.wa.gov",
            headers={"User-Agent": "keeping-law-simple/1.0", "Accept": "application/xml, text/xml, */*"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._feature_rows: dict[int, list[dict[str, Any]]] = {}

    def close(self) -> None:
        self.client.close()
        self.service_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        rows = self._feature_data_for_year(year)
        deduped: dict[str, dict[str, Any]] = {}
        for row in rows:
            bill_num = normalize_washington_bill_number(row.get("prefix"), row.get("legnum"))
            if not bill_num:
                continue
            existing = deduped.get(bill_num)
            candidate = self._build_list_item(year, bill_num, row)
            if existing is None or int(candidate.get("rowRank", 0)) > int(existing.get("rowRank", 0)):
                deduped[bill_num] = candidate
        return sorted(deduped.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        source_item = item or {}
        bill_num = str(source_item.get("billNum") or "").strip().upper()
        if not bill_num:
            raise ValueError("Washington bill number is required")

        year = int(source_item.get("sourceYear") or source_item.get("year") or 0)
        if year <= 0:
            raise ValueError("Washington source year is required")

        biennium = str(source_item.get("biennium") or washington_biennium(year))
        bill_number = int(clean_text(str(source_item.get("billNumber") or "")) or re.sub(r"^[A-Z]+", "", bill_num))
        bill_id = str(source_item.get("baseBillId") or self._base_bill_id(bill_num))

        legislation = self._fetch_legislation(biennium, bill_number)
        sponsors = self._fetch_sponsors(biennium, bill_id)
        amendments = self._fetch_amendments(biennium, bill_number)

        response = self.client.get(detail_path)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        official_page = str(response.url)
        current_version_path = self._original_bill_url(soup)
        article_text = self._article_text(soup)
        short_description = clean_text(self._xml_text(legislation.find("wa:ShortDescription", WASHINGTON_XML_NS)))
        long_description = clean_text(self._xml_text(legislation.find("wa:LongDescription", WASHINGTON_XML_NS)))
        legal_title = clean_text(self._xml_text(legislation.find("wa:LegalTitle", WASHINGTON_XML_NS)))
        sponsor = ", ".join(sponsors) or clean_text(str(source_item.get("sponsor") or ""))
        current_status = legislation.find("wa:CurrentStatus", WASHINGTON_XML_NS)
        last_action = clean_text(self._xml_text(current_status.find("wa:HistoryLine", WASHINGTON_XML_NS) if current_status is not None else None))
        last_action_date = parse_washington_date(
            self._xml_text(current_status.find("wa:ActionDate", WASHINGTON_XML_NS) if current_status is not None else None)
        )
        bill_status = clean_text(self._xml_text(current_status.find("wa:Status", WASHINGTON_XML_NS) if current_status is not None else None))
        bill_title = first_non_empty(long_description, short_description, legal_title, bill_num)
        chapter = self._session_law_chapter(legislation)
        signed_date = ""
        if chapter or (current_status is not None and self._xml_text(current_status.find("wa:Veto", WASHINGTON_XML_NS)).lower() == "false"):
            if legislation.find("wa:CurrentStatus/wa:Status", WASHINGTON_XML_NS) is not None and "governor" in last_action.lower():
                signed_date = last_action_date
        digest_path = self._fiscal_note_url(year, bill_number) if self._has_fiscal_note(legislation) else official_page
        current_fingerprint = "|".join(
            part
            for part in (
                self._xml_text(legislation.find("wa:BillId", WASHINGTON_XML_NS)),
                self._xml_text(legislation.find("wa:SubstituteVersion", WASHINGTON_XML_NS)),
                self._xml_text(legislation.find("wa:EngrossedVersion", WASHINGTON_XML_NS)),
                last_action_date,
                current_version_path,
            )
            if clean_text(str(part))
        )

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": first_non_empty(short_description, clean_text(str(source_item.get("catchTitle") or "")), bill_num),
            "sponsor": sponsor,
            "billTitle": bill_title,
            "billStatus": first_non_empty(bill_status, last_action),
            "lastAction": last_action or bill_status,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": clean_text(self._xml_text(legislation.find("wa:BillId", WASHINGTON_XML_NS))),
            "sponsorStringHouse": sponsor if bill_num.startswith(("HB", "HCR", "HJR", "HJM", "HR", "HI")) else None,
            "sponsorStringSenate": sponsor if bill_num.startswith(("SB", "SCR", "SJR", "SJM", "SR", "SI", "SGA")) else None,
            "introduced": current_version_path or None,
            "digest": digest_path,
            "summary": official_page,
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": current_fingerprint,
            "summaryHTML": self._paragraph_html(first_non_empty(short_description, bill_num)),
            "digestHTML": self._paragraph_html(first_non_empty(long_description, legal_title, article_text)),
            "currentBillHTML": "",
            "billActions": self._history_rows(legislation),
            "amendments": amendments,
            "officialPage": official_page,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        if not url:
            return ""
        normalized = str(url).replace("http://", "https://", 1)
        return fetch_document_text(self.client, normalized)

    def _feature_data_for_year(self, year: int) -> list[dict[str, Any]]:
        cached = self._feature_rows.get(year)
        if cached is not None:
            return cached

        response = self.service_client.get("/LegislationService.asmx/GetLegislativeBillListFeatureData")
        response.raise_for_status()
        root = ET.fromstring(response.text)
        rows: list[dict[str, Any]] = []
        biennium_start = washington_biennium_start(year)
        for node in root.findall(".//Table"):
            row = {child.tag: clean_text(child.text) for child in node}
            row_year = int(row.get("bienYear") or 0)
            if row_year != biennium_start:
                continue
            rows.append(row)
        self._feature_rows[year] = rows
        return rows

    def _build_list_item(self, year: int, bill_num: str, row: dict[str, Any]) -> dict[str, Any]:
        bill_number = int(clean_text(row.get("legnum")))
        canonical_prefix = canonical_washington_prefix(row.get("prefix"))
        title = first_non_empty(row.get("title"), row.get("sharepointtitle"), bill_num)
        status = clean_text(row.get("status"))
        detail_path = (
            f"{self.settings.washington_site_base.rstrip('/')}/billsummary"
            f"?BillNumber={bill_number}&Year={year}&Initiative=false"
        )
        return {
            "billNum": bill_num,
            "billType": canonical_prefix,
            "catchTitle": title,
            "billTitle": title,
            "sponsor": clean_text(row.get("sponsor")),
            "billStatus": status,
            "lastAction": status,
            "lastActionDate": "",
            "signedDate": "",
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": f"{canonical_prefix} {bill_number}",
            "detailPath": detail_path,
            "currentVersionPath": None,
            "currentVersionFingerprint": "|".join(
                part for part in (clean_text(row.get("sharepointtitle")), status, row.get("passedLegislature")) if part
            ),
            "baseBillId": f"{canonical_prefix} {bill_number}",
            "billNumber": bill_number,
            "biennium": washington_biennium(year),
            "sourceYear": year,
            "rowRank": self._row_rank(row),
        }

    @staticmethod
    def _row_rank(row: dict[str, Any]) -> int:
        prefix = clean_text(row.get("prefix")).upper().replace(" ", "")
        score = 0
        if prefix.startswith("2"):
            score += 30
        if "E" in prefix:
            score += 20
        if "S" in prefix[:-2]:
            score += 10
        if clean_text(row.get("passedLegislature")).lower() == "yes":
            score += 5
        return score

    def _fetch_legislation(self, biennium: str, bill_number: int) -> ET.Element:
        response = self.service_client.get(
            "/LegislationService.asmx/GetLegislation",
            params={"biennium": biennium, "billNumber": str(bill_number)},
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        legislation = root.find("wa:Legislation", WASHINGTON_XML_NS)
        if legislation is None:
            raise ValueError(f"Washington legislation detail did not include {bill_number}")
        return legislation

    def _fetch_sponsors(self, biennium: str, bill_id: str) -> list[str]:
        response = self.service_client.get(
            "/LegislationService.asmx/GetSponsors",
            params={"biennium": biennium, "billId": bill_id},
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        sponsors: list[str] = []
        for node in root.findall("wa:Sponsor", WASHINGTON_XML_NS):
            long_name = clean_text(self._xml_text(node.find("wa:LongName", WASHINGTON_XML_NS)))
            if long_name and long_name not in sponsors:
                sponsors.append(long_name)
        return sponsors

    def _fetch_amendments(self, biennium: str, bill_number: int) -> list[dict[str, Any]]:
        response = self.service_client.get(
            "/LegislationService.asmx/GetAmendmentsForBiennium",
            params={"biennium": biennium, "billNumber": str(bill_number)},
        )
        response.raise_for_status()
        root = ET.fromstring(response.text)
        payloads: list[dict[str, Any]] = []
        for node in root.findall("wa:Amendment", WASHINGTON_XML_NS):
            name = clean_text(self._xml_text(node.find("wa:Name", WASHINGTON_XML_NS)))
            if not name:
                continue
            payloads.append(
                {
                    "amendmentNumber": name,
                    "house": clean_text(self._xml_text(node.find("wa:Agency", WASHINGTON_XML_NS))),
                    "order": clean_text(self._xml_text(node.find("wa:FloorNumber", WASHINGTON_XML_NS))),
                    "sequence": 0,
                    "status": clean_text(
                        first_non_empty(
                            self._xml_text(node.find("wa:FloorAction", WASHINGTON_XML_NS)),
                            self._xml_text(node.find("wa:Description", WASHINGTON_XML_NS)),
                        )
                    ),
                    "sponsor": clean_text(self._xml_text(node.find("wa:SponsorName", WASHINGTON_XML_NS))),
                    "documentUrl": self._normalize_external_url(
                        first_non_empty(
                            self._xml_text(node.find("wa:PdfUrl", WASHINGTON_XML_NS)),
                            self._xml_text(node.find("wa:HtmUrl", WASHINGTON_XML_NS)),
                        )
                    ),
                }
            )
        return payloads

    @staticmethod
    def _normalize_external_url(url: str | None) -> str:
        value = clean_text(url)
        if not value:
            return ""
        return value.replace("http://", "https://", 1)

    @staticmethod
    def _history_rows(legislation: ET.Element) -> list[dict[str, str]]:
        current_status = legislation.find("wa:CurrentStatus", WASHINGTON_XML_NS)
        if current_status is None:
            return []
        history_line = clean_text(WashingtonApiClient._xml_text(current_status.find("wa:HistoryLine", WASHINGTON_XML_NS)))
        action_date = parse_washington_date(
            WashingtonApiClient._xml_text(current_status.find("wa:ActionDate", WASHINGTON_XML_NS))
        )
        status = clean_text(WashingtonApiClient._xml_text(current_status.find("wa:Status", WASHINGTON_XML_NS)))
        if not history_line and not status:
            return []
        return [
            {
                "location": clean_text(WashingtonApiClient._xml_text(legislation.find("wa:OriginalAgency", WASHINGTON_XML_NS))),
                "statusDate": action_date,
                "statusMessage": first_non_empty(history_line, status),
            }
        ]

    @staticmethod
    def _session_law_chapter(legislation: ET.Element) -> str:
        status = legislation.find("wa:CurrentStatus", WASHINGTON_XML_NS)
        history_line = clean_text(WashingtonApiClient._xml_text(status.find("wa:HistoryLine", WASHINGTON_XML_NS) if status is not None else None))
        match = re.search(r"\bchapter\s+(\d+)\b", history_line, re.IGNORECASE)
        if match is None:
            return ""
        return f"Chapter {match.group(1)}"

    @staticmethod
    def _has_fiscal_note(legislation: ET.Element) -> bool:
        for tag_name in ("wa:StateFiscalNote", "wa:LocalFiscalNote"):
            if WashingtonApiClient._xml_text(legislation.find(tag_name, WASHINGTON_XML_NS)).lower() == "true":
                return True
        return False

    @staticmethod
    def _fiscal_note_url(year: int, bill_number: int) -> str:
        return f"https://fnspublic.ofm.wa.gov/FNSPublicSearch/Search/bill/{bill_number}/{washington_legislature_number(year)}"

    @staticmethod
    def _base_bill_id(bill_num: str) -> str:
        match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
        if match is None:
            return bill_num
        return f"{match.group(1)} {int(match.group(2))}"

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = clean_text(value)
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _xml_text(node: ET.Element | None) -> str:
        if node is None or node.text is None:
            return ""
        return node.text

    @staticmethod
    def _article_text(soup: BeautifulSoup) -> str:
        article = soup.find("article")
        if article is None:
            return ""
        return " ".join(article.get_text(" ", strip=True).split())

    @staticmethod
    def _original_bill_url(soup: BeautifulSoup) -> str:
        for anchor in soup.find_all("a", href=True):
            text = " ".join(anchor.get_text(" ", strip=True).split()).lower()
            href = str(anchor.get("href") or "")
            if "view original bill" in text:
                return absolute_url("https://app.leg.wa.gov/billsummary/", href) or href
        return ""
