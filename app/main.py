from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from urllib.parse import urlencode
from xml.etree.ElementTree import Element, SubElement, tostring

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analytics import (
    GeoIPResolver,
    cleanup_old_page_views,
    metrics_response,
    record_request_metrics,
    route_label_for_path,
    track_page_view,
)
from app.db import (
    get_bill,
    get_analytics_overview,
    get_jurisdiction_rollups,
    list_available_tags,
    list_bill_amendments,
    get_bill_relationships_for_bill,
    get_dashboard_counts,
    get_latest_bill_refresh,
    get_sync_status,
    init_db,
    list_bills,
    list_recent_bills,
    list_sync_statuses,
    search_bills,
    list_years,
    normalize_special_session,
)
from app.federal_api import congress_bill_number_part, congress_bill_public_url
from app.jurisdictions import (
    Jurisdiction,
    get_jurisdiction,
    get_jurisdiction_by_state_code,
    get_state_jurisdiction,
    jurisdiction_href,
    list_jurisdictions,
)
from app.relationship_service import relationship_peer
from app.settings import get_settings
from app.tagging import tag_label
from app.wyoming_api import WyomingApiClient


BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()
app = FastAPI(title=settings.app_title)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
geoip_resolver = GeoIPResolver(settings)


def _static_asset_version(*relative_paths: str) -> str:
    latest_mtime = 0.0
    for relative_path in relative_paths:
        candidate = BASE_DIR / "static" / relative_path
        try:
            latest_mtime = max(latest_mtime, candidate.stat().st_mtime)
        except FileNotFoundError:
            continue
    return str(int(latest_mtime)) if latest_mtime else "1"


STATIC_ASSET_VERSION = _static_asset_version("styles.css", "favicon.svg")
IS_PRODUCTION_ENV = settings.environment_name in {"", "prod", "production"}
ENVIRONMENT_BADGE_LABEL = (
    settings.environment_label or ("QA" if settings.environment_name == "qa" else settings.environment_name.upper())
).strip()
SHOW_ENVIRONMENT_BADGE = not IS_PRODUCTION_ENV and bool(ENVIRONMENT_BADGE_LABEL)

templates.env.globals["show_environment_badge"] = SHOW_ENVIRONMENT_BADGE
templates.env.globals["environment_badge_label"] = ENVIRONMENT_BADGE_LABEL
templates.env.globals["google_analytics_id"] = settings.google_analytics_id
templates.env.globals["environment_name"] = settings.environment_name
templates.env.globals["static_asset_version"] = STATIC_ASSET_VERSION

DEFAULT_ROBOTS = "index,follow,max-image-preview:large" if settings.allow_indexing else "noindex,nofollow"
NOINDEX_ROBOTS = "noindex,follow,max-image-preview:large" if settings.allow_indexing else "noindex,nofollow"
ADMIN_AUTH_REALM = 'Basic realm="Keeping Law Simple Admin"'
INTERNAL_SERVICE_HOSTS = {
    "keeping-law-simple-web",
    "keeping-law-simple-web.keeping-law-simple",
    "keeping-law-simple-web.keeping-law-simple.svc",
    "keeping-law-simple-web.keeping-law-simple.svc.cluster.local",
}
INTERNAL_ALLOWED_HOST_SUFFIXES = (".svc", ".svc.cluster.local")
SYNC_STALE_AFTER = timedelta(minutes=20)


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon() -> RedirectResponse:
    return RedirectResponse("/static/favicon.svg", status_code=307)


def format_date(value: str | None) -> str:
    if not value:
        return ""
    return value[:10]


def format_datetime(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return value[:19].replace("T", " ")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _empty_counts() -> dict[str, int]:
    return {"total": 0, "active": 0, "passed": 0, "failed": 0}


def _bill_word(count: int) -> str:
    return "bill" if count == 1 else "bills"


def _coverage_detail(source_total: object, stored_total: object, *, is_running: bool) -> str:
    if source_total is None or stored_total is None:
        return ""
    try:
        source_count = max(0, int(source_total))
        stored_count = max(0, int(stored_total))
    except (TypeError, ValueError):
        return ""
    if source_count <= 0:
        return ""
    if stored_count >= source_count:
        return f"Stored all {source_count} official bills."
    if is_running:
        return f"Stored {stored_count} of {source_count} official bills so far."
    return f"Stored {stored_count} of {source_count} official bills."


def _build_sync_status_view(
    jurisdiction: Jurisdiction,
    sync_status: dict[str, object] | None,
    *,
    latest_bill_refresh: str | None = None,
) -> dict[str, object] | None:
    if not sync_status:
        if not latest_bill_refresh:
            return None
        return {
            "is_running": False,
            "headline": "Background sync runs every couple hours.",
            "detail": f"Latest stored bill refresh: {format_datetime(latest_bill_refresh)}.",
        }

    current_year = sync_status.get("current_year")
    scope_label = ""
    if isinstance(current_year, int):
        scope_label = f"Congress {current_year}" if jurisdiction.kind == "federal" else str(current_year)

    seen = int(sync_status.get("seen") or 0)
    updated = int(sync_status.get("updated") or 0)
    current_bill_num = str(sync_status.get("current_bill_num") or "").strip()
    is_running = bool(sync_status.get("is_running"))
    last_progress_at = _parse_iso_datetime(str(sync_status.get("updated_at") or sync_status.get("started_at") or ""))
    is_stale = bool(is_running and last_progress_at and (datetime.now(timezone.utc) - last_progress_at) > SYNC_STALE_AFTER)
    coverage_detail = _coverage_detail(
        sync_status.get("source_total"),
        sync_status.get("stored_total"),
        is_running=is_running and not is_stale,
    )

    if is_stale:
        headline = "Background sync looks stalled."
        if scope_label:
            headline = f"Background sync for {scope_label} looks stalled."
        detail_parts = []
        if current_bill_num:
            detail_parts.append(f"Last progress was on {current_bill_num}.")
        stalled_since = format_datetime(str(sync_status.get("updated_at") or sync_status.get("started_at") or ""))
        if stalled_since:
            detail_parts.append(f"No new progress since {stalled_since}.")
        if seen:
            detail_parts.append(f"Checked {seen} {_bill_word(seen)} before it paused.")
    elif is_running:
        headline = "Background sync is running."
        if scope_label:
            headline = f"Background sync is running for {scope_label}."
        detail_parts = []
        if seen:
            detail_parts.append(f"Checked {seen} {_bill_word(seen)} so far.")
        if current_bill_num:
            detail_parts.append(f"Working on {current_bill_num}.")
    else:
        finished_at = format_datetime(str(sync_status.get("last_success_at") or sync_status.get("finished_at") or ""))
        headline = f"Background sync last finished {finished_at}." if finished_at else "Background sync has not finished a run yet."
        detail_parts = []
        if seen:
            detail_parts.append(f"Last run checked {seen} {_bill_word(seen)}")
            if updated:
                detail_parts[-1] += f" and updated {updated}."
            else:
                detail_parts[-1] += "."
        elif scope_label:
            detail_parts.append(f"Latest stored run covered {scope_label}.")
    if coverage_detail:
        detail_parts.append(coverage_detail)

    return {
        "is_running": is_running and not is_stale,
        "headline": headline,
        "detail": " ".join(detail_parts).strip(),
    }


def _relative_url(path: str, query: dict[str, object] | None = None) -> str:
    if query:
        params = [(key, str(value)) for key, value in query.items() if value is not None and value != ""]
        if params:
            return f"{path}?{urlencode(params)}"
    return path


def _absolute_url(path: str, query: dict[str, object] | None = None) -> str:
    base = settings.public_base_url.rstrip("/")
    relative_path = _relative_url(path, query)
    return base if relative_path == "/" else f"{base}{relative_path}"


def _parse_optional_int(value: str | None) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return int(raw)


def _normalized_host(value: str | None) -> str:
    return str(value or "").split(":", 1)[0].strip().lower()


def _is_private_or_loopback_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_private or address.is_loopback)


