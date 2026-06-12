from __future__ import annotations

import gzip
import hashlib
import hmac
import ipaddress
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlparse

import httpx
import geoip2.database
from fastapi import Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

from app.db import cleanup_page_views, record_page_view
from app.settings import Settings


REQUESTS_TOTAL = Counter(
    "kls_http_requests_total",
    "Total HTTP requests served by Keeping Law Simple.",
    ["method", "route", "status_code"],
)
REQUEST_DURATION_SECONDS = Histogram(
    "kls_http_request_duration_seconds",
    "HTTP request latency for Keeping Law Simple.",
    ["method", "route"],
    buckets=(0.01, 0.03, 0.05, 0.1, 0.3, 0.5, 1, 2, 5, 10),
)
TRACKED_PAGE_VIEWS_TOTAL = Counter(
    "kls_tracked_page_views_total",
    "Server-side tracked HTML page views.",
    ["traffic_type"],
)
TRACKED_PAGE_VIEWS_BY_ROUTE_TOTAL = Counter(
    "kls_tracked_page_views_by_route_total",
    "Server-side tracked HTML page views by traffic type and route.",
    ["traffic_type", "route"],
)
TRACKED_PAGE_VIEWS_BY_COUNTRY_TOTAL = Counter(
    "kls_tracked_page_views_by_country_total",
    "Server-side tracked HTML page views by traffic type and country.",
    ["traffic_type", "country_code"],
)
TRACKED_PAGE_VIEWS_BY_LOCATION_TOTAL = Counter(
    "kls_tracked_page_views_by_location_total",
    "Server-side tracked HTML page views by traffic type and city-region location.",
    ["traffic_type", "country_code", "region_code", "region_name", "city_name", "latitude", "longitude"],
)

BOT_PATTERN = re.compile(
    r"(bot|crawl|spider|slurp|fetch|headless|preview|monitor|scan|python-requests|curl|wget|go-http-client)",
    re.IGNORECASE,
)
TRACKING_SKIP_PREFIXES = ("/static/", "/admin/analytics")
TRACKING_SKIP_PATHS = {
    "/favicon.ico",
    "/healthz",
    "/metrics",
    "/robots.txt",
    "/sitemap.xml",
}


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def route_label_for_path(path: str) -> str:
    normalized = path or "/"
    if normalized == "/":
        return "home"
    if normalized.startswith("/static/"):
        return "static"
    if normalized == "/search":
        return "search"
    if normalized == "/healthz":
        return "healthz"
    if normalized == "/metrics":
        return "metrics"
    if normalized == "/robots.txt":
        return "robots"
    if normalized == "/sitemap.xml":
        return "sitemap"
    if normalized == "/admin/analytics":
        return "admin_analytics"
    if normalized.startswith("/states/") and "/bills/" in normalized:
        return "state_bill_detail"
    if normalized.startswith("/states/"):
        return "state_listing"
    if normalized.startswith("/federal/bills/"):
        return "federal_bill_detail"
    if normalized == "/federal":
        return "federal_listing"
    if normalized.startswith("/bills/"):
        return "legacy_bill_redirect"
    return "other"


def should_track_page_view(request: Request, response: Response) -> bool:
    if request.method.upper() != "GET":
        return False
    path = request.url.path or "/"
    if path in TRACKING_SKIP_PATHS or any(path.startswith(prefix) for prefix in TRACKING_SKIP_PREFIXES):
        return False
    content_type = response.headers.get("content-type", "").lower()
    if "text/html" not in content_type:
        return False
    return 200 <= int(response.status_code) < 400


def record_request_metrics(method: str, route_label: str, status_code: int, duration_seconds: float) -> None:
    method_label = method.upper()
    REQUESTS_TOTAL.labels(method=method_label, route=route_label, status_code=str(status_code)).inc()
    REQUEST_DURATION_SECONDS.labels(method=method_label, route=route_label).observe(duration_seconds)


def detect_bot(user_agent: str) -> bool:
    return bool(BOT_PATTERN.search(user_agent or ""))


