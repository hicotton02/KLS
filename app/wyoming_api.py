from __future__ import annotations

from typing import Any

import httpx

from app.http_retry import get_with_retries
from app.settings import Settings
from app.text_utils import html_to_text, pdf_bytes_to_text


LIST_FIELDS = ",".join(
    [
        "BillNum",
        "ShortTitle",
        "Year",
        "ChapterNo",
        "Sponsor",
        "EnrolledNo",
        "LastActionDate",
        "LastAction",
        "SignedDate",
        "EffectiveDate",
        "BillType",
        "SpecialSessionValue",
        "BillStatus",
    ]
)


class WyomingApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.wyoming_api_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int, special_session_value: int | None = None) -> list[dict[str, Any]]:
        filter_parts = [f"Year eq {year}"]
        if special_session_value is not None:
            filter_parts.append(f"SpecialSessionValue eq {special_session_value}")
        params = {
            "$select": LIST_FIELDS,
            "$filter": " and ".join(filter_parts),
            "$orderby": "SpecialSessionValue,BillNum",
        }
        response = get_with_retries(self.client, "/BillInformation", params=params)
        response.raise_for_status()
        return response.json()

    def fetch_bill_detail(self, year: int, bill_num: str, special_session_value: int | None = None) -> dict[str, Any]:
        path = f"/BillInformation/{year}/{bill_num}"
        if special_session_value is not None:
            path += f"/{special_session_value}"
        response = get_with_retries(self.client, path)
        response.raise_for_status()
        return response.json()

    def fetch_legislators(self, year: int, chamber: str) -> list[dict[str, Any]]:
        response = get_with_retries(self.client, f"/legislator/{year}/{chamber}")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def public_document_url(self, path: str | None) -> str | None:
        if not path:
            return None
        return f"{self.settings.wyoming_site_base.rstrip('/')}/{path.lstrip('/')}"

    def public_bill_url(self, year: int, bill_num: str, special_session_value: int | None = None) -> str:
        url = f"{self.settings.wyoming_site_base.rstrip('/')}/Legislation/{year}/{bill_num}"
        if special_session_value is not None:
            url += f"?specialSessionValue={special_session_value}"
        return url

    def public_amendment_url(self, year: int, amendment_number: str, extension: str = "pdf") -> str:
        normalized_extension = extension.lstrip(".")
        return f"{self.settings.wyoming_site_base.rstrip('/')}/{year}/Amends/{amendment_number}.{normalized_extension}"

    def fetch_public_document_text(self, url: str | None) -> str:
        if not url:
            return ""
        response = get_with_retries(self.client, url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if url.lower().endswith(".pdf") or "pdf" in content_type:
            return pdf_bytes_to_text(response.content)
        return html_to_text(response.text)