def _host_is_allowed(host: str) -> bool:
    if not host:
        return True
    if host in {
        settings.redirect_from_host,
        settings.canonical_host,
        "localhost",
        "127.0.0.1",
        "::1",
        "testserver",
    }:
        return True
    if host in INTERNAL_SERVICE_HOSTS:
        return True
    if host.endswith(INTERNAL_ALLOWED_HOST_SUFFIXES):
        return True
    if _is_private_or_loopback_host(host):
        return True
    return False


def _clip_text(value: str, limit: int = 160) -> str:
    normalized = " ".join(value.split()).strip()
    if len(normalized) <= limit:
        return normalized
    clipped = normalized[: limit - 1].rsplit(" ", 1)[0].rstrip(" .,;:-")
    return f"{clipped}…"


def _unauthorized_admin() -> HTTPException:
    return HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": ADMIN_AUTH_REALM})


def _require_admin(request: Request) -> None:
    if not settings.admin_username or not settings.admin_password:
        raise HTTPException(status_code=404, detail="Admin analytics not configured")
    authorization = request.headers.get("authorization", "").strip()
    if not authorization.lower().startswith("basic "):
        raise _unauthorized_admin()
    try:
        decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        raise _unauthorized_admin()
    username, separator, password = decoded.partition(":")
    if not separator:
        raise _unauthorized_admin()
    if not secrets.compare_digest(username, settings.admin_username) or not secrets.compare_digest(password, settings.admin_password):
        raise _unauthorized_admin()


def _apply_security_headers(response: Response) -> Response:
    connect_src = [
        "'self'",
        "https://fonts.googleapis.com",
        "https://fonts.gstatic.com",
    ]
    script_src = ["'self'", "'unsafe-inline'"]
    if settings.google_analytics_id:
        connect_src.extend(
            [
                "https://www.google-analytics.com",
                "https://region1.google-analytics.com",
                "https://www.googletagmanager.com",
            ]
        )
        script_src.append("https://www.googletagmanager.com")

    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        f"connect-src {' '.join(connect_src)}; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data: https:; "
        "object-src 'none'; "
        f"script-src {' '.join(script_src)}; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com"
    )
    if IS_PRODUCTION_ENV:
        csp = f"{csp}; upgrade-insecure-requests"
    response.headers["Content-Security-Policy"] = csp
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if IS_PRODUCTION_ENV:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    else:
        if "Strict-Transport-Security" in response.headers:
            del response.headers["Strict-Transport-Security"]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Origin-Agent-Cluster"] = "?1"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if not settings.allow_indexing:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _json_ld_block(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _breadcrumb_json_ld(items: list[tuple[str, str]]) -> str:
    return _json_ld_block(
        {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": position,
                    "name": name,
                    "item": url,
                }
                for position, (name, url) in enumerate(items, start=1)
            ],
        }
    )


def _latest_year(available_years: list[int]) -> int | None:
    return available_years[0] if available_years else None


def _fallback_years_for_state(state_code: str | None) -> list[int]:
    normalized = (state_code or "").strip().lower()
    if normalized == "ak":
        return list(settings.alaska_years)
    if normalized == "ks":
        return list(settings.kansas_years)
    if normalized == "ky":
        return list(settings.kentucky_years)
    if normalized == "wy":
        return list(settings.wyoming_years)
    if normalized == "al":
        return list(settings.alabama_years)
    if normalized == "az":
        return list(settings.arizona_years)
    if normalized == "ar":
        return list(settings.arkansas_years)
    if normalized == "ca":
        return list(settings.california_years)
    if normalized == "ga":
        return list(settings.georgia_years)
    if normalized == "fl":
        return list(settings.florida_years)
    if normalized == "ia":
        return list(settings.iowa_years)
    if normalized == "md":
        return list(settings.maryland_years)
    if normalized == "ct":
        return list(settings.connecticut_years)
    if normalized == "nm":
        return list(settings.new_mexico_years)
    if normalized == "ne":
        return list(settings.nebraska_years)
    if normalized == "sc":
        return list(settings.south_carolina_years)
    if normalized == "sd":
        return list(settings.south_dakota_years)
    if normalized == "vt":
        return list(settings.vermont_years)
    if normalized == "ut":
        return list(settings.utah_years)
    if normalized == "va":
        return list(settings.virginia_years)
    if normalized == "ri":
        return list(settings.rhode_island_years)
    if normalized == "mn":
        return list(settings.minnesota_years)
    if normalized == "mo":
        return list(settings.missouri_years)
    if normalized == "mt":
        return list(settings.montana_years)
    if normalized == "nv":
        return list(settings.nevada_years)
    if normalized == "oh":
        return list(settings.ohio_years)
    if normalized == "wv":
        return list(settings.west_virginia_years)
    if normalized == "co":
        return list(settings.colorado_years)
    if normalized == "tx":
        return list(settings.texas_years)
    if normalized == "ok":
        return list(settings.oklahoma_years)
    if normalized == "or":
        return list(settings.oregon_years)
    if normalized == "pa":
        return list(settings.pennsylvania_years)
    if normalized == "tn":
        return list(settings.tennessee_years)
    if normalized == "ms":
        return list(settings.mississippi_years)
    if normalized == "nc":
        return list(settings.north_carolina_years)
    if normalized == "wi":
        return list(settings.wisconsin_years)
    if normalized == "us":
        return list(settings.federal_congresses)
    return []


