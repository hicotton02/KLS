from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from ftplib import FTP
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


TEXAS_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HCR|SCR|HJR|SJR|HR|SR)\d+$", re.IGNORECASE)
TEXAS_CATEGORIES = (
    "house_bills",
    "senate_bills",
    "house_concurrent_resolutions",
    "senate_concurrent_resolutions",
    "house_joint_resolutions",
    "senate_joint_resolutions",
    "house_resolutions",
    "senate_resolutions",
)


def parse_texas_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_texas_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if TEXAS_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_texas_bill_number(value: str | None) -> str:
    normalized = normalize_texas_bill_number(value)
    if normalized:
        return normalized
    match = re.search(r"\b(HB|SB|HCR|SCR|HJR|SJR|HR|SR)\s*(\d+)\b", clean_text(value), re.IGNORECASE)
    if match is None:
        return ""
    return normalize_texas_bill_number(f"{match.group(1)}{match.group(2)}")


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class TexasApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.texas_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_code = self.session_code_for_year(year)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for category in TEXAS_CATEGORIES:
            base_path = f"/bills/{session_code}/billhistory/{category}"
            for range_dir in self._ftp_listdir(base_path):
                for filename in self._ftp_listdir(f"{base_path}/{range_dir}"):
                    if not filename.lower().endswith(".xml"):
                        continue
                    bill_num = parse_texas_bill_number(filename)
                    if not bill_num or bill_num in seen:
                        continue
                    detail_path = f"{base_path}/{range_dir}/{filename}"
                    seen.add(bill_num)
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
                            "signedDate": "",
                            "effectiveDate": "",
                            "chapter": "",
                            "enrolledNumber": bill_num,
                            "detailPath": detail_path,
                            "currentVersionPath": None,
                            "currentVersionFingerprint": detail_path,
                        }
                    )
        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        session_code = self._session_code_from_path(detail_path)
        return self._parse_bill_xml(self._ftp_read_text(detail_path), detail_path, session_code)

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def session_code_for_year(year: int) -> str:
        regular_year = int(year) if int(year) % 2 == 1 else int(year) - 1
        legislature = max(1, (regular_year - 1847) // 2)
        return f"{legislature}R"

    @staticmethod
    def _session_code_from_path(path: str) -> str:
        match = re.search(r"/bills/([^/]+)/billhistory/", path, re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    def _ftp_listdir(self, path: str) -> list[str]:
        ftp = FTP(self.settings.texas_ftp_host, timeout=self.settings.request_timeout_seconds)
        try:
            ftp.login()
            names: list[str] = []
            ftp.cwd(path)
            ftp.retrlines("NLST", names.append)
            return sorted(name for name in names if clean_text(name))
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()

    def _ftp_read_text(self, path: str) -> str:
        ftp = FTP(self.settings.texas_ftp_host, timeout=self.settings.request_timeout_seconds)
        chunks: list[bytes] = []
        try:
            ftp.login()
            ftp.retrbinary(f"RETR {path}", chunks.append)
        finally:
            try:
                ftp.quit()
            except Exception:
                ftp.close()
        return b"".join(chunks).decode("utf-8-sig", errors="ignore")

    def _parse_bill_xml(self, raw_xml: str, detail_path: str, session_code: str) -> dict[str, Any]:
        root = ET.fromstring(raw_xml)
        bill_num = parse_texas_bill_number(root.attrib.get("bill"))
        if not bill_num:
            raise ValueError(f"Texas bill number could not be parsed from {detail_path}")

        title = clean_text(root.findtext("caption")) or bill_num
        authors = clean_text(root.findtext("authors"))
        coauthors = clean_text(root.findtext("coauthors"))
        sponsor = authors or clean_text(root.findtext("sponsors"))
        last_action = clean_text(root.findtext("lastaction"))
        last_action_date = parse_texas_date(last_action[:10]) if re.match(r"\d{2}/\d{2}/\d{4}", last_action) else ""

        actions = self._actions(root)
        billtext_versions = root.findall("./billtext/docTypes/bill/versions/version")
        introduced_version = billtext_versions[0] if billtext_versions else None
        current_version = billtext_versions[-1] if billtext_versions else None
        introduced_path = self._document_link(introduced_version)
        current_version_path = self._document_link(current_version) or introduced_path
        analysis_versions = root.findall("./billtext/docTypes/analysis/versions/version")
        digest_path = self._document_link(analysis_versions[-1]) if analysis_versions else current_version_path

        signed_date = ""
        effective_date = ""
        chapter = ""
        for action in actions:
            message = clean_text(action.get("statusMessage"))
            lowered = message.lower()
            if not signed_date and "signed by the governor" in lowered:
                signed_date = clean_text(action.get("statusDate"))
            if not effective_date and "effective on" in lowered:
                effective_date = clean_text(action.get("statusDate"))
            if not chapter:
                chapter_match = re.search(r"\bChapter\s+(\d+)\b", message, re.IGNORECASE)
                if chapter_match is not None:
                    chapter = clean_text(chapter_match.group(1))

        subjects = [clean_text(node.text) for node in root.findall("./subjects/subject") if clean_text(node.text)]
        committees = []
        for chamber in root.findall("./committees/*"):
            name = clean_text(chamber.attrib.get("name"))
            status = clean_text(chamber.attrib.get("status"))
            if name:
                committees.append(f"{name} ({status})" if status else name)

        digest_bits: list[str] = []
        if subjects:
            digest_bits.append(f"Subjects: {'; '.join(subjects)}")
        if committees:
            digest_bits.append(f"Committees: {'; '.join(committees)}")
        if coauthors:
            digest_bits.append(f"Coauthors: {coauthors}")

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": sponsor,
            "billTitle": title,
            "billStatus": last_action or title,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter,
            "enrolledNumber": clean_text(root.attrib.get("bill")) or bill_num,
            "sponsorStringHouse": authors if bill_num.startswith(("HB", "HCR", "HJR", "HR")) and authors else None,
            "sponsorStringSenate": authors if bill_num.startswith(("SB", "SCR", "SJR", "SR")) and authors else None,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": absolute_url(
                self.settings.texas_site_base,
                f"/BillLookup/History.aspx?LegSess={session_code}&Bill={bill_num}",
            ),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    clean_text(root.attrib.get("lastUpdate")),
                    current_version_path or "",
                    introduced_path or "",
                    last_action,
                    str(len(actions)),
                )
                if part
            ),
            "summaryHTML": f"<p>{html.escape(title)}</p>",
            "digestHTML": "".join(f"<p>{html.escape(bit)}</p>" for bit in digest_bits if bit),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": absolute_url(
                self.settings.texas_site_base,
                f"/BillLookup/History.aspx?LegSess={session_code}&Bill={bill_num}",
            ),
        }

    @staticmethod
    def _actions(root: ET.Element) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        for action in root.findall("./actions/action"):
            action_date = parse_texas_date(action.findtext("date"))
            description = clean_text(action.findtext("description"))
            comment = clean_text(action.findtext("comment"))
            message = description if not comment else f"{description}. {comment}"
            if not message:
                continue
            items.append(
                {
                    "statusDate": action_date,
                    "statusMessage": message,
                    "location": "",
                }
            )
        return items

    @staticmethod
    def _document_link(version: ET.Element | None) -> str | None:
        if version is None:
            return None
        for tag in ("WebHTMLURL", "WebPDFURL", "FTPHTMLURL", "FTPPDFURL"):
            url = clean_text(version.findtext(tag))
            if url and url.lower().startswith("http"):
                return url
        return None
