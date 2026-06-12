from __future__ import annotations

import io
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from pypdf import PdfReader


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    pending_blank = False
    for raw_line in value.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            if pending_blank and lines:
                lines.append("")
            lines.append(line)
            pending_blank = False
        else:
            pending_blank = True
    return "\n".join(lines).strip()


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return clean_text(soup.get_text("\n"))


def pdf_bytes_to_text(value: bytes) -> str:
    if not value:
        return ""
    try:
        reader = PdfReader(io.BytesIO(value))
    except Exception:  # noqa: BLE001
        return ""

    pages: list[str] = []
    for page in reader.pages:
        try:
            extracted = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            extracted = ""
        if extracted.strip():
            pages.append(extracted)
    return clean_text("\n".join(pages))


def truncate_for_prompt(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 14].rstrip() + "\n[truncated]"


def first_non_empty(*values: str | None) -> str:
    for value in values:
        cleaned = clean_text(value)
        if cleaned:
            return cleaned
    return ""


def sentence_list(value: str, max_items: int) -> list[str]:
    if not value:
        return []
    chunks = re.split(r"(?<=[.!?])\s+", clean_text(value))
    items: list[str] = []
    for chunk in chunks:
        normalized = chunk.strip(" -")
        if normalized:
            items.append(normalized)
        if len(items) >= max_items:
            break
    return items