def _state_clean_query(selected_year: int | None, available_years: list[int]) -> dict[str, object]:
    latest_year = _latest_year(available_years)
    if selected_year is None or (latest_year is not None and selected_year == latest_year):
        return {}
    return {"year": selected_year}


def _state_canonical_url(jurisdiction: Jurisdiction, selected_year: int | None, available_years: list[int]) -> str:
    return _absolute_url(jurisdiction_href(jurisdiction), _state_clean_query(selected_year, available_years))


def _bill_path(jurisdiction: Jurisdiction, year: int, bill_num: str) -> str:
    if jurisdiction.kind == "federal":
        return f"/federal/bills/{year}/{bill_num}"
    return f"/states/{jurisdiction.slug}/bills/{year}/{bill_num}"


def _bill_query(bill: dict[str, object]) -> dict[str, object]:
    if bill.get("special_session_value") is not None:
        return {"special_session": bill["special_session_value"]}
    return {}


def _bill_href(jurisdiction: Jurisdiction, bill: dict[str, object]) -> str:
    return _relative_url(_bill_path(jurisdiction, int(bill["year"]), str(bill["bill_num"])), _bill_query(bill))


def _bill_canonical_url(jurisdiction: Jurisdiction, bill: dict[str, object]) -> str:
    return _absolute_url(_bill_path(jurisdiction, int(bill["year"]), str(bill["bill_num"])), _bill_query(bill))


def _build_home_seo() -> dict[str, object]:
    description = _clip_text(
        "Read official bill text, current status, and plain-English explanations for state and federal legislation. Every page stays tied to the official source."
    )
    canonical_url = _absolute_url("/")
    return {
        "title": f"{settings.app_title} | Bills In Plain English",
        "description": description,
        "canonical_url": canonical_url,
        "robots": DEFAULT_ROBOTS,
        "og_type": "website",
        "json_ld": [
            _json_ld_block(
                {
                    "@context": "https://schema.org",
                    "@type": "WebSite",
                    "name": settings.app_title,
                    "url": canonical_url,
                    "description": description,
                    "inLanguage": "en-US",
                }
            )
        ],
    }


def _build_state_seo(
    jurisdiction: Jurisdiction,
    selected_year: int | None,
    available_years: list[int],
    query: str,
    status: str,
    tag: str,
    bills: list[dict[str, object]],
) -> dict[str, object]:
    canonical_url = _state_canonical_url(jurisdiction, selected_year, available_years)
    latest_year = _latest_year(available_years)
    filtered = bool(query.strip()) or bool(tag.strip()) or status != "all" or jurisdiction.coverage_status != "live"
    year_label = f" ({selected_year})" if selected_year is not None else ""
    if bool(query.strip()):
        title = f"Search {jurisdiction.name} Bills{year_label} | {settings.app_title}"
        description = _clip_text(
            f"Search results for {jurisdiction.name} bills{year_label}. Use the main page for the clean bill list, official text, status, and plain-English summaries."
        )
    elif bool(tag.strip()):
        title = f"{jurisdiction.name} {tag_label(tag)} Bills{year_label} | {settings.app_title}"
        description = _clip_text(
            f"Filtered {jurisdiction.name} bill results{year_label} for {tag_label(tag)}. Use the main page for the full bill list, official text, status, and plain-English summaries."
        )
    elif status != "all":
        title = f"{jurisdiction.name} {status.title()} Bills{year_label} | {settings.app_title}"
        description = _clip_text(
            f"Filtered {jurisdiction.name} bill results{year_label}. Use the main page for the full bill list, official text, status, and plain-English summaries."
        )
    elif jurisdiction.coverage_status == "live":
        title = f"{jurisdiction.name} Bills In Plain English{year_label} | {settings.app_title}"
        description = _clip_text(
            f"Track {jurisdiction.name} bills{f' for {selected_year}' if selected_year is not None else ''} with official text, current status, plain-English summaries, and related-bill notes."
        )
    else:
        title = f"{jurisdiction.name} Legislation | {settings.app_title}"
        description = _clip_text(jurisdiction.description)

    json_ld = [
        _breadcrumb_json_ld(
            [
                ("Home", _absolute_url("/")),
                (jurisdiction.name, canonical_url),
            ]
        )
    ]
    if jurisdiction.coverage_status == "live":
        item_list = []
        for position, bill in enumerate(bills[:10], start=1):
            item_list.append(
                {
                    "@type": "ListItem",
                    "position": position,
                    "url": _bill_canonical_url(jurisdiction, bill),
                    "name": f"{bill['bill_num']} {bill.get('catch_title') or ''}".strip(),
                }
            )
        collection_page: dict[str, object] = {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": f"{jurisdiction.name} bills{f' for {selected_year}' if selected_year is not None else ''}",
            "description": description,
            "url": canonical_url,
            "inLanguage": "en-US",
            "isPartOf": {
                "@type": "WebSite",
                "name": settings.app_title,
                "url": _absolute_url("/"),
            },
        }
        if item_list:
            collection_page["mainEntity"] = {
                "@type": "ItemList",
                "numberOfItems": len(bills),
                "itemListElement": item_list,
            }
        json_ld.append(_json_ld_block(collection_page))

    if latest_year is not None and selected_year is None:
        title = f"{jurisdiction.name} Bills In Plain English ({latest_year}) | {settings.app_title}"

    return {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "robots": NOINDEX_ROBOTS if filtered else DEFAULT_ROBOTS,
        "og_type": "website",
        "json_ld": json_ld,
    }


def _build_search_seo(query: str, state: str, tag: str) -> dict[str, object]:
    description = _clip_text(
        "Search stored bill text, sponsors, tags, and plain-English summaries across the site."
    )
    if query.strip():
        title = f"Search Results For {query.strip()} | {settings.app_title}"
    elif tag.strip():
        title = f"Search {tag_label(tag)} Bills | {settings.app_title}"
    else:
        title = f"Search Bills | {settings.app_title}"
    return {
        "title": title,
        "description": description,
        "canonical_url": _absolute_url("/search"),
        "robots": NOINDEX_ROBOTS,
        "og_type": "website",
        "json_ld": [
            _breadcrumb_json_ld(
                [
                    ("Home", _absolute_url("/")),
                    ("Search", _absolute_url("/search")),
                ]
            )
        ],
    }


