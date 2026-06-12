from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

import httpx

from app.http_documents import absolute_url, fetch_document_text
from app.settings import Settings


def parse_mississippi_action_date(value: str | None, year_hint: int | None = None) -> str:
    raw = str(value or "").strip()
    if not raw or " " not in raw:
        return ""
    date_text = raw.split(" ", 1)[0]
    if "/" not in date_text:
        return ""
    parts = [item.zfill(2) for item in date_text.split("/")]
    if len(parts) == 2 and year_hint is not None:
        month, day = parts
        year = str(year_hint)
    elif len(parts) == 3:
        month, day, year = parts
    else:
        return ""
    return f"{year}-{month}-{day}"


class MississippiApiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            base_url=self.settings.mississippi_site_base,
            headers={"User-Agent": "keeping-law-simple/1.0"},
            timeout=self.settings.request_timeout_seconds,
            follow_redirects=True,
            verify=False,
        )

    def close(self) -> None:
        self.client.close()

    def fetch_year_bills(self, year: int) -> list[dict[str, Any]]:
        items: dict[str, dict[str, Any]] = {}
        for author_index in (f"/{year}/pdf/h_auth.xml", f"/{year}/pdf/s_auth.xml"):
            root = self._xml_root(author_index)
            for author_link in root.iter():
                if author_link.tag.endswith("_LINK") and "authors/" in (author_link.text or ""):
                    author_url = absolute_url(f"{self.settings.mississippi_site_base}/{year}/pdf/", author_link.text)
                    author_root = self._xml_root(author_url)
                    for group in author_root.findall(".//MSRGROUP"):
                        measure = self._find_text(group, "MEASURE").replace(" ", "")
                        if not measure or measure in items:
                            continue
                        action_link = absolute_url(author_url or f"{self.settings.mississippi_site_base}/{year}/pdf/", self._find_text(group, "ACTIONLINK"))
                        measure_link = absolute_url(author_url or f"{self.settings.mississippi_site_base}/{year}/pdf/", self._find_text(group, "MEASURELINK"))
                        items[measure] = {
                            "billNum": measure,
                            "billType": measure[:2],
                            "catchTitle": self._find_text(group, "SHORTTITLE"),
                            "sponsor": self._find_text(group, "AUTHOR"),
                            "billStatus": self._find_text(group, "ACTION"),
                            "lastAction": self._find_text(group, "ACTION"),
                            "lastActionDate": parse_mississippi_action_date(self._find_text(group, "ACTION"), year),
                            "detailPath": action_link,
                            "measurePath": measure_link,
                        }
        return sorted(items.values(), key=lambda item: str(item["billNum"]))

    def fetch_bill_detail(self, year: int, action_link: str, measure_link: str | None = None) -> dict[str, Any]:
        root = self._xml_root(action_link)
        actions = []
        for action in root.findall(".//ACTION"):
            desc = self._find_text(action, "ACT_DESC")
            if not desc:
                continue
            actions.append(
                {
                    "statusDate": parse_mississippi_action_date(desc, year),
                    "location": "",
                    "statusMessage": desc,
                }
            )
        latest_action = actions[-1] if actions else {"statusDate": "", "statusMessage": ""}
        short_title = self._find_text(root, "SHORTTITLE")
        long_title = self._find_text(root, "LONGTITLE")
        disposition = self._find_text(root, "DISPOSITION")
        effective_date = self._find_text(root, "EFFECTIVEDATE")
        bill_num = self._find_text(root, "SHORT_MSRID").replace(" ", "")
        sponsor_names = [
            element.text.strip()
            for element in root.findall(".//AUTHORS//*")
            if element.tag.endswith("_NAME") and (element.text or "").strip()
        ]
        signed_date = ""
        if "law" in disposition.lower() or "approved by governor" in str(latest_action.get("statusMessage") or "").lower():
            signed_date = str(latest_action.get("statusDate") or "")

        return {
            "bill": bill_num,
            "billType": bill_num[:2],
            "catchTitle": short_title or bill_num,
            "sponsor": ", ".join(dict.fromkeys(sponsor_names)),
            "billTitle": long_title or short_title or bill_num,
            "billStatus": disposition or str(latest_action.get("statusMessage") or ""),
            "lastAction": str(latest_action.get("statusMessage") or ""),
            "lastActionDate": str(latest_action.get("statusDate") or ""),
            "signedDate": signed_date,
            "effectiveDate": effective_date,
            "chapter": "Law" if disposition.lower() == "law" else "",
            "enrolledNumber": "",
            "sponsorStringHouse": None,
            "sponsorStringSenate": None,
            "introduced": measure_link,
            "digest": None,
            "summary": action_link,
            "currentVersionPath": measure_link,
            "currentVersionFingerprint": measure_link or "",
            "summaryHTML": f"<p>{short_title}</p>",
            "digestHTML": f"<p>{long_title}</p>",
            "currentBillHTML": "",
            "billActions": list(reversed(actions)),
            "amendments": [],
            "officialPage": action_link,
        }

    def fetch_public_document_text(self, url: str | None) -> str:
        return fetch_document_text(self.client, url)

    def _xml_root(self, path: str | None) -> ET.Element:
        if not path:
            raise ValueError("XML path is required")
        response = self.client.get(path)
        response.raise_for_status()
        return ET.fromstring(response.text)

    @staticmethod
    def _find_text(root: ET.Element, tag_name: str) -> str:
        element = root.find(f".//{tag_name}")
        if element is None or element.text is None:
            return ""
        return element.text.strip()
