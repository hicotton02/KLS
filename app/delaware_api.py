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


DELAWARE_FEED_NAMES = (
    "IntroducedLegislation",
    "CommitteeLegislation",
    "StrickenLegislation",
    "OutOfCommitteeLegislation",
    "InLieuLegislation",
    "HousePassedLegislation",
    "SenatePassedLegislation",
    "GovernorSignedLegislation",
)
DELAWARE_TOKEN_PATTERN = re.compile(r"[A-Z]+|\d+")
DELAWARE_STATUS_DATE_PATTERN = re.compile(r"^(?P<status>.+?)\s+(?P<date>\d{1,2}/\d{1,2}/\d{2,4})$")
DELAWARE_CHAPTER_PATTERN = re.compile(r"(?:chapter|chp)\s*0*(\d+)", re.IGNORECASE)
DELAWARE_HEADING_PATTERNS = (
    (re.compile(r"^House Concurrent Resolution\s+(\d+)$", re.IGNORECASE), "HCR"),
    (re.compile(r"^Senate Concurrent Resolution\s+(\d+)$", re.IGNORECASE), "SCR"),
    (re.compile(r"^House Joint Resolution\s+(\d+)$", re.IGNORECASE), "HJR"),
    (re.compile(r"^Senate Joint Resolution\s+(\d+)$", re.IGNORECASE), "SJR"),
    (re.compile(r"^House Bill\s+(\d+)$", re.IGNORECASE), "HB"),
    (re.compile(r"^Senate Bill\s+(\d+)$", re.IGNORECASE), "SB"),
    (re.compile(r"^House Resolution\s+(\d+)$", re.IGNORECASE), "HR"),
    (re.compile(r"^Senate Resolution\s+(\d+)$", re.IGNORECASE), "SR"),
)


def delaware_session_start_year(year: int) -> int:
    value = int(year)
    return value if value % 2 == 1 else value - 1