def _build_admin_analytics_seo() -> dict[str, object]:
    return {
        "title": f"Admin Analytics | {settings.app_title}",
        "description": "Protected traffic analytics for Keeping Law Simple administrators.",
        "canonical_url": _absolute_url("/admin/analytics"),
        "robots": NOINDEX_ROBOTS,
        "og_type": "website",
        "json_ld": [],
    }


def _build_bill_seo(
    jurisdiction: Jurisdiction,
    bill: dict[str, object],
    interpretation: dict[str, object],
    official_links: dict[str, str | None],
) -> dict[str, object]:
    canonical_url = _bill_canonical_url(jurisdiction, bill)
    summary = str(interpretation.get("one_sentence_summary") or "").strip()
    catch_title = str(bill.get("catch_title") or bill.get("bill_title") or bill.get("bill_num") or "").strip()
    title = _clip_text(f"{bill['bill_num']} {catch_title} | {jurisdiction.name} Bill | {settings.app_title}", limit=70)
    description = _clip_text(
        summary
        or f"Official text, status, and plain-English summary for {jurisdiction.name} bill {bill['bill_num']}."
    )
    date_modified = format_date(
        str(bill.get("source_synced_at") or bill.get("updated_at") or bill.get("last_action_date") or "")
    )
    legislation: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Legislation",
        "name": f"{bill['bill_num']} {catch_title}".strip(),
        "description": description,
        "url": canonical_url,
        "inLanguage": "en-US",
        "legislationIdentifier": bill["bill_num"],
        "legislationType": str(bill.get("bill_type") or "bill"),
        "jurisdiction": jurisdiction.name,
        "publisher": {
            "@type": "Organization",
            "name": jurisdiction.source_name,
            "url": jurisdiction.source_url,
        },
    }
    if date_modified:
        legislation["dateModified"] = date_modified
    if official_links.get("official_page"):
        legislation["sameAs"] = official_links["official_page"]
        legislation["workExample"] = [
            {
                "@type": "LegislationObject",
                "name": "Official bill page",
                "url": official_links["official_page"],
                "encodingFormat": "text/html",
                "legislationLegalValue": "https://schema.org/OfficialLegalValue",
            }
        ]

    return {
        "title": title,
        "description": description,
        "canonical_url": canonical_url,
        "robots": DEFAULT_ROBOTS,
        "og_type": "article",
        "json_ld": [
            _breadcrumb_json_ld(
                [
                    ("Home", _absolute_url("/")),
                    (jurisdiction.name, _state_canonical_url(jurisdiction, int(bill["year"]), list_years(jurisdiction.state_code or ""))),
                    (str(bill["bill_num"]), canonical_url),
                ]
            ),
            _json_ld_block(legislation),
        ],
    }


def _bill_sitemap_lastmod(bill: dict[str, object]) -> str | None:
    return format_date(
        str(bill.get("source_synced_at") or bill.get("updated_at") or bill.get("last_action_date") or "")
    ) or None


def _latest_lastmod(values: list[str | None]) -> str | None:
    normalized = [value for value in values if value]
    if not normalized:
        return None
    return max(normalized)


def _core_sitemap_entries() -> dict[str, str | None]:
    return {_absolute_url("/"): None}


def _jurisdiction_sitemap_entries(jurisdiction: Jurisdiction) -> dict[str, str | None]:
    if jurisdiction.coverage_status != "live" or not jurisdiction.state_code:
        return {}

    years = list_years(jurisdiction.state_code)
    if not years:
        years = _fallback_years_for_state(jurisdiction.state_code)

    state_path = jurisdiction_href(jurisdiction)
    state_url = _absolute_url(state_path)
    latest_year = _latest_year(years)
    entries: dict[str, str | None] = {state_url: None}

    for year in years:
        bills = list_bills(jurisdiction.state_code, year)
        year_url = state_url if year == latest_year else _absolute_url(state_path, {"year": year})
        if year != latest_year:
            entries[year_url] = None
        current_lastmod = entries.get(year_url)
        for bill in bills:
            bill_lastmod = _bill_sitemap_lastmod(bill)
            entries[_bill_canonical_url(jurisdiction, bill)] = bill_lastmod
            if bill_lastmod and (not current_lastmod or bill_lastmod > current_lastmod):
                entries[year_url] = bill_lastmod
                current_lastmod = bill_lastmod

    state_lastmod = format_date(get_latest_bill_refresh(jurisdiction.state_code)) or None
    if state_lastmod and (not entries.get(state_url) or state_lastmod > str(entries.get(state_url) or "")):
        entries[state_url] = state_lastmod
    return entries


def _jurisdiction_sitemap_lastmod(jurisdiction: Jurisdiction) -> str | None:
    if jurisdiction.coverage_status != "live" or not jurisdiction.state_code:
        return None
    return format_date(get_latest_bill_refresh(jurisdiction.state_code)) or None


