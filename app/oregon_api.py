from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text


OREGON_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HCR|HJM|HJR|HR|SCR|SJM|SJR|SR)\d+$", re.IGNORECASE)


def normalize_oregon_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if OREGON_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_oregon_date(value: str | None, year: int | None = None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]

    dotnet_match = re.search(r"/Date\((\-?\d+)\)/", raw)
    if dotnet_match is not None:
        try:
            timestamp_ms = int(dotnet_match.group(1))
            return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, ValueError):
            return ""

    md_match = re.search(r"(\d{1,2})[-/](\d{1,2})", raw)
    if md_match is not None and year is not None:
        month = int(md_match.group(1))
        day = int(md_match.group(2))
        return f"{year:04d}-{month:02d}-{day:02d}"

    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
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


class OregonApiClient:
    index_requires_detail_fetch = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.oregon_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_key = self._session_key(year)
        response = self.client.get(f"/liz/{session_key}/Measures/list")
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        group_paths: list[str] = []
        for group in soup.select("ul.measure-item[data-load-action]"):
            path = clean_text(group.get("data-load-action"))
            if path and path not in group_paths:
                group_paths.append(path)

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for group_path in group_paths:
            group_response = self.client.get(group_path)
            group_response.raise_for_status()
            group_soup = BeautifulSoup(group_response.text, "html.parser")

            for row in group_soup.select("li.measure-desc.row"):
                anchor = row.find("a", href=True)
                if anchor is None:
                    continue
                bill_num = normalize_oregon_bill_number(anchor.get_text(" ", strip=True))
                if not bill_num or bill_num in seen:
                    continue
                seen.add(bill_num)

                summary_node = row.find_all("span")
                summary_text = clean_text(summary_node[-1].get_text(" ", strip=True)) if summary_node else ""
                detail_url = absolute_url(self.settings.oregon_site_base, anchor.get("href")) or ""
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                        "catchTitle": summary_text or bill_num,
                        "billTitle": summary_text or bill_num,
                        "sponsor": "",
                        "billStatus": "",
                        "lastAction": "",
                        "lastActionDate": "",
                        "detailPath": detail_url,
                        "currentVersionFingerprint": detail_url,
                    }
                )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(detail_path)
        response.raise_for_status()

        bill_num, session_key, year = self._measure_identifiers(str(response.url), item)
        sponsor_state = self._extract_assignment(response.text, "var Srv =")
        measure = self._extract_assignment(response.text, "Srv.Measure =")

        history_response = self.client.get(f"/liz/{session_key}/Measures/Overview/GetHistory/{bill_num}")
        history_response.raise_for_status()
        history_actions = self._history_actions(history_response.text, year)

        version_response = self.client.get(f"/liz/{session_key}/Measures/MeasureVersionList/{bill_num}?showAnnotationLinks=False")
        version_response.raise_for_status()
        version_rows = self._version_rows(version_response.text)

        sponsor = self._sponsor_string(sponsor_state)
        catch_title = clean_text(str(measure.get("CatchLine") or "")) or clean_text((item or {}).get("catchTitle")) or bill_num
        relating_to = clean_text(str(measure.get("RelatingTo") or ""))
        measure_summary = clean_text(str(measure.get("MeasureSummary") or ""))
        current_location = clean_text(str(measure.get("CurrentLocation") or ""))
        chapter_no = clean_text(str(measure.get("ChapterNumber") or ""))
        effective_date = parse_oregon_date(measure.get("EffectiveDate"), year)

        last_action_entry = history_actions[-1] if history_actions else {}
        last_action = clean_text(last_action_entry.get("statusMessage")) or current_location
        last_action_date = clean_text(last_action_entry.get("statusDate")) or self._signed_date(history_actions, chapter_no)
        signed_date = self._signed_date(history_actions, chapter_no)

        introduced_path = version_rows[0]["url"] if version_rows else self._measure_document_url(session_key, bill_num, "Introduced")
        current_version_path = version_rows[-1]["url"] if version_rows else introduced_path
        analysis_url = absolute_url(self.settings.oregon_site_base, f"/liz/{session_key}/Measures/Analysis/{bill_num}") or ""

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": catch_title,
            "sponsor": sponsor,
            "billTitle": catch_title,
            "billStatus": current_location,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": chapter_no,
            "enrolledNumber": version_rows[-1]["label"] if version_rows else "",
            "sponsorStringHouse": sponsor if bill_num.startswith("H") else None,
            "sponsorStringSenate": sponsor if bill_num.startswith("S") else None,
            "introduced": introduced_path or None,
            "digest": analysis_url or None,
            "summary": analysis_url or str(response.url),
            "currentVersionPath": current_version_path or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    current_location,
                    last_action,
                    last_action_date,
                    chapter_no,
                    effective_date,
                    str(len(history_actions)),
                )
                if part
            ),
            "summaryHTML": self._summary_html(measure_summary, relating_to, current_location),
            "digestHTML": self._history_html(history_actions),
            "currentBillHTML": "",
            "billActions": history_actions,
            "amendments": [],
            "officialPage": str(response.url),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    @staticmethod
    def _session_key(year: int) -> str:
        return f"{year}R1"

    @staticmethod
    def _extract_assignment(html_text: str, marker: str) -> dict[str, Any]:
        pattern = re.compile(re.escape(marker) + r"\s*(\{.*?\});", re.S)
        match = pattern.search(html_text)
        if match is None:
            return {}
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _measure_identifiers(detail_url: str, item: dict[str, Any] | None = None) -> tuple[str, str, int]:
        match = re.search(r"/liz/([^/]+)/Measures/Overview/([A-Z]+\d+)$", detail_url, re.IGNORECASE)
        if match is not None:
            session_key = clean_text(match.group(1))
            bill_num = normalize_oregon_bill_number(match.group(2))
            year_match = re.match(r"(\d{4})", session_key)
            if bill_num and year_match is not None:
                return bill_num, session_key, int(year_match.group(1))
        fallback = normalize_oregon_bill_number((item or {}).get("billNum"))
        if fallback:
            year = int((item or {}).get("year") or 0)
            if year:
                return fallback, f"{year}R1", year
        raise ValueError(f"Oregon bill identifiers could not be parsed from {detail_url}")

    @staticmethod
    def _measure_document_url(session_key: str, bill_num: str, version_label: str) -> str:
        return f"https://olis.oregonlegislature.gov/liz/{session_key}/Downloads/MeasureDocument/{bill_num}/{version_label}"

    def _history_actions(self, html_text: str, year: int) -> list[dict[str, str]]:
        soup = BeautifulSoup(html_text, "html.parser")
        rows: list[dict[str, str]] = []
        for row in soup.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if len(cells) < 2:
                continue
            raw_date = clean_text(cells[0].get_text(" ", strip=True))
            chamber_match = re.search(r"\(([HS])\)", raw_date)
            chamber = {"H": "House", "S": "Senate"}.get(chamber_match.group(1), "") if chamber_match else ""
            rows.append(
                {
                    "statusDate": parse_oregon_date(raw_date, year),
                    "location": chamber,
                    "statusMessage": clean_text(cells[1].get_text(" ", strip=True)),
                }
            )
        return rows

    def _version_rows(self, html_text: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html_text, "html.parser")
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            label = clean_text(anchor.get_text(" ", strip=True))
            href = absolute_url(self.settings.oregon_site_base, anchor.get("href")) or ""
            if not label or not href or href in seen:
                continue
            seen.add(href)
            rows.append({"label": label, "url": href})
        return rows

    @staticmethod
    def _sponsor_string(state: dict[str, Any]) -> str:
        names: list[str] = []
        for bucket in ("ChiefSponsors", "RegularSponsors"):
            for sponsor in state.get(bucket) or []:
                if not isinstance(sponsor, dict):
                    continue
                display_name = clean_text(str(sponsor.get("DisplayName") or sponsor.get("SponsorName") or ""))
                sponsor_type = clean_text(str(sponsor.get("SponsorType") or ""))
                if not display_name or "presession filed" in display_name.lower() or sponsor_type.lower() == "presession":
                    continue
                if display_name not in names:
                    names.append(display_name)
        return ", ".join(names)

    @staticmethod
    def _summary_html(summary_text: str, relating_to: str, current_location: str) -> str:
        paragraphs = [clean_text(part) for part in summary_text.splitlines()]
        parts = [paragraph for paragraph in paragraphs if paragraph]
        if relating_to and relating_to not in parts:
            parts.append(f"Relating to: {relating_to}")
        if current_location:
            parts.append(f"Current location: {current_location}")
        return "".join(f"<p>{html.escape(part)}</p>" for part in parts)

    @staticmethod
    def _history_html(actions: list[dict[str, str]]) -> str:
        if not actions:
            return ""
        recent = actions[-6:]
        return "".join(
            f"<p><strong>{html.escape(action['statusDate'])}</strong>: {html.escape(action['statusMessage'])}</p>"
            for action in recent
            if action["statusMessage"]
        )

    @staticmethod
    def _signed_date(actions: list[dict[str, str]], chapter_no: str) -> str:
        for action in reversed(actions):
            message = clean_text(action.get("statusMessage")).lower()
            if any(marker in message for marker in ("signed", "chapter", "filed with secretary of state", "became law")):
                return clean_text(action.get("statusDate"))
        if chapter_no and actions:
            return clean_text(actions[-1].get("statusDate"))
        return ""
