from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings
from app.text_utils import clean_text, first_non_empty


GEORGIA_AUTH_OBSCURE_KEY = "jVEXFFwSu36BwwcP83xYgxLAhLYmKk"
GEORGIA_PAGE_SIZE = 100
GEORGIA_BILL_NUMBER_PATTERN = re.compile(r"^(HB|SB|HR|SR)\d+$", re.IGNORECASE)
GEORGIA_BILL_PREFIXES = {
    (1, 1): "HB",
    (1, 2): "HR",
    (2, 1): "SB",
    (2, 2): "SR",
}


def parse_georgia_date(value: str | None) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    if "T" in raw and len(raw) >= 10:
        return raw[:10]
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def normalize_georgia_bill_number(value: str | None) -> str:
    raw = clean_text(value).upper().replace(" ", "")
    if GEORGIA_BILL_NUMBER_PATTERN.fullmatch(raw):
        return raw
    return ""


def _sort_bill_key(bill_num: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", str(bill_num or "").strip().upper())
    if match is None:
        return (str(bill_num or "").strip().upper(), 0)
    return (match.group(1), int(match.group(2)))


class GeorgiaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.georgia_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._token: str | None = None
        self._sessions_by_year: dict[int, dict[str, Any]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session = self.session_for_year(year)
        session_id = int(session["id"])
        results: list[dict[str, Any]] = []
        page = 0
        result_count = 0

        while True:
            response = self._api_request(
                "POST",
                f"/api/Legislation/Search/{GEORGIA_PAGE_SIZE}/{page}",
                json={"sessionId": session_id},
            )
            if response.status_code == 204:
                break

            payload = response.json()
            items = payload.get("results") or []
            if not items:
                break
            result_count = max(result_count, int(payload.get("resultCount") or 0))

            for row in items:
                bill_num = self._bill_number(row)
                if not bill_num:
                    continue
                legislation_id = int(row["legislationId"])
                title = clean_text(row.get("caption")) or bill_num
                status = clean_text(row.get("status"))
                status_date = parse_georgia_date(row.get("statusDate"))
                detail_path = absolute_url(self.settings.georgia_site_base, f"/api/Legislation/detail/{legislation_id}")
                if not detail_path:
                    continue
                results.append(
                    {
                        "billNum": bill_num,
                        "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
                        "catchTitle": title,
                        "billTitle": title,
                        "sponsor": self._primary_sponsor(row.get("sponsors") or []),
                        "billStatus": status,
                        "lastAction": status,
                        "lastActionDate": status_date,
                        "signedDate": status_date if "signed by governor" in status.lower() else "",
                        "effectiveDate": "",
                        "chapter": self._act_number(status),
                        "enrolledNumber": "",
                        "detailPath": detail_path,
                        "currentVersionPath": None,
                        "currentVersionFingerprint": "|".join(
                            part
                            for part in (
                                str(legislation_id),
                                status,
                                status_date,
                                clean_text(str(row.get("actVetoNumber") or "")),
                            )
                            if part
                        ),
                    }
                )

            if len(results) >= result_count:
                break
            page += 1

        deduped = {item["billNum"]: item for item in results}
        return sorted(deduped.values(), key=lambda item: _sort_bill_key(str(item["billNum"])))

    def fetch_bill_detail(self, detail_path: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._api_request("GET", detail_path)
        detail = response.json()

        bill_num = first_non_empty(
            self._bill_number(item or {}),
            self._bill_number(detail),
        )
        if not bill_num:
            raise ValueError(f"Georgia bill number could not be determined from {detail_path}")

        session_library = clean_text((detail.get("session") or {}).get("library"))
        versions = sorted(
            detail.get("versions") or [],
            key=lambda version: int(version.get("versionNumber") or 0),
            reverse=True,
        )
        current_version = versions[0] if versions else None
        introduced_version = versions[-1] if versions else current_version
        current_version_path = self._document_url(session_library, current_version)
        introduced_path = self._document_url(session_library, introduced_version) or current_version_path

        status_history = detail.get("statusHistory") or []
        bill_actions = [
            {
                "statusDate": parse_georgia_date(action.get("date")),
                "statusMessage": clean_text(action.get("name")),
                "location": "",
            }
            for action in status_history
            if clean_text(action.get("name"))
        ]
        last_action = first_non_empty(
            clean_text(status_history[0].get("name")) if status_history else "",
            clean_text(detail.get("status")),
        )
        last_action_date = (
            parse_georgia_date(status_history[0].get("date")) if status_history else parse_georgia_date((item or {}).get("lastActionDate"))
        )
        signed_date = ""
        chapter = ""
        for action in status_history:
            action_name = clean_text(action.get("name"))
            lowered = action_name.lower()
            action_date = parse_georgia_date(action.get("date"))
            if not chapter:
                chapter = self._act_number(action_name)
            if not signed_date and "signed by governor" in lowered:
                signed_date = action_date
        if not chapter:
            chapter = self._act_number(clean_text(detail.get("status")))

        sponsor_names = [clean_text(sponsor.get("name")) for sponsor in (detail.get("sponsors") or []) if clean_text(sponsor.get("name"))]
        sponsor = first_non_empty(
            self._primary_sponsor(detail.get("sponsors") or []),
            self._primary_sponsor((item or {}).get("sponsors") or []),
            clean_text((item or {}).get("sponsor")),
        )
        summary_text = first_non_empty(clean_text(detail.get("firstReader")), clean_text(detail.get("title")), bill_num)
        digest_bits = [
            clean_text(detail.get("footnotes")),
            clean_text(detail.get("status")),
        ]
        committees = [clean_text(committee.get("name")) for committee in (detail.get("committees") or []) if clean_text(committee.get("name"))]
        if committees:
            digest_bits.append(f"Committees: {'; '.join(committees)}")
        votes = detail.get("votes") or []
        if votes:
            digest_bits.append(f"Vote records posted: {len(votes)}")

        return {
            "bill": bill_num,
            "billType": re.match(r"[A-Z]+", bill_num).group(0) if re.match(r"[A-Z]+", bill_num) else bill_num,
            "catchTitle": first_non_empty(clean_text(detail.get("title")), clean_text((item or {}).get("catchTitle")), bill_num),
            "sponsor": sponsor,
            "billTitle": first_non_empty(clean_text(detail.get("title")), clean_text((item or {}).get("billTitle")), bill_num),
            "billStatus": first_non_empty(clean_text(detail.get("status")), clean_text((item or {}).get("billStatus")), last_action),
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": parse_georgia_date(
                next(
                    (action.get("date") for action in status_history if "effective date" in clean_text(action.get("name")).lower()),
                    "",
                )
            ),
            "chapter": chapter,
            "enrolledNumber": chapter or bill_num,
            "sponsorStringHouse": ", ".join(sponsor_names) if bill_num.startswith("H") and sponsor_names else None,
            "sponsorStringSenate": ", ".join(sponsor_names) if bill_num.startswith("S") and sponsor_names else None,
            "introduced": introduced_path,
            "digest": current_version_path,
            "summary": absolute_url(self.settings.georgia_site_base, f"/legislation/{detail.get('id')}"),
            "currentVersionPath": current_version_path,
            "currentVersionFingerprint": "|".join(
                part
                for part in (
                    current_version_path,
                    introduced_path,
                    clean_text(detail.get("status")),
                    last_action_date,
                    chapter,
                    str(len(versions)),
                )
                if part
            ),
            "summaryHTML": f"<p>{html.escape(summary_text)}</p>" if summary_text else "",
            "digestHTML": "".join(f"<p>{html.escape(bit)}</p>" for bit in digest_bits if bit),
            "currentBillHTML": "",
            "billActions": bill_actions,
            "amendments": [],
            "officialPage": absolute_url(self.settings.georgia_site_base, f"/legislation/{detail.get('id')}"),
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def session_for_year(self, year: int) -> dict[str, Any]:
        cached = self._sessions_by_year.get(year)
        if cached is not None:
            return cached

        response = self._api_request("GET", "/api/sessions")
        sessions = response.json()
        for session in sessions:
            description = clean_text(session.get("description"))
            library = clean_text(session.get("library"))
            if "special session" in description.lower():
                continue
            if str(year) in description or str(year) in library:
                self._sessions_by_year[year] = session
                return session
        raise ValueError(f"Georgia session could not be determined for {year}")

    def _api_request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers.update(self._auth_headers())
        response = self.client.request(method, url, headers=headers, **kwargs)
        if response.status_code == 401:
            self._token = None
            headers.update(self._auth_headers())
            response = self.client.request(method, url, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    def _auth_headers(self) -> dict[str, str]:
        if not self._token:
            self._token = self._fetch_token()
        return {"Authorization": f"Bearer {self._token}"}

    def _fetch_token(self) -> str:
        millis = str(int(time.time() * 1000))
        digest = hashlib.sha512(f"QFpCwKfd7f{GEORGIA_AUTH_OBSCURE_KEY}letvarconst{millis}".encode("utf-8")).hexdigest()
        response = self.client.get("/api/authentication/token", params={"key": digest, "ms": millis})
        response.raise_for_status()
        return response.text.strip().strip('"')

    @staticmethod
    def _primary_sponsor(sponsors: list[dict[str, Any]]) -> str:
        ordered = sorted(
            [sponsor for sponsor in sponsors if clean_text(sponsor.get("name"))],
            key=lambda sponsor: int(sponsor.get("sequence") or 9999),
        )
        return clean_text(ordered[0].get("name")) if ordered else ""

    @staticmethod
    def _bill_number(source: dict[str, Any]) -> str:
        chamber = int(source.get("chamberType") or source.get("chamber") or 0)
        document_type = int(source.get("documentType") or 0)
        prefix = GEORGIA_BILL_PREFIXES.get((chamber, document_type), "")
        number = clean_text(str(source.get("number") or ""))
        suffix = clean_text(source.get("suffix"))
        if not prefix or not number:
            return ""
        return normalize_georgia_bill_number(f"{prefix}{number}{suffix}")

    @staticmethod
    def _document_url(session_library: str, version: dict[str, Any] | None) -> str | None:
        if not session_library or not version:
            return None
        version_id = clean_text(str(version.get("id") or ""))
        if not version_id:
            return None
        library = clean_text(session_library)
        if library.startswith("http"):
            library = clean_text(urlparse(library).path.strip("/").split("/")[-1])
        return absolute_url("https://www.legis.ga.gov", f"/api/legislation/document/{library}/{version_id}")

    @staticmethod
    def _act_number(value: str | None) -> str:
        match = re.search(r"\bAct\s+(\d+)\b", clean_text(value), re.IGNORECASE)
        return clean_text(match.group(1) if match else "")