def referrer_domain(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").strip().lower()
    return host or None


def extract_client_ip(request: Request) -> str | None:
    header_candidates = [
        request.headers.get("cf-connecting-ip"),
        request.headers.get("x-real-ip"),
    ]
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        header_candidates.extend(part.strip() for part in forwarded.split(","))
    if request.client and request.client.host:
        header_candidates.append(request.client.host)

    fallback: str | None = None
    for candidate in header_candidates:
        if not candidate:
            continue
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        rendered = address.compressed
        if fallback is None:
            fallback = rendered
        if address.is_global:
            return rendered
    return fallback


def anonymize_visitor(client_ip: str | None, user_agent: str, secret: str) -> str | None:
    if not client_ip or not secret:
        return None
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return None
    if address.version == 4:
        network = ipaddress.ip_network(f"{address}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{address}/56", strict=False)
    normalized_agent = " ".join((user_agent or "").lower().split())[:120]
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{network.network_address}/{network.prefixlen}|{normalized_agent}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return digest[:24]


class GeoIPResolver:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.database_path = Path(settings.analytics_country_db_path)
        self.download_url = settings.analytics_country_db_url.strip()
        self._reader: geoip2.database.Reader | None = None

    def warm(self) -> None:
        if not self.settings.analytics_enabled:
            return
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self._should_refresh_database():
            self._download_database()
        if self.database_path.exists():
            self._reset_reader()

    def lookup_location(
        self,
        client_ip: str | None,
    ) -> tuple[str | None, str | None, str | None, str | None, str | None, float | None, float | None]:
        if not client_ip or self._reader is None:
            return None, None, None, None, None, None, None
        try:
            record = self._reader.city(client_ip)
            subdivision = record.subdivisions.most_specific
            region_code = (subdivision.iso_code or "").strip().upper() or None
            region_name = (subdivision.name or "").strip() or None
            country_code = (record.country.iso_code or "").strip().upper() or None
            country_name = (record.country.name or "").strip() or None
            city_name = (record.city.name or "").strip() or None
            latitude = _rounded_coordinate(getattr(record.location, "latitude", None))
            longitude = _rounded_coordinate(getattr(record.location, "longitude", None))
            return country_code, country_name, region_code, region_name, city_name, latitude, longitude
        except Exception:
            try:
                record = self._reader.country(client_ip)
            except Exception:
                return None, None, None, None, None, None, None
            country_code = (record.country.iso_code or "").strip().upper() or None
            country_name = (record.country.name or "").strip() or None
            return country_code, country_name, None, None, None, None, None

    def _should_refresh_database(self) -> bool:
        if not self.database_path.exists():
            return True
        age = datetime.now(timezone.utc) - datetime.fromtimestamp(self.database_path.stat().st_mtime, tz=timezone.utc)
        return age > timedelta(days=32)

    def _download_database(self) -> None:
        urls = [self.download_url] if self.download_url else _dbip_city_urls()
        for url in urls:
            gz_path: Path | None = None
            mmdb_path: Path | None = None
            try:
                with httpx.Client(timeout=60, follow_redirects=True) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    with NamedTemporaryFile(delete=False, suffix=".mmdb.gz", dir=str(self.database_path.parent)) as gz_file:
                        gz_file.write(response.content)
                        gz_path = Path(gz_file.name)
                    with NamedTemporaryFile(delete=False, suffix=".mmdb", dir=str(self.database_path.parent)) as mmdb_file:
                        mmdb_path = Path(mmdb_file.name)
                    with gzip.open(gz_path, "rb") as source, mmdb_path.open("wb") as target:
                        shutil.copyfileobj(source, target)
                    mmdb_path.replace(self.database_path)
                    gz_path.unlink(missing_ok=True)
                    return
            except Exception:
                if gz_path is not None:
                    gz_path.unlink(missing_ok=True)
                if mmdb_path is not None:
                    mmdb_path.unlink(missing_ok=True)
                continue

    def _reset_reader(self) -> None:
        if self._reader is not None:
            try:
                self._reader.close()
            except Exception:
                pass
        self._reader = geoip2.database.Reader(str(self.database_path))


def track_page_view(
    *,
    request: Request,
    response: Response,
    settings: Settings,
    resolver: GeoIPResolver,
    occurred_at: datetime | None = None,
) -> None:
    if not settings.analytics_enabled or not should_track_page_view(request, response):
        return
    timestamp = (occurred_at or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    path = request.url.path or "/"
    route_label = route_label_for_path(path)
    user_agent = request.headers.get("user-agent", "").strip()
    client_ip = extract_client_ip(request)
    country_code, country_name, region_code, region_name, city_name, latitude, longitude = resolver.lookup_location(client_ip)
    bot = detect_bot(user_agent)
    traffic_type = "bot" if bot else "human"
    country_code_label = (country_code or "ZZ").strip().upper() or "ZZ"
    region_code_label = (region_code or "").strip().upper() or None
    region_name_label = (region_name or "").strip() or None
    city_name_label = (city_name or "").strip() or None
    latitude_label = _coordinate_label(latitude)
    longitude_label = _coordinate_label(longitude)
    record_page_view(
        {
            "occurred_at": timestamp,
            "created_at": timestamp,
            "host": request.headers.get("host", "").split(":")[0].strip().lower(),
            "path": path,
            "route_label": route_label,
            "method": request.method.upper(),
            "status_code": int(response.status_code),
            "referrer_domain": referrer_domain(request.headers.get("referer")),
            "country_code": country_code,
            "country_name": country_name,
            "region_code": region_code_label,
            "region_name": region_name_label,
            "city_name": city_name_label,
            "latitude": latitude,
            "longitude": longitude,
            "visitor_hash": anonymize_visitor(client_ip, user_agent, settings.analytics_hmac_secret),
            "is_bot": bot,
            "user_agent": user_agent,
        }
    )
    TRACKED_PAGE_VIEWS_TOTAL.labels(traffic_type=traffic_type).inc()
    TRACKED_PAGE_VIEWS_BY_ROUTE_TOTAL.labels(traffic_type=traffic_type, route=route_label).inc()
    TRACKED_PAGE_VIEWS_BY_COUNTRY_TOTAL.labels(traffic_type=traffic_type, country_code=country_code_label).inc()
    if city_name_label and latitude_label and longitude_label:
        TRACKED_PAGE_VIEWS_BY_LOCATION_TOTAL.labels(
            traffic_type=traffic_type,
            country_code=country_code_label,
            region_code=region_code_label or "",
            region_name=region_name_label or "",
            city_name=city_name_label,
            latitude=latitude_label,
            longitude=longitude_label,
        ).inc()


def cleanup_old_page_views(settings: Settings) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.analytics_retention_days)).replace(microsecond=0).isoformat()
    return cleanup_page_views(cutoff)


def _dbip_city_urls() -> list[str]:
    now = datetime.now(timezone.utc).replace(day=1)
    previous_month = (now - timedelta(days=1)).replace(day=1)
    return [
        f"https://download.db-ip.com/free/dbip-city-lite-{now:%Y-%m}.mmdb.gz",
        f"https://download.db-ip.com/free/dbip-city-lite-{previous_month:%Y-%m}.mmdb.gz",
    ]


def _rounded_coordinate(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 2)


def _coordinate_label(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}"
