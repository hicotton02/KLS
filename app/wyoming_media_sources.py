from __future__ import annotations

import re
from urllib.parse import urlparse


def normalize_wyoming_media_url(source_url: object) -> str:
    value = str(source_url or "").strip()
    if value.startswith("//"):
        value = f"https:{value}"
    if value.casefold().startswith(("wyoleg.gov/", "www.wyoleg.gov/")):
        value = f"https://{value}"
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() == "s" and host in {"youtu.be", "youtube.com", "www.youtube.com"}:
        return parsed._replace(scheme="https").geturl()
    if host not in {"wyoleg.gov", "www.wyoleg.gov"} or parsed.scheme not in {"http", "https"}:
        return value
    path = parsed.path
    # The 2018 index includes its AudioMenu directory in recording links.
    path = re.sub(r"^/2018/Audio/AudioMenu/(house|senate)/", r"/2018/Audio/\1/", path)
    match = re.fullmatch(r"/2018/Audio/([hs]\d{6}(?:am|pm)\d+\.mp3)", path)
    if match:
        chamber = "house" if match[1].startswith("h") else "senate"
        path = f"/2018/Audio/{chamber}/{match[1]}"
    return parsed._replace(scheme="https", netloc="wyoleg.gov", path=path, fragment="").geturl()


def wyoming_media_url_variants(source_url: str) -> list[str]:
    canonical = normalize_wyoming_media_url(source_url)
    parsed = urlparse(canonical)
    if parsed.netloc != "wyoleg.gov":
        return [canonical]
    return [
        parsed._replace(scheme=scheme, netloc=host).geturl()
        for scheme in ("https", "http")
        for host in ("wyoleg.gov", "www.wyoleg.gov")
    ]