def _sitemap_urlset_response(entries: dict[str, str | None]) -> Response:
    urlset = Element("urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for loc, lastmod in entries.items():
        url = SubElement(urlset, "url")
        SubElement(url, "loc").text = loc
        if lastmod:
            SubElement(url, "lastmod").text = lastmod
    return Response(content=tostring(urlset, encoding="utf-8", xml_declaration=True), media_type="application/xml")


def _sitemap_index_entries() -> list[tuple[str, str | None]]:
    index_entries: list[tuple[str, str | None]] = [(_absolute_url("/sitemaps/core.xml"), None)]
    for jurisdiction in list_jurisdictions():
        if jurisdiction.coverage_status != "live" or not jurisdiction.state_code:
            continue
        index_entries.append(
            (_absolute_url(f"/sitemaps/{jurisdiction.slug}.xml"), _jurisdiction_sitemap_lastmod(jurisdiction))
        )
    return index_entries


def _jurisdiction_cards() -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    jurisdictions = list_jurisdictions()
    state_codes = [jurisdiction.state_code for jurisdiction in jurisdictions if jurisdiction.state_code]
    sync_statuses = list_sync_statuses(state_codes)
    rollups = get_jurisdiction_rollups(state_codes)
    for jurisdiction in jurisdictions:
        rollup = rollups.get(jurisdiction.state_code or "", {})
        latest_year = rollup.get("latest_year")
        counts = rollup.get("counts") or _empty_counts()
        sync_status = _build_sync_status_view(
            jurisdiction,
            sync_statuses.get(jurisdiction.state_code or ""),
            latest_bill_refresh=rollup.get("latest_refresh"),
        )
        cards.append(
            {
                "jurisdiction": jurisdiction,
                "href": jurisdiction_href(jurisdiction),
                "latest_year": latest_year,
                "counts": counts,
                "sync_status": sync_status,
            }
        )
    return cards


def _jurisdiction_json(jurisdiction: Jurisdiction) -> dict[str, object]:
    return {
        "slug": jurisdiction.slug,
        "name": jurisdiction.name,
        "kind": jurisdiction.kind,
        "state_code": jurisdiction.state_code,
        "coverage_status": jurisdiction.coverage_status,
        "coverage_note": jurisdiction.coverage_note,
        "description": jurisdiction.description,
        "source_name": jurisdiction.source_name,
        "source_url": jurisdiction.source_url,
    }


def _bill_summary_json(jurisdiction: Jurisdiction, bill: dict[str, object]) -> dict[str, object]:
    interpretation = bill.get("interpretation_json")
    if not isinstance(interpretation, dict):
        interpretation = {}
    tags = [str(item or "").strip() for item in bill.get("bill_tags_json") or [] if str(item or "").strip()]
    return {
        "area_slug": jurisdiction.slug,
        "area_name": jurisdiction.name,
        "area_kind": jurisdiction.kind,
        "state_code": jurisdiction.state_code,
        "year": bill.get("year"),
        "special_session": bill.get("special_session_value"),
        "bill_num": bill.get("bill_num"),
        "catch_title": bill.get("catch_title"),
        "bill_title": bill.get("bill_title"),
        "sponsor": bill.get("sponsor"),
        "status_label": bill.get("status_label"),
        "status_explainer": bill.get("status_explainer"),
        "outcome": bill.get("outcome"),
        "last_action": bill.get("last_action"),
        "last_action_date": bill.get("last_action_date"),
        "updated_at": bill.get("updated_at"),
        "plain_language_title": interpretation.get("plain_language_title"),
        "summary": interpretation.get("one_sentence_summary"),
        "interpretation_model": interpretation.get("generator_model"),
        "fact_check_status": interpretation.get("fact_check_status"),
        "tags": [{"value": tag, "label": tag_label(tag)} for tag in tags],
        "legacy_href": _bill_href(jurisdiction, bill),
    }


def _public_json_response(payload: dict[str, object], *, max_age: int = 60) -> JSONResponse:
    return JSONResponse(
        content=payload,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": f"public, max-age={max_age}, stale-while-revalidate={max_age * 4}",
        },
    )


def _state_page_context(
    request: Request,
    jurisdiction: Jurisdiction,
    year: int | None,
    query: str,
    status: str,
    tag: str,
) -> dict[str, object]:
    fallback_years = _fallback_years_for_state(jurisdiction.state_code)
    available_years = list_years(jurisdiction.state_code or "") or fallback_years
    selected_year = year or (available_years[0] if available_years else None)
    bills = (
        list_bills(jurisdiction.state_code or "", selected_year, query=query, status=status, tag=tag)
        if selected_year is not None
        else []
    )
    bills = [{**bill, "href": _bill_href(jurisdiction, bill)} for bill in bills]
    counts = get_dashboard_counts(jurisdiction.state_code or "", selected_year) if selected_year is not None else _empty_counts()
    sync_status = _build_sync_status_view(
        jurisdiction,
        get_sync_status(jurisdiction.state_code or ""),
        latest_bill_refresh=get_latest_bill_refresh(jurisdiction.state_code or "") if jurisdiction.state_code else None,
    )
    return {
        "request": request,
        "app_title": settings.app_title,
        "jurisdiction": jurisdiction,
        "available_years": available_years,
        "available_tags": list_available_tags(jurisdiction.state_code or "", selected_year) if selected_year is not None else [],
        "selected_year": selected_year,
        "query": query,
        "status": status,
        "tag": tag,
        "counts": counts,
        "bills": bills,
        "sync_status": sync_status,
        "latest_year": _latest_year(available_years),
        "page_href": jurisdiction_href(jurisdiction),
    }


def _bill_back_href(jurisdiction: Jurisdiction, year: int) -> str:
    return f"{jurisdiction_href(jurisdiction)}?year={year}"


def _relationship_strength_rank(value: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value).strip().lower(), 0)


def _unique_text_items(values: list[object]) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def _collapse_related_relationships(
    jurisdiction: Jurisdiction,
    year: int,
    bill_num: str,
    special_session: int | None,
    relationships: list[dict[str, object]],
) -> list[dict[str, object]]:
    collapsed: dict[tuple[str, int], dict[str, object]] = {}

    for relationship in relationships:
        peer = relationship_peer(relationship, bill_num, special_session)
        peer_bill_num = str(peer.get("bill_num") or "").strip()
        if not peer_bill_num:
            continue
        peer_special_session = peer.get("special_session_value")
        peer_special_session_key = normalize_special_session(peer_special_session)
        key = (peer_bill_num, peer_special_session_key)
        peer_query = {"special_session": peer_special_session} if peer_special_session is not None else None

        item = collapsed.setdefault(
            key,
            {
                "peer": peer,
                "peer_href": _relative_url(_bill_path(jurisdiction, year, peer_bill_num), peer_query),
                "relationship_strength": str(relationship.get("relationship_strength") or "low"),
                "needs_human_review": bool(relationship.get("needs_human_review")),
                "pair_summaries": [],
                "combined_effects": [],
                "why_reviews": [],
                "evidence_items": [],
            },
        )

        if _relationship_strength_rank(str(relationship.get("relationship_strength") or "")) > _relationship_strength_rank(
            str(item["relationship_strength"])
        ):
            item["relationship_strength"] = str(relationship.get("relationship_strength") or "low")
        item["needs_human_review"] = bool(item["needs_human_review"]) or bool(relationship.get("needs_human_review"))

        pair_summaries = list(item["pair_summaries"])
        pair_summaries.append(relationship.get("pair_summary"))
        item["pair_summaries"] = _unique_text_items(pair_summaries)

        combined_effects = list(item["combined_effects"])
        combined_effects.append(relationship.get("combined_effect"))
        item["combined_effects"] = _unique_text_items(combined_effects)

        why_reviews = list(item["why_reviews"])
        why_reviews.append(relationship.get("why_review"))
        item["why_reviews"] = _unique_text_items(why_reviews)

        evidence_items = list(item["evidence_items"])
        evidence_items.extend(list(relationship.get("bill_a_evidence_json") or []))
        evidence_items.extend(list(relationship.get("bill_b_evidence_json") or []))
        item["evidence_items"] = _unique_text_items(evidence_items)

    return sorted(
        collapsed.values(),
        key=lambda item: (
            -_relationship_strength_rank(str(item["relationship_strength"])),
            str(item["peer"].get("bill_num") or ""),
            normalize_special_session(item["peer"].get("special_session_value")),
        ),
    )


