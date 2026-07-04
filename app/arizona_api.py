from __future__ import annotations

import re
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.http_retry import get_with_retries
from app.settings import Settings


ARIZONA_BILL_NUMBER_PATTERN = re.compile(r"^(?:HB|SB)\d+$", re.IGNORECASE)
ARIZONA_DATE_TYPE_LABELS = {
    "FIRST": "first read",
    "SECOND": "second read",
    "CONSENT": "consent calendar",
    "MAJCAUCUS": "majority caucus",
    "MINCAUCUS": "minority caucus",
    "COW": "committee of the whole",
    "ADCOW": "amended committee of the whole",
    "MOTIONADCOW": "amendment motion",
    "THIRD": "third read",
    "MISC": "miscellaneous action",
}


def parse_arizona_date(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "T" in raw:
        return raw.split("T", 1)[0]
    if "-" in raw and len(raw) >= 10:
        return raw[:10]
    parts = raw.split("/")
    if len(parts) != 3:
        return raw
    month, day, year = (item.zfill(2) for item in parts)
    return f"{year}-{month}-{day}"


class ArizonaApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.arizona_api_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
        )
        self._session_cache: dict[int, dict[str, Any]] = {}

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        session = self._session_for_year(year)
        response = self._get("/api/BillStatus/", params={"sessionId": session["session_id"]})
        response.raise_for_status()
        rows = response.json()

        items: list[dict[str, Any]] = []
        seen_bill_nums: set[str] = set()
        for row in rows:
            bill_num = str(row.get("BillNumber") or "").strip().upper()
            if not ARIZONA_BILL_NUMBER_PATTERN.fullmatch(bill_num) or bill_num in seen_bill_nums:
                continue
            seen_bill_nums.add(bill_num)
            description = str(row.get("Description") or "").strip()
            short_title = str(row.get("ShortTitle") or "").strip()
            chapter = str(row.get("Chapter") or "").strip()
            items.append(
                {
                    "billNum": bill_num,
                    "billType": bill_num[:2],
                    "catchTitle": short_title or self._trim_description_prefix(description, bill_num) or bill_num,
                    "billTitle": description or short_title or bill_num,
                    "sponsor": str(row.get("PrimarySponsorName") or "").strip(),
                    "billStatus": f"Chapter {chapter}" if chapter else "",
                    "lastAction": f"Chapter {chapter}" if chapter else "",
                    "lastActionDate": "",
                }
            )
        return sorted(items, key=lambda item: item["billNum"])

    def fetch_bill_detail(self, year: int, bill_num: str) -> dict[str, Any]:
        session = self._session_for_year(year)
        normalized_bill_num = str(bill_num or "").strip().upper()
        legislative_body = "H" if normalized_bill_num.startswith("HB") else "S"

        bill_response = self._get(
            "/api/Bill/",
            params={
                "billNumber": normalized_bill_num,
                "sessionId": session["session_id"],
                "legislativeBody": legislative_body,
            },
        )
        bill_response.raise_for_status()
        bill = bill_response.json()
        if not bill or not bill.get("BillId"):
            raise ValueError(f"Arizona bill detail was not found for {normalized_bill_num} in {year}")

        bill_id = int(bill["BillId"])
        overview_rows = self._json("/api/BillStatusOverview/", {"billNumber": normalized_bill_num, "sessionId": session["session_id"]})
        sponsor_rows = self._json("/api/BillSponsor/", {"id": bill_id})
        keyword_rows = self._json("/api/Keyword/", {"billStatusId": bill_id})
        section_rows = self._json("/api/SectionsAffected/", {"billStatusId": bill_id})
        document_groups = self._json("/api/DocType/", {"billStatusId": bill_id})

        versions = self._bill_version_documents(document_groups)
        misc_documents = self._misc_documents(document_groups)
        amendments = self._amendment_documents(document_groups)
        actions = self._overview_actions(overview_rows, bill)

        current_version = versions[-1] if versions else {}
        introduced_version = versions[0] if versions else {}
        latest_misc_document = misc_documents[-1] if misc_documents else {}

        description = str(bill.get("Description") or "").strip()
        short_title = str(bill.get("ShortTitle") or "").strip()
        catch_title = short_title or self._trim_description_prefix(description, normalized_bill_num) or normalized_bill_num
        sponsor_names = [
            str((row.get("Legislator") or {}).get("FullName") or "").strip()
            for row in sponsor_rows
            if str((row.get("Legislator") or {}).get("FullName") or "").strip()
        ]
        sponsor_text = ", ".join(dict.fromkeys(sponsor_names))

        chapter = str(bill.get("ChapterNumber") or bill.get("ChapterInfo") or "").strip()
        governor_action = str(bill.get("GovernorAction") or "").strip()
        governor_action_date = parse_arizona_date(str(bill.get("GovernorActionDate") or ""))
        signed_date = governor_action_date if governor_action.lower() == "signed" or chapter else ""

        if governor_action:
            last_action = f"Governor {governor_action.lower()}"
        elif actions:
            last_action = str(actions[0].get("statusMessage") or "")
        else:
            last_action = ""

        bill_status = f"Chapter {chapter}" if chapter else last_action
        last_action_date = governor_action_date or str(actions[0].get("statusDate") or "") if actions else governor_action_date

        summary_html = self._fetch_optional_html(str(latest_misc_document.get("html_url") or "")) or self._paragraph_html(description)
        digest_html = self._build_digest_html(keyword_rows, section_rows)
        current_version_url = str(current_version.get("html_url") or current_version.get("pdf_url") or "")
        introduced_url = str(introduced_version.get("html_url") or introduced_version.get("pdf_url") or "")
        current_version_name = str(current_version.get("document_name") or "")
        overview_url = absolute_url(self.settings.arizona_api_base, f"/BillStatus/BillOverview/{bill_id}") or ""

        return {
            "bill": normalized_bill_num,
            "billType": normalized_bill_num[:2],
            "catchTitle": catch_title,
            "sponsor": sponsor_text,
            "billTitle": description or catch_title,
            "billStatus": bill_status,
            "lastAction": last_action,
            "lastActionDate": last_action_date,
            "signedDate": signed_date,
            "effectiveDate": "",
            "chapter": chapter,
            "enrolledNumber": current_version_name,
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": introduced_url or None,
            "digest": str(latest_misc_document.get("html_url") or "") or None,
            "summary": overview_url,
            "currentVersionPath": current_version_url or None,
            "currentVersionFingerprint": "|".join(
                part
                for part in [
                    current_version_name,
                    str(current_version.get("pdf_url") or ""),
                    str(current_version.get("html_url") or ""),
                ]
                if part
            ),
            "summaryHTML": summary_html,
            "digestHTML": digest_html,
            "currentBillHTML": "",
            "billActions": actions,
            "amendments": amendments,
            "officialPage": overview_url,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _json(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        response = self._get(path, params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return []

    def _session_for_year(self, year: int) -> dict[str, Any]:
        cached = self._session_cache.get(year)
        if cached is not None:
            return cached
        response = self._get("/api/Session/")
        response.raise_for_status()
        rows = response.json()
        for row in rows:
            name = str(row.get("Name") or "").strip()
            if name.startswith(f"{year} -") and "Regular Session" in name:
                session = {
                    "session_id": int(row["SessionId"]),
                    "session_code": str(row.get("Code") or "").strip(),
                    "legislature": int(row.get("Legislature") or 0),
                }
                self._session_cache[year] = session
                return session
        raise ValueError(f"Arizona regular session was not found for {year}")

    @staticmethod
    def _trim_description_prefix(description: str, bill_num: str) -> str:
        raw = str(description or "").strip()
        prefix = f"{bill_num} - "
        if raw.upper().startswith(prefix.upper()):
            return raw[len(prefix):].strip()
        return raw

    def _bill_version_documents(self, groups: list[dict[str, Any]]) -> list[dict[str, str]]:
        documents: list[dict[str, str]] = []
        for group in groups:
            if str(group.get("DocumentGroupCode") or "") != "BillDocuments":
                continue
            for document in group.get("Documents") or []:
                documents.append(self._normalize_document(document))
        return documents

    def _misc_documents(self, groups: list[dict[str, Any]]) -> list[dict[str, str]]:
        documents: list[dict[str, str]] = []
        for group in groups:
            if str(group.get("DocumentGroupCode") or "") != "MiscBillDocuments":
                continue
            for document in group.get("Documents") or []:
                documents.append(self._normalize_document(document))
        return documents

    def _amendment_documents(self, groups: list[dict[str, Any]]) -> list[dict[str, str]]:
        amendments: list[dict[str, str]] = []
        seen_amendment_numbers: set[str] = set()
        for group in groups:
            group_code = str(group.get("DocumentGroupCode") or "")
            if group_code not in {"AdoptedAmendments", "ProposedAmendments"}:
                continue
            status = "Adopted" if group_code == "AdoptedAmendments" else "Proposed"
            for index, document in enumerate(group.get("Documents") or [], start=1):
                normalized = self._normalize_document(document)
                document_name = str(document.get("DocumentName") or "").strip()
                if document_name:
                    amendment_number = f"{document_name} ({status})"
                else:
                    amendment_number = f"{status} Amendment {index}"
                normalized_amendment_number = amendment_number.upper()
                if normalized_amendment_number in seen_amendment_numbers:
                    continue
                seen_amendment_numbers.add(normalized_amendment_number)
                amendments.append(
                    {
                        "amendmentNumber": amendment_number,
                        "house": "",
                        "order": str(index),
                        "sequence": "",
                        "status": status,
                        "sponsor": "",
                        "documentUrl": normalized.get("pdf_url") or normalized.get("html_url") or "",
                    }
                )
        return amendments

    def _normalize_document(self, document: dict[str, Any]) -> dict[str, str]:
        return {
            "document_name": str(document.get("DocumentName") or "").strip(),
            "pdf_url": absolute_url(self.settings.arizona_api_base, document.get("PdfPath")) or "",
            "html_url": absolute_url(self.settings.arizona_site_base, document.get("HtmlPath")) or "",
            "word_url": absolute_url(self.settings.arizona_site_base, document.get("WordPath")) or "",
        }

    def _fetch_optional_html(self, url: str) -> str:
        if not url:
            return ""
        response = self._get(url)
        response.raise_for_status()
        if "html" not in response.headers.get("content-type", "").lower():
            return ""
        return response.text

    def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        return get_with_retries(
            self.client,
            url,
            max_attempts=6,
            base_delay_seconds=2.0,
            max_delay_seconds=45.0,
            **kwargs,
        )

    @staticmethod
    def _paragraph_html(text: str) -> str:
        cleaned = str(text or "").strip()
        if not cleaned:
            return ""
        return f"<p>{cleaned}</p>"

    def _build_digest_html(self, keywords: list[dict[str, Any]], sections: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        keyword_text = ", ".join(
            str(item.get("Keyword") or "").strip()
            for item in keywords
            if str(item.get("Keyword") or "").strip()
        )
        if keyword_text:
            parts.append(f"<p>Keywords: {keyword_text}</p>")

        section_items = [
            " ".join(
                part
                for part in [
                    str(item.get("SectionNumber") or "").strip(),
                    str(item.get("Action") or "").strip(),
                ]
                if part
            ).strip()
            for item in sections
        ]
        section_items = [item for item in section_items if item]
        if section_items:
            joined = "; ".join(section_items[:25])
            parts.append(f"<p>Sections affected: {joined}</p>")
        return "".join(parts)

    def _overview_actions(self, rows: list[dict[str, Any]], bill: dict[str, Any]) -> list[dict[str, str]]:
        actions: list[dict[str, str]] = []
        for row in rows:
            status_date = parse_arizona_date(str(row.get("SortedDate") or ""))
            message = self._overview_message(row, bill)
            if not message:
                continue
            actions.append(
                {
                    "statusDate": status_date,
                    "location": self._body_name(str(row.get("Body") or "")),
                    "statusMessage": message,
                }
            )
        actions.reverse()
        return actions

    def _overview_message(self, row: dict[str, Any], bill: dict[str, Any]) -> str:
        date_type = str(row.get("DateType") or "").strip().upper()
        body = self._body_name(str(row.get("Body") or ""))
        action = str(row.get("Action") or "").strip()

        if date_type == "_STANDING":
            committee = str(row.get("col6") or row.get("col2") or "").strip()
            committee_action = str(row.get("col4") or "").strip()
            if committee and committee_action:
                return f"{body} {committee}: {committee_action}"
            if committee:
                return f"{body} {committee}"
            return f"{body} standing committee action"

        if date_type == "TRANSMIT":
            target = self._body_name(str(row.get("Body") or ""))
            if target:
                return f"Transmitted to {target}"
            return "Transmitted"

        if date_type == "GOVERNOR":
            governor_action = str(bill.get("GovernorAction") or "").strip()
            if governor_action:
                return f"Governor {governor_action.lower()}"
            return "Sent to governor"

        if date_type == "THIRD":
            if action:
                return f"{body} third read {action.lower()}"
            return f"{body} third read"

        if date_type == "MISC" and action:
            return f"{body} {action.lower()}"

        label = ARIZONA_DATE_TYPE_LABELS.get(date_type)
        if label:
            return f"{body} {label}".strip()
        if action:
            return f"{body} {action.lower()}".strip()
        return ""

    @staticmethod
    def _body_name(code: str) -> str:
        normalized = str(code or "").strip().upper()
        if normalized == "H":
            return "House"
        if normalized == "S":
            return "Senate"
        if normalized == "G":
            return "Governor"
        return normalized
