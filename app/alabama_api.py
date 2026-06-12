from __future__ import annotations

import html
import re
from typing import Any

import httpx

from app.http_documents import fetch_document_fingerprint, fetch_document_text
from app.settings import Settings


ALABAMA_BILLS_QUERY = """
query bills(
  $sessionAbbreviation: String,
  $instrumentType: InstrumentType,
  $limit: Int,
  $offset: Int,
  $where: InstrumentWhere = {},
  $order: Order = ["sessionAbbreviation", "DESC"]
) {
  instruments(
    where: [
      { sessionAbbreviation: { eq: $sessionAbbreviation }, instrumentType: { eq: $instrumentType } }
      $where
    ]
    order: $order
    limit: $limit
    offset: $offset
  ) {
    count
    data {
      instrumentNbr
      shortTitle
      subject
      sponsor
      currentStatus
      lastAction
      actSummary
      viewEnacted
      companionInstrumentNbr
      effectiveDateCertain
      effectiveDateOther
    }
  }
}
"""

ALABAMA_BILL_MODAL_QUERY = """
query billModal($sessionAbbreviation: String, $instrumentNbr: String, $instrumentType: InstrumentType) {
  instrument: instrument(
    where: {
      sessionAbbreviation: { eq: $sessionAbbreviation }
      instrumentNbr: { eq: $instrumentNbr }
      instrumentType: { eq: $instrumentType }
    }
  ) {
    instrumentNbr
    shortTitle
    subject
    sponsor
    currentStatus
    introducedFileUrl
    engrossedFileUrl
    enrolledFileUrl
    reenrolledFileUrl
    viewEnacted
    actNbr
    actSummary
    effectiveDateCertain
    effectiveDateOther
  }
  fiscalNotes(where: { sessionAbbreviation: { eq: $sessionAbbreviation }, instrumentNbr: { eq: $instrumentNbr } }) {
    data {
      description
      fileUrl
      sortOrder
    }
  }
}
"""

ALABAMA_BILL_HISTORY_QUERY = """
query billHistory($sessionAbbreviation: String, $instrumentNbr: String) {
  histories: instrumentHistories(
    where: {
      sessionAbbreviation: { eq: $sessionAbbreviation }
      instrumentNbr: { eq: $instrumentNbr }
    }
  ) {
    data {
      calendarDate
      body
      matter
      amdSub
      amdSubFileUrl
      committee
      voteType
      voteTitle
      rollCallNbr
      yeas
      nays
      statusDescription
    }
  }
}
"""

ALABAMA_SESSION_QUERY = """
query sessionByAbbreviation($abbreviation: String) {
  session(where: { abbreviation: { eq: $abbreviation } }) {
    abbreviation
    name
  }
}
"""

ALABAMA_CURRENT_SESSION_QUERY = """
query config {
  currentSession: session(where: { current: { eq: true } }) {
    name
    abbreviation
  }
}
"""

ALABAMA_PAGE_SIZE = 200
ALABAMA_BILL_INSTRUMENT_TYPE = "B"
ALABAMA_MOTION_SPONSOR_PATTERN = re.compile(r"^([A-Za-z][A-Za-z .'-]+?)\s+motion to\b", re.IGNORECASE)