def _official_links_for_bill(jurisdiction: Jurisdiction, bill: dict[str, object]) -> dict[str, str | None]:
    def _public_link(value: object) -> str | None:
        raw = str(value or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        return None

    def _dedupe(candidate: str | None, primary: str | None) -> str | None:
        if not candidate or candidate == primary:
            return None
        return candidate

    if jurisdiction.kind == "state":
        if jurisdiction.state_code == "wy":
            api = WyomingApiClient(settings)
            try:
                year = int(bill["year"])
                bill_num = str(bill["bill_num"])
                special_session = bill.get("special_session_value")
                return {
                    "official_page": api.public_bill_url(year, bill_num, special_session),
                    "introduced": api.public_document_url(bill.get("introduced_path")),
                    "digest": api.public_document_url(bill.get("digest_path")),
                    "summary": api.public_document_url(bill.get("summary_path")),
                    "current_version": api.public_document_url(bill.get("current_version_path")),
                }
            finally:
                api.close()

        official_page = _public_link(
            bill.get("summary_path") or bill.get("introduced_path") or bill.get("current_version_path")
        )
        introduced = _dedupe(_public_link(bill.get("introduced_path")), official_page)
        digest = _dedupe(_public_link(bill.get("digest_path")), official_page)
        summary = _dedupe(_public_link(bill.get("summary_path")), official_page)
        current_version = _dedupe(_public_link(bill.get("current_version_path")), official_page)
        return {
            "official_page": official_page,
            "introduced": introduced,
            "digest": digest,
            "summary": summary,
            "current_version": current_version,
        }

    number = congress_bill_number_part(str(bill.get("bill_num") or ""), str(bill.get("bill_type") or ""))
    return {
        "official_page": congress_bill_public_url(int(bill["year"]), str(bill.get("bill_type") or ""), number),
        "introduced": str(bill.get("introduced_path") or "") or None,
        "digest": str(bill.get("digest_path") or "") or None,
        "summary": str(bill.get("summary_path") or "") or None,
        "current_version": str(bill.get("current_version_path") or "") or None,
    }


def _render_bill_detail(
    request: Request,
    jurisdiction: Jurisdiction,
    year: int,
    bill_num: str,
    *,
    special_session: int | None = None,
) -> HTMLResponse:
    bill = get_bill(jurisdiction.state_code or "", year, bill_num, special_session_value=special_session)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    official_links = _official_links_for_bill(jurisdiction, bill)
    interpretation = bill.get("interpretation_json") or {}
    bill_tags = [{"value": tag, "label": tag_label(tag)} for tag in bill.get("bill_tags_json") or []]
    amendments = list_bill_amendments(
        jurisdiction.state_code or "",
        year,
        bill_num,
        special_session_value=special_session,
    )
    actions = bill.get("bill_actions_json") or []
    actions = sorted(actions, key=lambda item: item.get("statusDate", ""), reverse=True)
    related_relationships = []
    if jurisdiction.kind == "state":
        raw_relationships = get_bill_relationships_for_bill(
            jurisdiction.state_code or "",
            year,
            bill_num,
            special_session_value=special_session,
            limit=6,
        )
        related_relationships = _collapse_related_relationships(
            jurisdiction,
            year,
            bill_num,
            special_session,
            raw_relationships,
        )

    return templates.TemplateResponse(
        "bill_detail.html",
        {
            "request": request,
            "app_title": settings.app_title,
            "jurisdiction": jurisdiction,
            "bill": bill,
            "official_links": official_links,
            "interpretation": interpretation,
            "bill_tags": bill_tags,
            "amendments": amendments,
            "actions": actions,
            "related_relationships": related_relationships,
            "back_href": _bill_back_href(jurisdiction, year),
            "seo": _build_bill_seo(jurisdiction, bill, interpretation, official_links),
        },
    )


templates.env.filters["format_date"] = format_date
templates.env.filters["format_datetime"] = format_datetime


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    geoip_resolver.warm()
    cleanup_old_page_views(settings)


@app.middleware("http")
async def security_and_analytics_middleware(request: Request, call_next):  # type: ignore[override]
    started = perf_counter()
    path = request.url.path or "/"
    host = _normalized_host(request.headers.get("host"))
    if host and not _host_is_allowed(host):
        response = PlainTextResponse("Invalid host header", status_code=400)
    elif settings.redirect_to_www and host == settings.redirect_from_host:
        path = request.url.path or "/"
        query = request.url.query
        target_url = f"{settings.public_base_url.rstrip('/')}{path}"
        if query:
            target_url = f"{target_url}?{query}"
        response: Response = RedirectResponse(target_url, status_code=307)
    else:
        response = await call_next(request)

    duration_seconds = perf_counter() - started
    route_label = route_label_for_path(path)
    record_request_metrics(request.method, route_label, int(response.status_code), duration_seconds)
    track_page_view(request=request, response=response, settings=settings, resolver=geoip_resolver)
    response = _apply_security_headers(response)
    if path.startswith("/admin/"):
        response.headers["Cache-Control"] = "private, no-store"
    elif path == "/metrics":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@app.get("/readyz")
def readyz() -> JSONResponse:
    years = list_years("wy")
    total = 0
    if years:
        total = get_dashboard_counts("wy", years[0])["total"]
    return JSONResponse({"status": "ok", "years": years, "latest_year_total": total})


@app.get("/api/v1/overview")
def api_overview() -> JSONResponse:
    jurisdictions = []
    for card in _jurisdiction_cards():
        jurisdiction = card["jurisdiction"]
        if not isinstance(jurisdiction, Jurisdiction):
            continue
        jurisdictions.append(
            {
                **_jurisdiction_json(jurisdiction),
                "latest_year": card["latest_year"],
                "counts": card["counts"],
                "sync_status": card["sync_status"],
                "legacy_href": card["href"],
            }
        )

    recent_bills = []
    for bill in list_recent_bills(limit=8):
        jurisdiction = get_jurisdiction_by_state_code(str(bill.get("state") or ""))
        if jurisdiction is not None:
            recent_bills.append(_bill_summary_json(jurisdiction, bill))

    return _public_json_response(
        {
            "site_name": settings.app_title,
            "interpretation_model": settings.ollama_model,
            "jurisdictions": jurisdictions,
            "recent_bills": recent_bills,
        }
    )


@app.get("/api/v1/areas/{area_slug}")
def api_area(
    request: Request,
    area_slug: str,
    year: int | None = Query(default=None),
    q: str = Query(default=""),
    status: str = Query(default="all"),
    tag: str = Query(default=""),
    limit: int = Query(default=60, ge=1, le=100),
) -> JSONResponse:
    jurisdiction = get_jurisdiction(area_slug)
    if jurisdiction is None or jurisdiction.coverage_status != "live":
        raise HTTPException(status_code=404, detail="Coverage area not found")
    context = _state_page_context(request, jurisdiction, year, q, status, tag)
    bills = [
        _bill_summary_json(jurisdiction, bill)
        for bill in list(context["bills"])[:limit]
        if isinstance(bill, dict)
    ]
    return _public_json_response(
        {
            "jurisdiction": _jurisdiction_json(jurisdiction),
            "available_years": context["available_years"],
            "available_tags": [
                {"value": item, "label": tag_label(str(item))} for item in context["available_tags"]
            ],
            "selected_year": context["selected_year"],
            "query": q,
            "status": status,
            "tag": tag,
            "counts": context["counts"],
            "sync_status": context["sync_status"],
            "bills": bills,
        },
        max_age=30,
    )


@app.get("/api/v1/search")
def api_search(
    q: str = Query(default=""),
    area: str = Query(default="all"),
    year: int | None = Query(default=None),
    status: str = Query(default="all"),
    tag: str = Query(default=""),
    limit: int = Query(default=60, ge=1, le=100),
) -> JSONResponse:
    state_filter = None if area == "all" else area
    results = []
    if q.strip() or tag.strip() or year is not None or status != "all" or area != "all":
        for bill in search_bills(q, state=state_filter, year=year, status=status, tag=tag, limit=limit):
            jurisdiction = get_jurisdiction_by_state_code(str(bill.get("state") or ""))
            if jurisdiction is not None:
                results.append(_bill_summary_json(jurisdiction, bill))
    areas = [
        {"value": jurisdiction.state_code or "", "slug": jurisdiction.slug, "label": jurisdiction.name}
        for jurisdiction in list_jurisdictions()
        if jurisdiction.state_code
    ]
    return _public_json_response(
        {
            "query": q,
            "area": area,
            "year": year,
            "status": status,
            "tag": tag,
            "areas": areas,
            "available_tags": [
                {"value": item, "label": tag_label(item)} for item in list_available_tags(state_filter, year)
            ],
            "results": results,
        },
        max_age=30,
    )


@app.get("/api/v1/areas/{area_slug}/bills/{year}/{bill_num}")
def api_bill_detail(
    area_slug: str,
    year: int,
    bill_num: str,
    special_session: int | None = Query(default=None),
) -> JSONResponse:
    jurisdiction = get_jurisdiction(area_slug)
    if jurisdiction is None or jurisdiction.coverage_status != "live":
        raise HTTPException(status_code=404, detail="Coverage area not found")
    bill = get_bill(jurisdiction.state_code or "", year, bill_num, special_session_value=special_session)
    if bill is None:
        raise HTTPException(status_code=404, detail="Bill not found")

    interpretation = bill.get("interpretation_json")
    if not isinstance(interpretation, dict):
        interpretation = {}
    actions = bill.get("bill_actions_json") or []
    actions = sorted(actions, key=lambda item: item.get("statusDate", ""), reverse=True)
    amendments = list_bill_amendments(
        jurisdiction.state_code or "",
        year,
        bill_num,
        special_session_value=special_session,
    )
    relationships = []
    if jurisdiction.kind == "state":
        raw_relationships = get_bill_relationships_for_bill(
            jurisdiction.state_code or "",
            year,
            bill_num,
            special_session_value=special_session,
            limit=6,
        )
        for relationship in _collapse_related_relationships(
            jurisdiction,
            year,
            bill_num,
            special_session,
            raw_relationships,
        ):
            peer = relationship.get("peer")
            if not isinstance(peer, dict):
                continue
            relationships.append(
                {
                    "peer": _bill_summary_json(jurisdiction, peer),
                    "relationship_strength": relationship.get("relationship_strength"),
                    "needs_human_review": relationship.get("needs_human_review"),
                    "pair_summaries": relationship.get("pair_summaries"),
                    "combined_effects": relationship.get("combined_effects"),
                    "why_reviews": relationship.get("why_reviews"),
                    "evidence_items": relationship.get("evidence_items"),
                }
            )

    return _public_json_response(
        {
            "jurisdiction": _jurisdiction_json(jurisdiction),
            "bill": {
                **_bill_summary_json(jurisdiction, bill),
                "status_explainer": bill.get("status_explainer"),
                "signed_date": bill.get("signed_date"),
                "effective_date": bill.get("effective_date"),
                "chapter_no": bill.get("chapter_no"),
                "enrolled_no": bill.get("enrolled_no"),
                "official_summary_text": bill.get("official_summary_text"),
                "official_digest_text": bill.get("official_digest_text"),
            },
            "interpretation": interpretation,
            "official_links": _official_links_for_bill(jurisdiction, bill),
            "actions": actions,
            "amendments": amendments,
            "relationships": relationships,
            "interpretation_model": settings.ollama_model,
        },
        max_age=60,
    )


@app.get("/robots.txt")
def robots_txt() -> PlainTextResponse:
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /admin/",
            "Disallow: /healthz",
            "Disallow: /metrics",
            f"Sitemap: {_absolute_url('/sitemap.xml')}",
        ]
    )
    return PlainTextResponse(content)


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    host = _normalized_host(request.headers.get("host"))
    if host in {settings.canonical_host, settings.redirect_from_host}:
        raise HTTPException(status_code=404, detail="Not found")
    return metrics_response()


