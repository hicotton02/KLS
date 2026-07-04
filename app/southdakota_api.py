from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.http_retry import get_with_retries
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


SOUTH_DAKOTA_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HC|SC|HCR|SCR|HJR|SJR|HR|SR)\d+$", re.IGNORECASE)


def normalize_south_dakota_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if SOUTH_DAKOTA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def parse_south_dakota_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "T" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    if "-" in raw and len(raw) >= 10 and raw[:4].isdigit():
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
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


class SouthDakotaApiClient:
    index_requires_detail_fetch = False

    def __init__(self, settings: Settings):
        self.settings = settings
        self.site_client = httpx.Client(
            base_url=self.settings.south_dakota_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._sessions_by_id: dict[int, dict[str, Any]] = {}
        self._sessions_by_year: dict[int, dict[str, Any]] = {}

    def close(self) -> None:
        self.site_client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session = self._session_for_year(year)
        session_id = int(session["SessionId"])
        response = self._get(f"/api/Bills/BillStatus/{session_id}")
        response.raise_for_status()

        items: list[dict[str, Any]] = []
        for row in response.json():
            bill_num = normalize_south_dakota_bill_number(
                f"{clean_text(row.get('BillType'))}{clean_text(str(row.get('BillNumberOnly') or ''))}"
            )
            if not bill_num:
                continue
            action_rows = self._action_rows(row.get("ActionLogs") or [])
            latest = action_rows[-1] if action_rows else {}
            bill_id = int(row.get("BillId") or 0)
            items.append(
                {
                    "billNum": bill_num,
                    "billType": clean_text(row.get("BillType")),
                    "catchTitle": clean_text(row.get("Title")) or bill_num,
                    "billTitle": clean_text(row.get("Title")) or bill_num,
                    "sponsor": "",
                    "billStatus": clean_text(latest.get("statusMessage")),
                    "lastAction": clean_text(latest.get("statusMessage")),
                    "lastActionDate": clean_text(latest.get("statusDate")),
                    "detailPath": absolute_url(self.settings.south_dakota_site_base, f"/Session/Bill/{bill_id}") or "",
                    "billId": bill_id,
                    "sessionId": session_id,
                    "currentVersionFingerprint": "|".join(
                        part
                        for part in (
                            str(bill_id),
                            clean_text(latest.get("statusMessage")),
                            clean_text(latest.get("statusDate")),
                            clean_text(row.get("Title")),
                        )
                        if part
                    ),
                }
            )

        return sorted(items, key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        bill_id = int((item or {}).get("billId") or self._bill_id_from_path(detail_path) or 0)
        if bill_id <= 0:
            raise ValueError(f"South Dakota bill id could not be determined from {detail_path}")

        detail = self._json_get(f"/api/Bills/{bill_id}")
        action_logs = self._action_rows(self._json_get(f"/api/Bills/ActionLog/{bill_id}"))
        versions = self._json_get(f"/api/Bills/Versions/{bill_id}")
        amendments = self._json_get(f"/api/Bills/Amendments/{bill_id}")
        fiscal_notes = self._json_get(f"/api/Bills/FiscalNotes/{bill_id}")
        prison_jail = self._json_get(f"/api/Bills/PrisonJail/{bill_id}")
        session = self._session_by_id(int(detail["SessionId"]))
        year = int(clean_text(session.get("YearString")) or clean_text(session.get("Year")) or "0")

        bill_num = normalize_south_dakota_bill_number(f"{detail.get('BillType')}{detail.get('BillNumber')}")
        if not bill_num:
            raise ValueError(f"South Dakota bill number could not be determined for bill {bill_id}")

        sponsor_entries = detail.get("BillSponsor") or []
        sponsor_names = [self._sponsor_name(entry) for entry in sponsor_entries if self._sponsor_name(entry)]
        prime_names = [
            self._sponsor_name(entry)
            for entry in sponsor_entries
            if clean_text(entry.get("SponsorType")).upper() == "P" and self._sponsor_name(entry)
        ]
        committee_sponsor = clean_text(detail.get("BillCommitteeSponsor"))
        sponsor = first_non_empty(
            prime_names[0] if prime_names else "",
            sponsor_names[0] if sponsor_names else "",
            committee_sponsor,
        )

        house_sponsors = [
            self._sponsor_name(entry)
            for entry in sponsor_entries
            if clean_text(entry.get("MemberType")).upper() == "H" and self._sponsor_name(entry)
        ]
        senate_sponsors = [
            self._sponsor_name(entry)
            for entry in sponsor_entries
            if clean_text(entry.get("MemberType")).upper() == "S" and self._sponsor_name(entry)
        ]

        version_rows = [
            {
                "label": clean_text(row.get("BillVersion")),
                "date": parse_south_dakota_date(row.get("DocumentDate")),
                "url": self._document_url(row.get("DocumentId"), year),
            }
            for row in versions
            if row.get("DocumentId")
        ]
        amendment_rows = [
            {
                "label": clean_text(row.get("Filename")),
                "date": "",
                "url": self._document_url(row.get("DocumentId"), year),
                "summary": clean_text(row.get("Result")) or clean_text(row.get("BillVersion")),
            }
            for row in amendments
            if row.get("DocumentId")
        ]
        fiscal_rows = [
            {
                "label": clean_text(f"{row.get('BillType')}{row.get('BillNumber')} {row.get('Version')}"),
                "date": "",
                "url": self._document_url(row.get("DocumentId"), year),
            }
            for row in fiscal_notes
            if row.get("DocumentId")
        ]
        prison_rows = [
            {
                "label": clean_text(f"{row.get('BillType')}{row.get('BillNumber')} {row.get('Version')}"),
                "date": "",
                "url": self._document_url(row.get("DocumentId"), year),
            }
            for row in prison_jail
            if row.get("DocumentId")
        ]

        latest_action = action_logs[-1] if action_logs else {}
        last_action = clean_text(latest_action.get("statusMessage"))
        last_action_date = clean_text(latest_action.get("statusDate"))
        signed_date = self._signed_date(action_logs)

        introduced_path = version_rows[0]["url"] if version_rows else None
        current_version_path = version_rows[-1]["url"] if version_rows else introduced_path
        digest_path = first_non_empty(
            fiscal_rows[0]["url"] if fiscal_rows else "",
            prison_rows[0]["url"] if prison_rows else "",
            None,
        )

        keywords = [clean_text(row.get("Keyword")) for row in detail.get("Keywords") or [] if clean_text(row.get("Keyword"))]
        summary_parts = [f"<p>{html.escape(clean_text(detail.get('Title')) or bill_num)}</p>"]
        if keywords:
            summary_parts.append("<p>Official keyword topics:</p><ul>")
            for keyword in keywords:
                summary_parts.append(f"<li>{html.escape(keyword)}</li>")
            summary_parts.append("</ul>")
        if committee_sponsor:
            summary_parts.append(f"<p>Official sponsor note: {html.escape(committee_sponsor)}</p>")

        digest_parts: list[str] = []
        if action_logs:
            digest_parts.append("<p>Recent official actions:</p><ul>")
            for row in action_logs[-8:]:
                digest_parts.append(
                    "<li>"
                    + html.escape(
                        " ".join(
                            part
                            for part in (
                                row.get("statusDate"),
                                row.get("location"),
                                row.get("statusMessage"),
                            )
                            if clean_text(str(part))
                        )
                    )
                    + "</li>"
                )
            digest_parts.append("</ul>")
        if amendment_rows:
            digest_parts.append("<p>Amendments:</p><ul>")
            for row in amendment_rows:
                extra = f" ({row['summary']})" if row["summary"] else ""
                digest_parts.append(f"<li>{html.escape(row['label'] + extra)}</li>")
            digest_parts.append("</ul>")
        if fiscal_rows or prison_rows:
            digest_parts.append("<p>Official fiscal and corrections notes:</p><ul>")
            for row in fiscal_rows + prison_rows:
                digest_parts.append(f"<li>{html.escape(row['label'])}</li>")
            digest_parts.append("</ul>")

        amendment_payload = [
            {
                "amendmentNumber": row["label"],
                "adoptedDate": row["date"],
                "documentUrl": row["url"],
                "summaryText": first_non_empty(row["summary"], row["label"]),
                "source": "South Dakota Legislature",
            }
            for row in amendment_rows
        ]

        current_version_fingerprint = "|".join(
            part
            for part in (
                current_version_path,
                introduced_path,
                digest_path,
                last_action,
                last_action_date,
                signed_date,
                str(len(action_logs)),
                str(len(version_rows)),
                str(len(amendment_rows)),
                str(len(fiscal_rows)),
            )
            if clean_text(str(part))
        )

        return {
            "bill": bill_num,
            "billType": clean_text(detail.get("BillType")),
            "catchTitle": clean_text(detail.get("Title")) or bill_num,
            "sponsor": sponsor,
            "billTitle": clean_text(detail.get("Title")) or bill_num,
            "billStatus": last_action,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": "",
            "enrolledNumber": "",
            "sponsorStringHouse": ", ".join(house_sponsors) if house_sponsors else None,
            "sponsorStringSenate": ", ".join(senate_sponsors) if senate_sponsors else None,
            "introduced": introduced_path,
            "digest": digest_path,
            "summary": None,
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": "".join(summary_parts),
            "digestHTML": "".join(digest_parts),
            "currentBillHTML": "",
            "billActions": action_logs,
            "amendments": amendment_payload,
            "officialPage": absolute_url(self.settings.south_dakota_site_base, f"/Session/Bill/{bill_id}") or detail_path,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.site_client, url)

    def _load_sessions(self) -> None:
        if self._sessions_by_id:
            return
        response = self._get("/api/Sessions")
        response.raise_for_status()
        for session in response.json():
            session_id = int(session["SessionId"])
            self._sessions_by_id[session_id] = session
            year = self._session_year(session)
            if year and not session.get("SpecialSession"):
                current = self._sessions_by_year.get(year)
                if current is None or int(current["SessionId"]) < session_id:
                    self._sessions_by_year[year] = session

    def _session_for_year(self, year: int) -> dict[str, Any]:
        self._load_sessions()
        session = self._sessions_by_year.get(year)
        if session is None:
            raise ValueError(f"South Dakota regular session not found for {year}")
        return session

    def _session_by_id(self, session_id: int) -> dict[str, Any]:
        self._load_sessions()
        session = self._sessions_by_id.get(session_id)
        if session is None:
            raise ValueError(f"South Dakota session id not found: {session_id}")
        return session

    def _json_get(self, path: str) -> Any:
        response = self._get(path)
        response.raise_for_status()
        return response.json()

    def _get(self, path: str, **kwargs: Any) -> httpx.Response:
        return get_with_retries(
            self.site_client,
            path,
            max_attempts=7,
            base_delay_seconds=2.0,
            max_delay_seconds=90.0,
            **kwargs,
        )

    @staticmethod
    def _bill_id_from_path(detail_path: str) -> int:
        match = re.search(r"/Session/Bill/(\d+)$", detail_path)
        return int(match.group(1)) if match is not None else 0

    def _document_url(self, document_id: Any, year: int) -> str:
        raw = clean_text(str(document_id or ""))
        if not raw:
            return ""
        return f"{self.settings.south_dakota_document_base}/api/Documents/{raw}.pdf?Year={year}"

    @staticmethod
    def _session_year(session: dict[str, Any]) -> int:
        for raw in (clean_text(session.get("YearString")), clean_text(session.get("Year"))):
            match = re.search(r"(20\d{2})", raw)
            if match is not None:
                return int(match.group(1))
        return 0

    @staticmethod
    def _sponsor_name(entry: dict[str, Any]) -> str:
        member = entry.get("Member") or {}
        unique = clean_text(member.get("UniqueName"))
        if unique:
            return unique
        first = clean_text(member.get("FirstName"))
        last = clean_text(member.get("LastName"))
        return clean_text(f"{first} {last}")

    def _action_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        for row in rows:
            assigned = row.get("AssignedCommittee") or {}
            assigned_name = clean_text(assigned.get("FullName")) or clean_text(assigned.get("Name"))
            status_text = clean_text(row.get("StatusText"))
            if assigned_name and assigned_name.lower() not in status_text.lower():
                status_text = clean_text(f"{status_text} {assigned_name}")
            actions.append(
                {
                    "statusDate": parse_south_dakota_date(row.get("ActionDate")),
                    "location": clean_text((row.get("ActionCommittee") or {}).get("FullName"))
                    or clean_text((row.get("ActionCommittee") or {}).get("Name")),
                    "statusMessage": status_text,
                }
            )
        actions.sort(key=lambda row: (row.get("statusDate") or "", row.get("statusMessage") or ""))
        return actions

    @staticmethod
    def _signed_date(actions: list[dict[str, str]]) -> str:
        for row in reversed(actions):
            if "signed by the governor" in clean_text(row.get("statusMessage")).lower():
                return clean_text(row.get("statusDate"))
        return ""
