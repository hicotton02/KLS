from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


OHIO_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HR|SR|HCR|SCR|HJR|SJR)\d+$", re.IGNORECASE)


def parse_ohio_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_ohio_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if OHIO_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class OhioApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.ohio_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            verify=False,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        general_assembly = self.general_assembly_for_year(year)
        response = self.client.get(f"/api/v2/general_assembly_{general_assembly}/legislation/")
        response.raise_for_status()
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for row in response.json():
            bill_num = normalize_ohio_bill_number(clean_text(row.get("number")))
            if not bill_num or bill_num in seen:
                continue
            seen.add(bill_num)
            title = clean_text(row.get("short_title")) or clean_text(row.get("name")) or bill_num
            detail_path = absolute_url(self.settings.ohio_site_base, f"/api/v2/general_assembly_{general_assembly}/legislation/{clean_text(row.get('number'))}/")
            current_version_path = absolute_url(
                self.settings.ohio_site_base,
                clean_text(row.get("download_html")) or clean_text(row.get("download")),
            )
            items.append(
                {
                    "billNum": bill_num,
                    "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                    "catchTitle": title,
                    "billTitle": title,
                    "sponsor": self._primary_sponsor(row),
                    "billStatus": clean_text(row.get("version")),
                    "lastAction": clean_text(row.get("version")),
                    "lastActionDate": parse_ohio_date(row.get("governor_signed_date") or row.get("effective_date")),
                    "signedDate": parse_ohio_date(row.get("governor_signed_date")),
                    "effectiveDate": parse_ohio_date(row.get("effective_date")),
                    "chapter": "",
                    "enrolledNumber": clean_text(row.get("name")) or bill_num,
                    "detailPath": detail_path,
                    "currentVersionPath": current_version_path,
                    "currentVersionFingerprint": "|".join(
                        part
                        for part in (
                            clean_text(str(row.get("revno") or "")),
                            current_version_path or "",
                            clean_text(row.get("version")),
                            parse_ohio_date(row.get("governor_signed_date")),
                            parse_ohio_date(row.get("effective_date")),
                        )
                        if part
                    ),
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()
        versions = response.json()
        if not versions:
            raise ValueError(f"Ohio bill detail could not be read from {detail_path}")

        current = versions[-1]
        introduced = versions[0]
        number = clean_text(current.get("number")) or clean_text(introduced.get("number"))
        bill_num = normalize_ohio_bill_number(number)
        if not bill_num:
            raise ValueError(f"Ohio bill number could not be determined from {detail_path}")

        amendments_path = clean_text(current.get("amendments")) or clean_text(introduced.get("amendments"))
        amendments: list[dict[str, Any]] = []
        if amendments_path:
            amendment_response = self.client.get(amendments_path)
            amendment_response.raise_for_status()
            amendments = self._amendments(amendment_response.json())

        subjects = [clean_text(subject) for subject in (current.get("subjects") or []) if clean_text(subject)]
        digest_bits: list[str] = []
        if clean_text(current.get("local_impact_statement")):
            digest_bits.append(f"Local impact: {clean_text(current.get('local_impact_statement'))}")
        if subjects:
            digest_bits.append(f"Subjects: {'; '.join(subjects)}")
        if clean_text(current.get("version")):
            digest_bits.append(f"Current version: {clean_text(current.get('version'))}")

        bill_actions = [
            {
                "statusDate": parse_ohio_date(version.get("governor_signed_date") or version.get("effective_date")),
                "statusMessage": clean_text(version.get("version")),
                "location": "",
            }
            for version in versions
            if clean_text(version.get("version"))
        ]

        general_assembly = self.general_assembly_from_detail_path(detail_path)
        official_page = absolute_url(self.settings.ohio_public_base, f"/legislation/{general_assembly}/{number.lower()}") if general_assembly else None
        current_version_path = absolute_url(
            self.settings.ohio_site_base,
            clean_text(current.get("download_html")) or clean_text(current.get("download")),
        )
        introduced_path = absolute_url(
            self.settings.ohio_site_base,
            clean_text(introduced.get("download_html")) or clean_text(introduced.get("download")),
        ) or current_version_path
        sponsor_names = [clean_text(sponsor.get("full_name")) for sponsor in (current.get("sponsors") or []) if clean_text(sponsor.get("full_name"))]
        sponsor = first_non_empty(self._primary_sponsor(current), self._primary_sponsor(introduced), clean_text((item or {}).get("sponsor")))

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": first_non_empty(clean_text(current.get("short_title")), clean_text((item or {}).get("catchTitle")), bill_num),
            "sponsor": sponsor,
            "billTitle": first_non_empty(clean_text(current.get("short_title")), clean_text((item or {}).get("billTitle")), bill_num),
            "billStatus": clean_text(current.get("version")),
            "lastAction": clean_text(current.get("version")),
            "lastActionDate": parse_ohio_date(clean_text(current.get("governor_signed_date")) or clean_text(current.get("effective_date"))),
            "signedDate": parse_ohio_date(current.get("governor_signed_date")),
            "effectiveDate": parse_ohio_date(current.get("effective_date")),
            "chapter": "",
            "enrolledNumber": clean_text(current.get("name")) or bill_num,
            "sponsorStringHouse": ", ".join(sponsor_names) if clean_text(current.get("chamber")).lower() == "house" and sponsor_names else None,
            "sponsorStringSenate": ", ".join(sponsor_names) if clean_text(current.get("chamber")).lower() == "senate" and sponsor_names else None,
            "introduced": introduced_path,
            "digest": current_version_path,
            "summary": official_page,
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    clean_text(str(current.get("revno") or "")),
                    current_version_path or "",
                    clean_text(current.get("version")),
                    parse_ohio_date(current.get("governor_signed_date")),
                    parse_ohio_date(current.get("effective_date")),
                    str(len(amendments)),
                )
                if part
            ),
            "summaryHTML": f"<p>{html.escape(first_non_empty(clean_text(current.get('long_title')), clean_text(current.get('short_title')), bill_num))}</p>",
            "digestHTML": "".join(f"<p>{html.escape(bit)}</p>" for bit in digest_bits if bit),
            "currentBillHTML": "",
            "billActions": bill_actions,
            "amendments": amendments,
            "officialPage": official_page,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def general_assembly_for_year(year: int) -> int:
        return max(1, (int(year) - 1753) // 2)

    @staticmethod
    def general_assembly_from_detail_path(detail_path: str) -> str:
        match = re.search(r"/general_assembly_(\d+)/", detail_path, re.IGNORECASE)
        return clean_text(match.group(1) if match else "")

    @staticmethod
    def _primary_sponsor(row: dict[str, Any]) -> str:
        sponsors = row.get("sponsors") or []
        return clean_text(sponsors[0].get("full_name")) if sponsors else ""

    def _amendments(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        amendments: list[dict[str, Any]] = []
        seen: set[str] = set()
        for amendment in payload:
            amendment_number = clean_text(amendment.get("amendment_number")).upper()
            if not amendment_number or amendment_number in seen:
                continue
            seen.add(amendment_number)
            sponsor = self._primary_sponsor(amendment)
            summary_parts = [clean_text(amendment.get("version"))]
            if sponsor:
                summary_parts.append(f"Sponsor: {sponsor}")
            amendments.append(
                {
                    "amendmentNumber": amendment_number,
                    "adoptedDate": "",
                    "documentUrl": absolute_url(
                        self.settings.ohio_site_base,
                        clean_text(amendment.get("html_link")) or clean_text(amendment.get("link")),
                    ),
                    "summaryText": ". ".join(part for part in summary_parts if part),
                    "source": "Ohio Legislature SOLAR API",
                }
            )
        return amendments