@app.get("/sitemap.xml")
def sitemap_xml() -> Response:
    sitemapindex = Element("sitemapindex", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9")
    for loc, lastmod in _sitemap_index_entries():
        sitemap = SubElement(sitemapindex, "sitemap")
        SubElement(sitemap, "loc").text = loc
        if lastmod:
            SubElement(sitemap, "lastmod").text = lastmod
    return Response(content=tostring(sitemapindex, encoding="utf-8", xml_declaration=True), media_type="application/xml")


@app.get("/sitemaps/core.xml")
def sitemap_core_xml() -> Response:
    return _sitemap_urlset_response(_core_sitemap_entries())


@app.get("/sitemaps/{scope}.xml")
def sitemap_scope_xml(scope: str) -> Response:
    jurisdiction = get_jurisdiction(scope)
    if jurisdiction is None or jurisdiction.coverage_status != "live" or not jurisdiction.state_code:
        raise HTTPException(status_code=404, detail="Sitemap not found")
    return _sitemap_urlset_response(_jurisdiction_sitemap_entries(jurisdiction))


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "app_title": settings.app_title,
            "jurisdiction_cards": _jurisdiction_cards(),
            "seo": _build_home_seo(),
        },
    )


@app.get("/states/{state_slug}", response_class=HTMLResponse)
def state_page(
    request: Request,
    state_slug: str,
    year: int | None = Query(default=None),
    q: str = Query(default=""),
    status: str = Query(default="all"),
    tag: str = Query(default=""),
) -> HTMLResponse:
    jurisdiction = get_state_jurisdiction(state_slug)
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="State page not found")
    context = _state_page_context(request, jurisdiction, year, q, status, tag)
    context["seo"] = _build_state_seo(
        jurisdiction=jurisdiction,
        selected_year=context["selected_year"],  # type: ignore[arg-type]
        available_years=context["available_years"],  # type: ignore[arg-type]
        query=q,
        status=status,
        tag=tag,
        bills=context["bills"],  # type: ignore[arg-type]
    )
    return templates.TemplateResponse("jurisdiction.html", context)


