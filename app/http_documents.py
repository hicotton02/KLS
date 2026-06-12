from __future__ import annotations

from urllib.parse import urljoin

import httpx

from app.text_utils import html_to_text, pdf_bytes_to_text


def absolute_url(base_url: str, path: str | None) -> str | None:
    if not path:
        return None
    return urljoin(base_url, path)


def fetch_document_text(client: httpx.Client, url: str | None) -> str:
    if not url:
        return ""
    response = client.get(url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if url.lower().endswith(".pdf") or "pdf" in content_type:
        return pdf_bytes_to_text(response.content)
    return html_to_text(response.text)


def fetch_document_fingerprint(client: httpx.Client, url: str | None) -> str:
    if not url:
        return ""
    response: httpx.Response | None = None
    try:
        response = client.head(url)
        if response.status_code >= 400:
            response = None
    except httpx.HTTPError:
        response = None

    if response is None:
        response = client.get(url, headers={"Range": "bytes=0-0"})
    response.raise_for_status()

    parts = [
        str(response.url),
        response.headers.get("etag", "").strip(),
        response.headers.get("last-modified", "").strip(),
        response.headers.get("content-length", "").strip(),
    ]
    return "|".join(part for part in parts if part)