def parse_alabama_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[:10]


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _paragraph_html(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    return f"<p>{html.escape(cleaned)}</p>"


def _amendment_status_label(matter: str) -> str:
    raw = str(matter or "").strip()
    lowered = raw.lower()
    if "adopted" in lowered:
        return "Adopted"
    if "offered" in lowered:
        return "Offered"
    if "withdrawn" in lowered:
        return "Withdrawn"
    if any(marker in lowered for marker in ("failed", "lost", "rejected")):
        return "Failed"
    return raw[:80] or "Filed"


class AlabamaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.alabama_api_base,
            headers={
                "User-Agent": "keeping-law-simple/1.0",
                "Content-Type": "application/json",
            },
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._session_cache: dict[int, str] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session_abbreviation = self._session_abbreviation(year)
        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        expected_count = 0
        offset = 0

        while True:
            data = self._graphql(
                ALABAMA_BILLS_QUERY,
                {
                    "sessionAbbreviation": session_abbreviation,
                    "instrumentType": ALABAMA_BILL_INSTRUMENT_TYPE,
                    "limit": ALABAMA_PAGE_SIZE,
                    "offset": offset,
                    "where": {},
                    "order": ["instrumentNbr", "ASC"],
                },
            )
            instruments = data.get("instruments") or {}
            if not expected_count:
                expected_count = int(instruments.get("count") or 0)
            batch = instruments.get("data") or []
            if not batch:
                break

            for row in batch:
                bill_num = str(row.get("instrumentNbr") or "").strip().upper()
                if not bill_num or bill_num in seen_bill_nums:
                    continue
                seen_bill_nums.add(bill_num)
                items.append(
                    {
                        "billNum": bill_num,
                        "billType": bill_num[:2],
                        "catchTitle": _first_non_empty(row.get("shortTitle"), row.get("subject"), bill_num),
                        "billTitle": _first_non_empty(row.get("shortTitle"), row.get("subject"), bill_num),
                        "sponsor": str(row.get("sponsor") or "").strip(),
                        "billStatus": _first_non_empty(row.get("currentStatus"), row.get("lastAction")),
                        "lastAction": _first_non_empty(row.get("lastAction"), row.get("currentStatus")),
                        "lastActionDate": "",
                        "signedDate": "",
                        "effectiveDate": _first_non_empty(row.get("effectiveDateCertain"), row.get("effectiveDateOther")),
                        "chapter": "",
                        "enrolledNumber": "",
                    }
                )

            offset += len(batch)
            if offset >= expected_count:
                break

        if expected_count != len(items):
            raise RuntimeError(
                f"Alabama source count mismatch for {session_abbreviation}: expected {expected_count}, collected {len(items)}"
            )
        return items

    def fetch_bill_detail(self, year: int, bill_num: str) -> dict[str, Any]:
        normalized_bill_num = str(bill_num or "").strip().upper()
        if not normalized_bill_num:
            raise ValueError("Bill number is required")

        session_abbreviation = self._session_abbreviation(year)
        detail_data = self._graphql(
            ALABAMA_BILL_MODAL_QUERY,
            {
                "sessionAbbreviation": session_abbreviation,
                "instrumentNbr": normalized_bill_num,
                "instrumentType": ALABAMA_BILL_INSTRUMENT_TYPE,
            },
        )
        instrument = detail_data.get("instrument") or {}
        if not instrument:
            raise ValueError(f"Alabama bill detail was not found for {normalized_bill_num} in {session_abbreviation}")

        history_data = self._graphql(
            ALABAMA_BILL_HISTORY_QUERY,
            {
                "sessionAbbreviation": session_abbreviation,
                "instrumentNbr": normalized_bill_num,
            },
        )
        histories = history_data.get("histories", {}).get("data") or []
        actions = self._action_rows(histories)
        amendments = self._amendment_rows(histories)
        latest_action = actions[0] if actions else {"statusDate": "", "statusMessage": ""}
        current_version_label, current_version_url = self._current_version(instrument)
        current_version_fingerprint = fetch_document_fingerprint(self.client, current_version_url) if current_version_url else ""
        official_summary_text = _first_non_empty(instrument.get("actSummary"), instrument.get("shortTitle"))
        official_digest_text = _first_non_empty(instrument.get("subject"))
        signed_date = self._signed_date(actions, instrument)

        return {
            "bill": normalized_bill_num,
            "billType": normalized_bill_num[:2],
            "catchTitle": _first_non_empty(instrument.get("shortTitle"), instrument.get("subject"), normalized_bill_num),
            "sponsor": str(instrument.get("sponsor") or "").strip(),
            "billTitle": _first_non_empty(instrument.get("shortTitle"), instrument.get("subject"), normalized_bill_num),
            "billStatus": _first_non_empty(instrument.get("currentStatus"), latest_action.get("statusMessage")),
            "lastAction": _first_non_empty(latest_action.get("statusMessage"), instrument.get("currentStatus")),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": _first_non_empty(instrument.get("effectiveDateCertain"), instrument.get("effectiveDateOther")),
            "chapter": str(instrument.get("actNbr") or "").strip(),
            "enrolledNumber": current_version_label,
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": str(instrument.get("introducedFileUrl") or "").strip() or None,
            "digest": None,
            "summary": str(instrument.get("viewEnacted") or "").strip() or None,
            "currentVersionPath": current_version_url,
            "currentVersionFingerprint": current_version_fingerprint,
            "summaryHTML": _paragraph_html(official_summary_text),
            "digestHTML": _paragraph_html(official_digest_text),
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": str(instrument.get("viewEnacted") or current_version_url or instrument.get("introducedFileUrl") or "").strip(),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _session_abbreviation(self, year: int) -> str:
        if year in self._session_cache:
            return self._session_cache[year]

        candidate = f"{year}RS"
        session_data = self._graphql(ALABAMA_SESSION_QUERY, {"abbreviation": candidate})
        session = session_data.get("session") or {}
        abbreviation = str(session.get("abbreviation") or "").strip()
        if not abbreviation:
            current_data = self._graphql(ALABAMA_CURRENT_SESSION_QUERY, {})
            current_session = current_data.get("currentSession") or {}
            current_abbreviation = str(current_session.get("abbreviation") or "").strip()
            current_name = str(current_session.get("name") or "")
            if current_abbreviation and str(year) in current_name:
                abbreviation = current_abbreviation
        if not abbreviation:
            raise ValueError(f"Alabama session abbreviation was not found for {year}")
        self._session_cache[year] = abbreviation
        return abbreviation

    def _graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("", json={"query": query, "variables": variables})
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise RuntimeError(str(payload["errors"]))
        return payload.get("data") or {}

    @staticmethod
    def _history_sort_key(item: dict[str, Any]) -> tuple[str, int]:
        return (
            parse_alabama_date(str(item.get("calendarDate") or "")),
            int(item.get("rollCallNbr") or 0),
        )

    def _action_rows(self, histories: list[dict[str, Any]]) -> list[dict[str, str]]:
        ordered = sorted(histories, key=self._history_sort_key, reverse=True)
        rows: list[dict[str, str]] = []
        for item in ordered:
            message = self._history_message(item)
            if not message:
                continue
            rows.append(
                {
                    "statusDate": parse_alabama_date(str(item.get("calendarDate") or "")),
                    "location": _first_non_empty(item.get("body"), item.get("committee"), "Alabama Legislature"),
                    "statusMessage": message,
                }
            )
        return rows

    def _amendment_rows(self, histories: list[dict[str, Any]]) -> list[dict[str, str]]:
        ordered = sorted(histories, key=self._history_sort_key, reverse=True)
        amendments: dict[str, dict[str, str]] = {}
        for item in ordered:
            amendment_number = str(item.get("amdSub") or "").strip()
            if not amendment_number:
                continue
            matter = str(item.get("matter") or item.get("statusDescription") or "").strip()
            entry = amendments.get(amendment_number)
            if entry is None:
                entry = {
                    "amendmentNumber": amendment_number,
                    "house": str(item.get("body") or "").strip(),
                    "order": _first_non_empty(item.get("committee"), item.get("voteTitle"), item.get("body")),
                    "sequence": parse_alabama_date(str(item.get("calendarDate") or "")),
                    "status": _amendment_status_label(matter),
                    "sponsor": self._amendment_sponsor(matter),
                    "documentUrl": str(item.get("amdSubFileUrl") or "").strip(),
                }
                amendments[amendment_number] = entry
                continue
            if not entry.get("documentUrl"):
                entry["documentUrl"] = str(item.get("amdSubFileUrl") or "").strip()
            if not entry.get("order"):
                entry["order"] = _first_non_empty(item.get("committee"), item.get("voteTitle"), item.get("body"))
            if not entry.get("sponsor"):
                entry["sponsor"] = self._amendment_sponsor(matter)
            if entry.get("status") == "Filed":
                entry["status"] = _amendment_status_label(matter)
        return sorted(
            amendments.values(),
            key=lambda item: (str(item.get("sequence") or ""), str(item.get("amendmentNumber") or "")),
            reverse=True,
        )

    @staticmethod
    def _history_message(item: dict[str, Any]) -> str:
        matter = str(item.get("matter") or item.get("statusDescription") or item.get("voteTitle") or "").strip()
        if not matter:
            return ""
        yeas = item.get("yeas")
        nays = item.get("nays")
        vote_bits: list[str] = []
        if yeas is not None:
            vote_bits.append(f"Yeas {yeas}")
        if nays is not None:
            vote_bits.append(f"Nays {nays}")
        if vote_bits and "yea" not in matter.lower() and "nay" not in matter.lower():
            matter = f"{matter} ({', '.join(vote_bits)})"
        return matter

    @staticmethod
    def _current_version(instrument: dict[str, Any]) -> tuple[str, str | None]:
        candidates = (
            ("Reenrolled", instrument.get("reenrolledFileUrl")),
            ("Enrolled", instrument.get("enrolledFileUrl")),
            ("Engrossed", instrument.get("engrossedFileUrl")),
            ("Introduced", instrument.get("introducedFileUrl")),
            ("Enacted", instrument.get("viewEnacted")),
        )
        for label, url in candidates:
            normalized = str(url or "").strip()
            if normalized:
                return label, normalized
        return "", None

    @staticmethod
    def _signed_date(actions: list[dict[str, str]], instrument: dict[str, Any]) -> str:
        if str(instrument.get("actNbr") or "").strip():
            for action in actions:
                message = str(action.get("statusMessage") or "").strip().lower()
                if any(marker in message for marker in ("enacted", "approved by governor", "signed by governor")):
                    return str(action.get("statusDate") or "")
        return ""

    @staticmethod
    def _amendment_sponsor(matter: str) -> str:
        match = ALABAMA_MOTION_SPONSOR_PATTERN.search(str(matter or "").strip())
        if not match:
            return ""
        return match.group(1).strip()