@app.get("/federal", response_class=HTMLResponse)
def federal_page(
    request: Request,
    year: int | None = Query(default=None),
    q: str = Query(default=""),
    status: str = Query(default="all"),
    tag: str = Query(default=""),
) -> HTMLResponse:
    jurisdiction = get_jurisdiction("federal")
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="Federal page not found")
    context = _state_page_context(request, jurisdiction, year, q, status, tag)
    context["seo"] = _build_state_seo(
        jurisdiction=jurisdiction,
        selected_year=context["selected_year"],  # type: ignore[arg-type]
        available_years=context["available_years"],  # type: ignore[arg-type]
        query=q,
        status=status,
        tag=tag,
        bills=context["bills"],  # type: ignore[arg-type]
    )
    return templates.TemplateResponse("jurisdiction.html", context)


@app.get("/search", response_class=HTMLResponse)
def search_page(
    request: Request,
    q: str = Query(default=""),
    area: str = Query(default="all"),
    year: str | None = Query(default=None),
    status: str = Query(default="all"),
    tag: str = Query(default=""),
) -> HTMLResponse:
    parsed_year = _parse_optional_int(year)
    state_filter = None if area == "all" else area
    search_areas = [
        {"value": "all", "label": "All coverage"},
        *[
            {"value": jurisdiction.state_code or "", "label": jurisdiction.name}
            for jurisdiction in list_jurisdictions()
            if jurisdiction.state_code
        ],
    ]
    if q.strip() or tag.strip() or parsed_year is not None or status != "all" or area != "all":
        results = search_bills(
            q,
            state=state_filter,
            year=parsed_year,
            status=status,
            tag=tag,
            limit=60,
        )
    else:
        results = []
    enriched_results = []
    for bill in results:
        jurisdiction = get_jurisdiction_by_state_code(str(bill.get("state") or ""))
        if jurisdiction is None:
            continue
        enriched_results.append(
            {
                **bill,
                "href": _bill_href(jurisdiction, bill),
                "jurisdiction_name": jurisdiction.name,
                "tag_labels": [tag_label(item) for item in bill.get("bill_tags_json") or []],
            }
        )

    return templates.TemplateResponse(
        "search.html",
        {
            "request": request,
            "app_title": settings.app_title,
            "query": q,
            "area": area,
            "year": parsed_year,
            "status": status,
            "tag": tag,
            "search_areas": search_areas,
            "available_tags": list_available_tags(state_filter, parsed_year),
            "results": enriched_results,
            "seo": _build_search_seo(q, area, tag),
        },
    )


@app.get("/admin/analytics", response_class=HTMLResponse)
def admin_analytics(request: Request) -> HTMLResponse:
    _require_admin(request)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    internal_hosts = tuple(
        dict.fromkeys(
            [
                settings.redirect_from_host,
                settings.canonical_host,
            ]
        )
    )
    analytics = get_analytics_overview(
        internal_hosts=internal_hosts,
        since_24h=(now - timedelta(days=1)).isoformat(),
        since_7d=(now - timedelta(days=7)).isoformat(),
        since_30d=(now - timedelta(days=30)).isoformat(),
    )
    return templates.TemplateResponse(
        "admin_analytics.html",
        {
            "request": request,
            "app_title": settings.app_title,
            "analytics": analytics,
            "seo": _build_admin_analytics_seo(),
        },
    )


@app.get("/bills/{year}/{bill_num}")
def legacy_bill_detail_redirect(year: int, bill_num: str, special_session: int | None = Query(default=None)) -> RedirectResponse:
    target = f"/states/wyoming/bills/{year}/{bill_num}"
    if special_session is not None:
        target = f"{target}?special_session={special_session}"
    return RedirectResponse(target, status_code=307)


@app.get("/states/{state_slug}/bills/{year}/{bill_num}", response_class=HTMLResponse)
def state_bill_detail(
    request: Request,
    state_slug: str,
    year: int,
    bill_num: str,
    special_session: int | None = Query(default=None),
) -> HTMLResponse:
    jurisdiction = get_state_jurisdiction(state_slug)
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="State page not found")
    return _render_bill_detail(request, jurisdiction, year, bill_num, special_session=special_session)


@app.get("/federal/bills/{year}/{bill_num}", response_class=HTMLResponse)
def federal_bill_detail(request: Request, year: int, bill_num: str) -> HTMLResponse:
    jurisdiction = get_jurisdiction("federal")
    if jurisdiction is None:
        raise HTTPException(status_code=404, detail="Federal page not found")
    return _render_bill_detail(request, jurisdiction, year, bill_num)