def delaware_general_assembly(year: int) -> int:
    session_start = delaware_session_start_year(year)
    return 153 + ((session_start - 2025) // 2)


def parse_delaware_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if raw[:4].isdigit() and "-" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%y", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if "T" in raw and raw[:4].isdigit():
        return raw.split("T", 1)[0]
    return raw


def normalize_delaware_bill_number(value: str | None) -> str:
    tokens = DELAWARE_TOKEN_PATTERN.findall(clean_text(value).upper())
    if not tokens:
        return ""
    return "".join(tokens)


def _sort_bill_key(bill_num: str) -> tuple[tuple[int, str | int], ...]:
    parts = DELAWARE_TOKEN_PATTERN.findall(str(bill_num or "").upper())
    if not parts:
        return ((0, str(bill_num or "").upper()),)
    key: list[tuple[int, str | int]] = []
    for part in parts:
        if part.isdigit():
            key.append((1, int(part)))
        else:
            key.append((0, part))
    return tuple(key)


def _first_sentence(text: str | None) -> str:
    raw = clean_text(text)
    if not raw:
        return ""
    match = re.split(r"(?<=[.!?])\s+", raw, maxsplit=1)
    return clean_text(match[0]) if match else raw


class DelawareApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.delaware_site_base,
            headers={
                "User-Agent": "keeping-law-simple/1.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._amendment_detail_cache: dict[int, dict[str, str]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_start = delaware_session_start_year(year)
        general_assembly = delaware_general_assembly(session_start)
        items_by_legislation_id: dict[int, dict[str, Any]] = {}

        for feed_name in DELAWARE_FEED_NAMES:
            response = self.client.get(
                f"/json/JsonFeed/{feed_name}",
                params={"legislationTypeId": 1, "sort": "asc", "selectedGA": general_assembly},
            )
            response.raise_for_status()
            payload = response.json()
            for raw_item in payload.get("Items") or []:
                normalized = self._normalize_feed_item(raw_item, session_start)
                if normalized is None:
                    continue
                legislation_id = int(normalized["legislationId"])
                existing = items_by_legislation_id.get(legislation_id)
                if existing is None:
                    items_by_legislation_id[legislation_id] = normalized
                    continue
                items_by_legislation_id[legislation_id] = self._merge_feed_items(existing, normalized)

        return sorted(items_by_legislation_id.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        legislation_id = self._legislation_id_from_value((item or {}).get("legislationId") or detail_path)
        if legislation_id <= 0:
            raise ValueError(f"Delaware legislation id could not be determined from {detail_path}")

        recent_reports = self._fetch_grid_data(f"/json/BillDetail/GetRecentReportsByLegislationId?legislationId={legislation_id}")
        roll_calls = self._fetch_grid_data(f"/json/BillDetail/GetVotingReportsByLegislationId?legislationId={legislation_id}")
        related_amendments = self._normalize_related_amendments(
            self._fetch_grid_data(f"/json/BillDetail/GetRelatedAmendmentsByLegislationId?legislationId={legislation_id}")
        )

        response = self.client.get(detail_path)
        if response.status_code >= 400:
            return self._build_fallback_detail(
                item=item,
                detail_path=detail_path,
                legislation_id=legislation_id,
                recent_reports=recent_reports,
                roll_calls=roll_calls,
                related_amendments=related_amendments,
            )
        response.raise_for_status()
        if self._is_gateway_forbidden(response.text):
            return self._build_fallback_detail(
                item=item,
                detail_path=str(response.url),
                legislation_id=legislation_id,
                recent_reports=recent_reports,
                roll_calls=roll_calls,
                related_amendments=related_amendments,
            )

        soup = BeautifulSoup(response.text, "html.parser")
        sections = self._section_map(soup)
        progress = self._section_fields(sections.get("Progress"))
        details = self._section_fields(sections.get("Details"))
        if not details:
            return self._build_fallback_detail(
                item=item,
                detail_path=str(response.url),
                legislation_id=legislation_id,
                recent_reports=recent_reports,
                roll_calls=roll_calls,
                related_amendments=related_amendments,
            )

        text_links = self._section_link_groups(sections.get("Text"), str(response.url))
        session_law_links = self._section_link_groups(sections.get("Session Laws"), str(response.url))
        actions = self._recent_reports_to_actions(recent_reports)
        status_text = clean_text(progress.get("Status"))
        display_status = self._preferred_status_text(status_text, actions[-1]["statusMessage"] if actions else "")
        next_step = clean_text(progress.get("What typically happens next?"))
        introduced_on = parse_delaware_date(details.get("Introduced on"))
        primary_sponsor = clean_text(details.get("Primary Sponsor"))
        additional_sponsors = clean_text(details.get("Additional Sponsor(s)"))
        co_sponsors = clean_text(details.get("Co-Sponsor(s)"))
        long_title = clean_text(details.get("Long Title"))
        synopsis = clean_text(details.get("Original Synopsis")) or clean_text((item or {}).get("synopsis"))
        heading = soup.find("h2")
        heading_text = clean_text(heading.get_text(" ", strip=True)) if heading is not None else ""
        display_code = first_non_empty(clean_text((item or {}).get("displayCode")), self._display_code_from_heading(heading_text))
        bill_num = first_non_empty(
            normalize_delaware_bill_number((item or {}).get("billNum")),
            normalize_delaware_bill_number(display_code),
        )
        if not bill_num:
            raise ValueError(f"Delaware bill number could not be determined from {detail_path}")

        current_version_path = self._select_preferred_document_url(text_links) or self._select_preferred_document_url(session_law_links)
        introduced_path = self._select_original_document_url(text_links) or current_version_path
        chapter = first_non_empty(
            self._chapter_from_link_groups(session_law_links),
            self._chapter_from_text(status_text),
            self._chapter_from_reports(recent_reports),
        )
        last_action = first_non_empty(
            actions[-1]["statusMessage"] if actions else "",
            self._status_without_date(status_text),
            status_text,
        )
        last_action_date = first_non_empty(
            actions[-1]["statusDate"] if actions else "",
            self._date_from_status(status_text),
            introduced_on,
        )
        signed_date = ""
        if "signed" in status_text.lower() or "signed by governor" in last_action.lower():
            signed_date = first_non_empty(last_action_date, self._date_from_status(status_text))

        title = first_non_empty(
            long_title,
            clean_text((item or {}).get("catchTitle")),
            clean_text((item or {}).get("billTitle")),
            _first_sentence(synopsis),
            display_code,
            bill_num,
        )
        digest_bits = [
            f"Status: {display_status}" if display_status else "",
            f"What typically happens next: {next_step}" if next_step else "",
            f"Introduced on: {introduced_on}" if introduced_on else "",
            f"Primary sponsor: {primary_sponsor}" if primary_sponsor else "",
            f"Additional sponsors: {additional_sponsors}" if additional_sponsors else "",
            f"Co-sponsors: {co_sponsors}" if co_sponsors else "",
        ]
        digest_bits.extend(self._roll_call_summaries(roll_calls))
        digest_bits.extend(
            f"{action['statusDate']}: {action['statusMessage']}".strip(": ")
            for action in actions[-3:]
            if action.get("statusMessage")
        )
        current_fingerprint = "|".join(
            part
            for part in (
                display_code,
                current_version_path,
                introduced_path,
                self._select_preferred_document_url(session_law_links),
                status_text,
                last_action,
                last_action_date,
                chapter,
                str(len(related_amendments)),
            )
            if clean_text(str(part))
        )

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": primary_sponsor,
            "billTitle": title,
            "billStatus": first_non_empty(display_status, status_text, last_action),
            "lastAction": first_non_empty(last_action, status_text),
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": display_code or bill_num,
            "sponsorStringHouse": primary_sponsor if bill_num.startswith(("H", "HA", "HS")) else None,
            "sponsorStringSenate": primary_sponsor if bill_num.startswith(("S", "SA", "SS")) else None,
            "introduced": introduced_path or None,
            "digest": str(response.url),
            "summary": str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": current_fingerprint,
            "summaryHTML": self._paragraph_html(*[bit for bit in (long_title, synopsis) if bit]),
            "digestHTML": self._paragraph_html(*[bit for bit in digest_bits if bit]),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": related_amendments,
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        normalized = absolute_url(self.settings.delaware_site_base, url) or ""
        try:
            return fetch_document_text(self.client, normalized)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {403, 404}:
                return ""
            raise

    def _normalize_feed_item(self, raw_item: dict[str, Any], session_start: int) -> dict[str, Any] | None:
        detail_path = absolute_url(self.settings.delaware_site_base, raw_item.get("Link")) or ""
        legislation_id = self._legislation_id_from_value(detail_path)
        if legislation_id <= 0:
            return None
        display_code = clean_text(raw_item.get("Title"))
        bill_num = normalize_delaware_bill_number(display_code)
        if not bill_num:
            return None
        synopsis = clean_text(raw_item.get("Synopsis"))
        title = first_non_empty(clean_text(raw_item.get("LongTitle")), _first_sentence(synopsis), display_code, bill_num)
        introduced_on = parse_delaware_date(raw_item.get("IntroducedDate"))
        fingerprint = "|".join(bit for bit in (display_code, title, synopsis, introduced_on) if bit)
        return {
            "billNum": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "billTitle": title,
            "sponsor": "",
            "billStatus": "",
            "lastAction": "",
            "lastActionDate": introduced_on,
            "signedDate": "",
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": display_code or bill_num,
            "detailPath": detail_path,
            "currentVersionPath": None,
            "currentVersionFingerprint": fingerprint,
            "legislationId": legislation_id,
            "displayCode": display_code,
            "synopsis": synopsis,
            "summaryText": synopsis,
            "year": session_start,
        }

    @staticmethod
    def _merge_feed_items(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        merged = dict(existing)
        for key in ("catchTitle", "billTitle", "synopsis", "summaryText", "displayCode", "currentVersionFingerprint"):
            current_value = clean_text(merged.get(key))
            candidate_value = clean_text(candidate.get(key))
            if len(candidate_value) > len(current_value):
                merged[key] = candidate_value
        if not merged.get("lastActionDate") and candidate.get("lastActionDate"):
            merged["lastActionDate"] = candidate["lastActionDate"]
        return merged

    def _build_fallback_detail(
        self,
        *,
        item: dict[str, Any] | None,
        detail_path: str,
        legislation_id: int,
        recent_reports: list[dict[str, Any]],
        roll_calls: list[dict[str, Any]],
        related_amendments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actions = self._recent_reports_to_actions(recent_reports)
        bill_num = first_non_empty(
            normalize_delaware_bill_number((item or {}).get("billNum")),
            normalize_delaware_bill_number((item or {}).get("displayCode")),
        )
        if not bill_num:
            bill_num = normalize_delaware_bill_number(f"DE{legislation_id}")
        synopsis = clean_text((item or {}).get("synopsis"))
        title = first_non_empty(
            clean_text((item or {}).get("catchTitle")),
            clean_text((item or {}).get("billTitle")),
            _first_sentence(synopsis),
            clean_text((item or {}).get("displayCode")),
            bill_num,
        )
        last_action = first_non_empty(actions[-1]["statusMessage"] if actions else "", clean_text((item or {}).get("billStatus")))
        last_action_date = first_non_empty(actions[-1]["statusDate"] if actions else "", clean_text((item or {}).get("lastActionDate")))
        digest_bits = [bit for bit in (title, synopsis, *self._roll_call_summaries(roll_calls)) if bit]
        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": title,
            "sponsor": clean_text((item or {}).get("sponsor")),
            "billTitle": title,
            "billStatus": first_non_empty(clean_text((item or {}).get("billStatus")), last_action),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": last_action_date if "signed" in last_action.lower() else "",
            "effectiveDate": "",
            "chapter": self._chapter_from_reports(recent_reports),
            "enrolledNumber": clean_text((item or {}).get("displayCode")) or bill_num,
            "sponsorStringHouse": clean_text((item or {}).get("sponsor")) if bill_num.startswith(("H", "HA", "HS")) else None,
            "sponsorStringSenate": clean_text((item or {}).get("sponsor")) if bill_num.startswith(("S", "SA", "SS")) else None,
            "introduced": None,
            "digest": detail_path,
            "summary": detail_path,
            "currentVersionPath": None,
            "currentVersionFingerprint": clean_text((item or {}).get("currentVersionFingerprint")) or detail_path,
            "summaryHTML": self._paragraph_html(*[bit for bit in (title, synopsis) if bit]),
            "digestHTML": self._paragraph_html(*digest_bits),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": related_amendments,
            "officialPage": detail_path,
        }

    def _normalize_related_amendments(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            amendment_legislation_id = self._legislation_id_from_value(item.get("AmendmentLegislationId"))
            if amendment_legislation_id <= 0:
                continue
            amendment_number = first_non_empty(
                clean_text(item.get("ShortAmendmentCode")),
                clean_text(item.get("AmendmentCode")),
                clean_text(item.get("FullTextAmendmentCode")),
            )
            if not amendment_number or amendment_number in seen:
                continue
            seen.add(amendment_number)
            detail_url = f"{self.settings.delaware_site_base.rstrip('/')}/BillDetail?legislationId={amendment_legislation_id}"
            metadata = self._amendment_detail_metadata(amendment_legislation_id, detail_url)
            normalized.append(
                {
                    "amendmentNumber": amendment_number,
                    "house": self._chamber_from_amendment_number(amendment_number),
                    "order": clean_text(str(item.get("AmendmentOrder") or item.get("AmendmentNumber") or "")),
                    "sequence": clean_text(str(item.get("AmendmentDepth") or item.get("AmendmentNumber") or "")),
                    "status": first_non_empty(metadata.get("status"), clean_text(item.get("PublicStatusName"))),
                    "sponsor": clean_text(item.get("PrimarySponsorShortName")),
                    "documentUrl": metadata.get("document_url"),
                    "detailUrl": detail_url,
                }
            )
        return normalized

    def _amendment_detail_metadata(self, legislation_id: int, detail_url: str) -> dict[str, str]:
        cached = self._amendment_detail_cache.get(legislation_id)
        if cached is not None:
            return cached

        payload = {"detail_url": detail_url, "document_url": "", "status": ""}
        response = self.client.get(detail_url)
        if response.status_code < 400 and not self._is_gateway_forbidden(response.text):
            soup = BeautifulSoup(response.text, "html.parser")
            sections = self._section_map(soup)
            progress = self._section_fields(sections.get("Progress"))
            text_links = self._section_link_groups(sections.get("Text"), str(response.url))
            payload["detail_url"] = str(response.url)
            payload["status"] = clean_text(progress.get("Status"))
            payload["document_url"] = self._select_preferred_document_url(text_links)
        self._amendment_detail_cache[legislation_id] = payload
        return payload

    def _section_map(self, soup: BeautifulSoup) -> dict[str, Tag]:
        sections: dict[str, Tag] = {}
        for heading in soup.find_all("h3", class_="section-head"):
            name = clean_text(heading.get_text(" ", strip=True))
            if not name:
                continue
            parent = heading.find_parent("section")
            if parent is not None:
                sections[name] = parent
        return sections

    @staticmethod
    def _section_fields(section: Tag | None) -> dict[str, str]:
        if section is None:
            return {}
        fields: dict[str, str] = {}
        for group in section.find_all("div", class_="info-group"):
            label = group.find("label", class_="info-label")
            value = group.find("div", class_="info-value")
            label_text = clean_text(label.get_text(" ", strip=True)).rstrip(":") if label is not None else ""
            if not label_text or value is None:
                continue
            fields[label_text] = clean_text(value.get_text(" ", strip=True))
        return fields

    @staticmethod
    def _section_link_groups(section: Tag | None, detail_url: str) -> list[dict[str, Any]]:
        if section is None:
            return []
        groups: list[dict[str, Any]] = []
        for group in section.find_all("div", class_="info-group"):
            label = group.find("label", class_="info-label")
            value = group.find("div", class_="info-value")
            label_text = clean_text(label.get_text(" ", strip=True)).rstrip(":") if label is not None else ""
            if value is None:
                continue
            links = []
            for anchor in value.find_all("a", href=True):
                links.append(
                    {
                        "label": clean_text(anchor.get_text(" ", strip=True)),
                        "url": absolute_url(detail_url, anchor.get("href")) or "",
                    }
                )
            groups.append({"label": label_text, "text": clean_text(value.get_text(" ", strip=True)), "links": links})
        return groups

    @staticmethod
    def _select_preferred_document_url(groups: list[dict[str, Any]]) -> str:
        for group in groups:
            for link in group.get("links") or []:
                if "html" in clean_text(link.get("label")).lower() or "generatehtmldocument" in str(link.get("url")).lower():
                    return str(link.get("url") or "")
        for group in groups:
            for link in group.get("links") or []:
                if clean_text(link.get("label")):
                    return str(link.get("url") or "")
        return ""

    @staticmethod
    def _select_original_document_url(groups: list[dict[str, Any]]) -> str:
        for group in groups:
            label = clean_text(group.get("label"))
            if "original" not in label.lower() and "not amended" not in label.lower():
                continue
            for link in group.get("links") or []:
                if "html" in clean_text(link.get("label")).lower():
                    return str(link.get("url") or "")
            for link in group.get("links") or []:
                return str(link.get("url") or "")
        return DelawareApiClient._select_preferred_document_url(groups)

    def _fetch_grid_data(self, path: str) -> list[dict[str, Any]]:
        response = self.client.post(path)
        if response.status_code >= 400:
            return []
        body = response.text.strip()
        if not body or body.startswith("<"):
            return []
        try:
            payload = response.json()
        except ValueError:
            return []
        if not isinstance(payload, dict):
            return []
        data = payload.get("Data") or []
        return data if isinstance(data, list) else []

    @staticmethod
    def _recent_reports_to_actions(reports: list[dict[str, Any]]) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        for report in reports:
            message = clean_text(report.get("ActionDescription"))
            if not message:
                continue
            actions.append(
                {
                    "statusDate": parse_delaware_date(report.get("OccuredAtDateTime")),
                    "location": "",
                    "statusMessage": message,
                }
            )
        return actions

    @staticmethod
    def _roll_call_summaries(roll_calls: list[dict[str, Any]]) -> list[str]:
        summaries: list[str] = []
        for roll_call in roll_calls[:3]:
            chamber = clean_text(roll_call.get("ChamberName"))
            result = clean_text(roll_call.get("RollCallResultTypeName"))
            taken_at = parse_delaware_date(roll_call.get("TakenAtDateTime"))
            yes_total = clean_text(str(roll_call.get("YesTotal") or ""))
            no_total = clean_text(str(roll_call.get("NoTotal") or ""))
            summary = " ".join(
                bit
                for bit in (
                    chamber,
                    result,
                    f"on {taken_at}" if taken_at else "",
                    f"(Yes {yes_total}, No {no_total})" if yes_total or no_total else "",
                )
                if bit
            )
            if summary:
                summaries.append(summary)
        return summaries

    @staticmethod
    def _chapter_from_link_groups(groups: list[dict[str, Any]]) -> str:
        for group in groups:
            label = clean_text(group.get("label"))
            match = DELAWARE_CHAPTER_PATTERN.search(label)
            if match is not None:
                return match.group(1)
            for link in group.get("links") or []:
                match = DELAWARE_CHAPTER_PATTERN.search(str(link.get("url") or ""))
                if match is not None:
                    return match.group(1)
        return ""

    @staticmethod
    def _chapter_from_text(value: str | None) -> str:
        match = DELAWARE_CHAPTER_PATTERN.search(clean_text(value))
        return match.group(1) if match is not None else ""

    @staticmethod
    def _chapter_from_reports(reports: list[dict[str, Any]]) -> str:
        for report in reversed(reports):
            match = DELAWARE_CHAPTER_PATTERN.search(clean_text(report.get("ActionDescription")))
            if match is not None:
                return match.group(1)
        return ""

    @staticmethod
    def _status_without_date(value: str | None) -> str:
        raw = clean_text(value)
        match = DELAWARE_STATUS_DATE_PATTERN.fullmatch(raw)
        if match is None:
            return raw
        return clean_text(match.group("status"))

    @staticmethod
    def _preferred_status_text(status_text: str | None, last_action: str | None) -> str:
        raw = clean_text(status_text)
        action = clean_text(last_action)
        if not raw:
            return action
        if re.fullmatch(r"[A-Z]{2,6}(?:\s+\d{1,2}/\d{1,2}/\d{2,4})?", raw):
            return action or raw
        return raw

    @staticmethod
    def _date_from_status(value: str | None) -> str:
        raw = clean_text(value)
        match = DELAWARE_STATUS_DATE_PATTERN.fullmatch(raw)
        if match is None:
            return ""
        return parse_delaware_date(match.group("date"))

    @staticmethod
    def _chamber_from_amendment_number(value: str | None) -> str:
        raw = clean_text(value).upper()
        if raw.startswith(("HA", "HS")):
            return "House"
        if raw.startswith(("SA", "SS")):
            return "Senate"
        return ""

    @staticmethod
    def _display_code_from_heading(value: str | None) -> str:
        raw = clean_text(value)
        if not raw:
            return ""

        amendment_match = re.match(r"^(House|Senate)\s+Amendment\s+(\d+)\s+to\s+(.+)$", raw, re.IGNORECASE)
        if amendment_match is not None:
            chamber = amendment_match.group(1).lower()
            base = DelawareApiClient._display_code_from_heading(amendment_match.group(3))
            prefix = "HA" if chamber == "house" else "SA"
            return f"{prefix} {int(amendment_match.group(2))} to {base}" if base else ""

        substitute_match = re.match(r"^(House|Senate)\s+Substitute\s+(\d+)\s+for\s+(.+)$", raw, re.IGNORECASE)
        if substitute_match is not None:
            chamber = substitute_match.group(1).lower()
            base = DelawareApiClient._display_code_from_heading(substitute_match.group(3))
            prefix = "HS" if chamber == "house" else "SS"
            return f"{prefix} {int(substitute_match.group(2))} for {base}" if base else ""

        for pattern, prefix in DELAWARE_HEADING_PATTERNS:
            match = pattern.fullmatch(raw)
            if match is not None:
                return f"{prefix} {int(match.group(1))}"
        return ""

    @staticmethod
    def _legislation_id_from_value(value: object) -> int:
        raw = clean_text(str(value or ""))
        if raw.isdigit():
            return int(raw)
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
        item = clean_text(query.get("legislationId", [""])[0])
        return int(item) if item.isdigit() else 0

    @staticmethod
    def _paragraph_html(*parts: str) -> str:
        paragraphs = []
        for part in parts:
            text = clean_text(part)
            if not text:
                continue
            paragraphs.append(f"<p>{html.escape(text)}</p>")
        return "".join(paragraphs)

    @staticmethod
    def _is_gateway_forbidden(body: str) -> bool:
        text = str(body or "")
        return "403 Forbidden" in text and "Application-Gateway" in text
