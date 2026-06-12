from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


UTAH_BILL_PATH_PATTERN = re.compile(r"/~(?P<year>\d{4})/bills/static/(?P<bill>[A-Z]+\d+)\.html", re.IGNORECASE)


def parse_utah_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_utah_bill_number(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    match = re.fullmatch(r"([A-Z]+)(\d+)", raw)
    if match is None:
        return ""
    prefix = match.group(1)
    number = int(match.group(2))
    width = len(match.group(2))
    return f"{prefix}{number:0{width}d}"


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class UtahApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.utah_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        response = self.client.get("/billlist.jsp", params={"session": f"{year}GS"})
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in soup.select("a.billlink[href]"):
            href = str(link.get("href") or "")
            match = UTAH_BILL_PATH_PATTERN.search(href)
            if match is None or int(match.group("year")) != year:
                continue
            bill_num = normalize_utah_bill_number(match.group("bill"))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)

            title = ""
            sponsor = ""
            list_item = link.find_parent("li")
            if list_item is not None:
                title_node = list_item.find("b")
                sponsor_node = list_item.find("i")
                title = clean_text(title_node.get_text(" ", strip=True) if title_node is not None else "")
                sponsor = clean_text(sponsor_node.get_text(" ", strip=True) if sponsor_node is not None else "")

            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title or bill_num,
                    "billTitle": title or bill_num,
                    "sponsor": sponsor,
                    "billStatus": "",
                    "lastAction": "",
                    "lastActionDate": "",
                    "detailPath": href,
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        match = UTAH_BILL_PATH_PATTERN.search(detail_path)
        if match is None:
            raise ValueError(f"Utah bill detail path could not be parsed: {detail_path}")
        year = int(match.group("year"))
        bill_num = normalize_utah_bill_number(match.group("bill"))
        session_id = f"{year}GS"

        json_response = self.client.get(f"/data/{session_id}/{bill_num}.json")
        json_response.raise_for_status()
        payload = json_response.json()

        active_version = self._active_version(payload)
        introduced_doc = self._find_bill_doc(payload, active_version, short_desc="Introduced")
        current_doc = (
            self._find_bill_doc(payload, active_version, file_type="Enrolled")
            or self._find_bill_doc(payload, active_version, short_desc_prefix="Substitute")
            or self._find_bill_doc(payload, active_version, short_desc="Introduced")
        )
        digest_doc = self._find_bill_doc(payload, active_version, file_type="PubFN")
        actions = self._action_rows(payload)
        last_action = clean_text(str(payload.get("lastAction") or ""))
        last_action_date = parse_utah_date(str(payload.get("lastActionDate") or ""))
        sponsor = clean_text(str(payload.get("primeSponsorName") or "")) or clean_text(str((item or {}).get("sponsor") or ""))
        current_version_path = absolute_url(self.settings.utah_site_base, current_doc.get("url") if current_doc else None)
        introduced_path = absolute_url(self.settings.utah_site_base, introduced_doc.get("url") if introduced_doc else None)
        digest_path = absolute_url(self.settings.utah_site_base, digest_doc.get("url") if digest_doc else None)
        fingerprint_parts = []
        if active_version is not None:
            for doc in active_version.get("billDocs", []):
                url = absolute_url(self.settings.utah_site_base, doc.get("url"))
                if url:
                    fingerprint_parts.append(f"{doc.get('shortDesc')}:{url}:{doc.get('fileDate')}")

        signed_date = last_action_date if "governor signed" in last_action.lower() else ""
        summary_html = self._paragraph_html(str(payload.get("generalProvisions") or "").strip() or str(payload.get("shortTitle") or bill_num))
        digest_html = self._highlighted_provisions_html(str(payload.get("highlightedProvisions") or ""))

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": clean_text(str(payload.get("shortTitle") or "")) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num,
            "sponsor": sponsor,
            "billTitle": clean_text(str(payload.get("shortTitle") or "")) or clean_text(str((item or {}).get("billTitle") or "")) or bill_num,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": clean_text(str(current_doc.get("shortDesc") or "")) if current_doc else "",
            "sponsorStringHouse": sponsor if str(payload.get("primeSponsorHouse") or "").upper() == "H" else None,
            "sponsorStringSenate": sponsor if str(payload.get("primeSponsorHouse") or "").upper() == "S" else None,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": detail_path,
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(fingerprint_parts),
            "summaryHTML": summary_html,
            "digestHTML": digest_html,
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": [],
            "officialPage": detail_path,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _action_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for item in payload.get("actionHistoryList", []):
            rows.append(
                {
                    "location": clean_text(str(item.get("owner") or "")),
                    "statusDate": parse_utah_date(str(item.get("actionDate") or "")),
                    "statusMessage": clean_text(str(item.get("description") or "")),
                }
            )
        rows.sort(key=lambda row: (row.get("statusDate") or "", row.get("statusMessage") or ""))
        return rows

    @staticmethod
    def _active_version(payload: dict[str, Any]) -> dict[str, Any] | None:
        for version in payload.get("billVersionList", []):
            if version.get("activeVersion"):
                return version
        versions = payload.get("billVersionList", [])
        if versions:
            return versions[-1]
        return None

    @staticmethod
    def _find_bill_doc(
        payload: dict[str, Any],
        active_version: dict[str, Any] | None,
        *,
        file_type: str | None = None,
        short_desc: str | None = None,
        short_desc_prefix: str | None = None,
    ) -> dict[str, Any] | None:
        versions = payload.get("billVersionList", [])
        search_versions = [active_version] if active_version is not None else []
        search_versions.extend(version for version in versions if version is not active_version)
        for version in search_versions:
            if version is None:
                continue
            for doc in version.get("billDocs", []):
                doc_type = str(doc.get("fileType") or "")
                doc_desc = clean_text(str(doc.get("shortDesc") or ""))
                if file_type and doc_type != file_type:
                    continue
                if short_desc and doc_desc != short_desc:
                    continue
                if short_desc_prefix and not doc_desc.startswith(short_desc_prefix):
                    continue
                return doc
        return None

    @staticmethod
    def _paragraph_html(value: str) -> str:
        text = clean_text(value)
        if not text:
            return ""
        return f"<p>{text}</p>"

    @staticmethod
    def _highlighted_provisions_html(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        pieces = []
        for part in raw.replace("<ltbullet>", "\n* ").replace("<hr>", "\n").splitlines():
            cleaned = clean_text(part.replace("* ", ""))
            if cleaned:
                pieces.append(cleaned)
        if not pieces:
            return ""
        if len(pieces) == 1:
            return f"<p>{pieces[0]}</p>"
        items = "".join(f"<li>{item}</li>" for item in pieces)
        return f"<ul>{items}</ul>"
