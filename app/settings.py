from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_int_list(value: str | None, default: tuple[int, ...]) -> tuple[int, ...]:
    if not value:
        return default
    parsed: list[int] = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parsed.append(int(raw))
    return tuple(parsed) or default


def _current_congress(now: datetime | None = None) -> int:
    current = now or datetime.utcnow()
    return ((current.year - 1789) // 2) + 1


@dataclass(frozen=True)
class Settings:
    app_title: str
    database_path: str
    database_url: str
    public_base_url: str
    canonical_host: str
    redirect_from_host: str
    redirect_to_www: bool
    environment_name: str
    environment_label: str
    allow_indexing: bool
    google_analytics_id: str
    alaska_site_base: str
    alaska_years: tuple[int, ...]
    kansas_site_base: str
    kansas_years: tuple[int, ...]
    kentucky_site_base: str
    kentucky_years: tuple[int, ...]
    louisiana_site_base: str
    louisiana_years: tuple[int, ...]
    maine_site_base: str
    maine_years: tuple[int, ...]
    west_virginia_site_base: str
    west_virginia_years: tuple[int, ...]
    wyoming_api_base: str
    wyoming_site_base: str
    wyoming_years: tuple[int, ...]
    alabama_api_base: str
    alabama_site_base: str
    alabama_years: tuple[int, ...]
    arizona_api_base: str
    arizona_site_base: str
    arizona_years: tuple[int, ...]
    arkansas_site_base: str
    arkansas_years: tuple[int, ...]
    california_site_base: str
    california_years: tuple[int, ...]
    georgia_site_base: str
    georgia_years: tuple[int, ...]
    delaware_site_base: str
    delaware_years: tuple[int, ...]
    florida_site_base: str
    florida_years: tuple[int, ...]
    hawaii_site_base: str
    hawaii_years: tuple[int, ...]
    idaho_site_base: str
    idaho_years: tuple[int, ...]
    indiana_site_base: str
    indiana_api_key: str
    indiana_years: tuple[int, ...]
    illinois_site_base: str
    illinois_years: tuple[int, ...]
    north_dakota_site_base: str
    north_dakota_years: tuple[int, ...]
    iowa_site_base: str
    iowa_years: tuple[int, ...]
    maryland_site_base: str
    maryland_years: tuple[int, ...]
    massachusetts_site_base: str
    massachusetts_years: tuple[int, ...]
    michigan_site_base: str
    michigan_years: tuple[int, ...]
    washington_site_base: str
    washington_years: tuple[int, ...]
    connecticut_site_base: str
    connecticut_years: tuple[int, ...]
    new_mexico_site_base: str
    new_mexico_years: tuple[int, ...]
    nebraska_site_base: str
    nebraska_years: tuple[int, ...]
    south_carolina_site_base: str
    south_carolina_years: tuple[int, ...]
    south_dakota_site_base: str
    south_dakota_document_base: str
    south_dakota_years: tuple[int, ...]
    vermont_site_base: str
    vermont_years: tuple[int, ...]
    utah_site_base: str
    utah_years: tuple[int, ...]
    virginia_site_base: str
    virginia_years: tuple[int, ...]
    rhode_island_site_base: str
    rhode_island_status_base: str
    rhode_island_years: tuple[int, ...]
    minnesota_site_base: str
    minnesota_years: tuple[int, ...]
    missouri_house_base: str
    missouri_senate_base: str
    missouri_years: tuple[int, ...]
    montana_site_base: str
    montana_years: tuple[int, ...]
    nevada_site_base: str
    nevada_years: tuple[int, ...]
    new_hampshire_site_base: str
    new_hampshire_years: tuple[int, ...]
    new_jersey_site_base: str
    new_jersey_years: tuple[int, ...]
    district_of_columbia_site_base: str
    district_of_columbia_years: tuple[int, ...]
    new_york_site_base: str
    new_york_api_base: str
    new_york_api_key: str
    new_york_years: tuple[int, ...]
    ohio_site_base: str
    ohio_public_base: str
    ohio_years: tuple[int, ...]
    colorado_site_base: str
    colorado_years: tuple[int, ...]
    texas_site_base: str
    texas_ftp_host: str
    texas_years: tuple[int, ...]
    oklahoma_site_base: str
    oklahoma_reports_base: str
    oklahoma_years: tuple[int, ...]
    oregon_site_base: str
    oregon_years: tuple[int, ...]
    pennsylvania_site_base: str
    pennsylvania_years: tuple[int, ...]
    tennessee_site_base: str
    tennessee_bill_base: str
    tennessee_years: tuple[int, ...]
    mississippi_site_base: str
    mississippi_years: tuple[int, ...]
    north_carolina_site_base: str
    north_carolina_webservices_base: str
    north_carolina_years: tuple[int, ...]
    wisconsin_site_base: str
    wisconsin_years: tuple[int, ...]
    congress_api_base: str
    congress_api_key: str
    federal_congresses: tuple[int, ...]
    federal_sync_limit: int
    request_timeout_seconds: float
    ollama_base_url: str
    ollama_model: str
    ollama_timeout_seconds: float
    sync_parallelism: int
    analytics_enabled: bool
    analytics_retention_days: int
    analytics_country_db_path: str
    analytics_country_db_url: str
    admin_username: str
    admin_password: str
    analytics_hmac_secret: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    current_year = datetime.utcnow().year
    current_alaska_year = current_year if current_year % 2 == 0 else current_year + 1
    current_kansas_year = current_year
    current_north_dakota_year = current_year if current_year % 2 == 1 else current_year - 1
    current_odd_session_year = current_year if current_year % 2 == 1 else current_year - 1
    current_even_session_year = current_year if current_year % 2 == 0 else current_year - 1
    current_tennessee_year = current_year if current_year % 2 == 0 else current_year + 1
    current_congress = _current_congress()
    congress_api_key = (os.getenv("KLS_CONGRESS_API_KEY", "DEMO_KEY") or "DEMO_KEY").strip() or "DEMO_KEY"
    federal_sync_limit_default = "12" if congress_api_key.upper() == "DEMO_KEY" else "40"
    return Settings(
        app_title=os.getenv("KLS_APP_TITLE", "Keeping Law Simple"),
        database_path=os.getenv("KLS_DATABASE_PATH", "/data/keepinglawsimple.db"),
        database_url=(os.getenv("KLS_DATABASE_URL", "") or "").strip(),
        public_base_url=os.getenv("KLS_PUBLIC_BASE_URL", "https://www.keepinglawsimple.org"),
        canonical_host=os.getenv("KLS_CANONICAL_HOST", "www.keepinglawsimple.org"),
        redirect_from_host=os.getenv("KLS_REDIRECT_FROM_HOST", "keepinglawsimple.org"),
        redirect_to_www=_parse_bool(os.getenv("KLS_REDIRECT_TO_WWW"), default=True),
        environment_name=(os.getenv("KLS_ENVIRONMENT_NAME", "production") or "production").strip().lower(),
        environment_label=(os.getenv("KLS_ENVIRONMENT_LABEL", "") or "").strip(),
        allow_indexing=_parse_bool(os.getenv("KLS_ALLOW_INDEXING"), default=True),
        google_analytics_id=(os.getenv("KLS_GOOGLE_ANALYTICS_ID", "") or "").strip(),
        alaska_site_base=os.getenv("KLS_ALASKA_SITE_BASE", "https://www.akleg.gov"),
        alaska_years=_parse_int_list(os.getenv("KLS_ALASKA_YEARS"), default=(current_alaska_year,)),
        kansas_site_base=os.getenv("KLS_KANSAS_SITE_BASE", "https://www.kslegislature.gov"),
        kansas_years=_parse_int_list(os.getenv("KLS_KANSAS_YEARS"), default=(current_kansas_year,)),
        kentucky_site_base=os.getenv("KLS_KENTUCKY_SITE_BASE", "https://apps.legislature.ky.gov"),
        kentucky_years=_parse_int_list(os.getenv("KLS_KENTUCKY_YEARS"), default=(current_year,)),
        louisiana_site_base=os.getenv("KLS_LOUISIANA_SITE_BASE", "https://legis.la.gov/legis"),
        louisiana_years=_parse_int_list(os.getenv("KLS_LOUISIANA_YEARS"), default=(current_year,)),
        maine_site_base=os.getenv("KLS_MAINE_SITE_BASE", "https://legislature.maine.gov"),
        maine_years=_parse_int_list(os.getenv("KLS_MAINE_YEARS"), default=(current_odd_session_year,)),
        west_virginia_site_base=os.getenv("KLS_WEST_VIRGINIA_SITE_BASE", "https://www.wvlegislature.gov"),
        west_virginia_years=_parse_int_list(os.getenv("KLS_WEST_VIRGINIA_YEARS"), default=(current_year,)),
        wyoming_api_base=os.getenv("KLS_WYOMING_API_BASE", "https://web.wyoleg.gov/LsoService/api"),
        wyoming_site_base=os.getenv("KLS_WYOMING_SITE_BASE", "https://www.wyoleg.gov"),
        wyoming_years=_parse_int_list(os.getenv("KLS_WYOMING_YEARS"), default=(current_year,)),
        alabama_api_base=os.getenv("KLS_ALABAMA_API_BASE", "https://alison.legislature.state.al.us/graphql"),
        alabama_site_base=os.getenv("KLS_ALABAMA_SITE_BASE", "https://alison.legislature.state.al.us"),
        alabama_years=_parse_int_list(os.getenv("KLS_ALABAMA_YEARS"), default=(current_year,)),
        arizona_api_base=os.getenv("KLS_ARIZONA_API_BASE", "https://apps.azleg.gov"),
        arizona_site_base=os.getenv("KLS_ARIZONA_SITE_BASE", "https://www.azleg.gov"),
        arizona_years=_parse_int_list(os.getenv("KLS_ARIZONA_YEARS"), default=(current_year,)),
        arkansas_site_base=os.getenv("KLS_ARKANSAS_SITE_BASE", "https://www.arkleg.state.ar.us"),
        arkansas_years=_parse_int_list(os.getenv("KLS_ARKANSAS_YEARS"), default=(current_year,)),
        california_site_base=os.getenv("KLS_CALIFORNIA_SITE_BASE", "https://leginfo.legislature.ca.gov"),
        california_years=_parse_int_list(os.getenv("KLS_CALIFORNIA_YEARS"), default=(current_year,)),
        georgia_site_base=os.getenv("KLS_GEORGIA_SITE_BASE", "https://www.legis.ga.gov"),
        georgia_years=_parse_int_list(os.getenv("KLS_GEORGIA_YEARS"), default=(current_odd_session_year,)),
        delaware_site_base=os.getenv("KLS_DELAWARE_SITE_BASE", "https://legis.delaware.gov"),
        delaware_years=_parse_int_list(os.getenv("KLS_DELAWARE_YEARS"), default=(current_odd_session_year,)),
        florida_site_base=os.getenv("KLS_FLORIDA_SITE_BASE", "https://www.flsenate.gov"),
        florida_years=_parse_int_list(os.getenv("KLS_FLORIDA_YEARS"), default=(current_year,)),
        hawaii_site_base=os.getenv("KLS_HAWAII_SITE_BASE", "https://data.capitol.hawaii.gov"),
        hawaii_years=_parse_int_list(os.getenv("KLS_HAWAII_YEARS"), default=(current_year,)),
        idaho_site_base=os.getenv("KLS_IDAHO_SITE_BASE", "https://legislature.idaho.gov"),
        idaho_years=_parse_int_list(os.getenv("KLS_IDAHO_YEARS"), default=(current_year,)),
        indiana_site_base=os.getenv("KLS_INDIANA_SITE_BASE", "https://api.iga.in.gov"),
        indiana_api_key=(os.getenv("KLS_INDIANA_API_KEY", "") or "").strip(),
        indiana_years=_parse_int_list(os.getenv("KLS_INDIANA_YEARS"), default=(current_year,)),
        illinois_site_base=os.getenv("KLS_ILLINOIS_SITE_BASE", "https://www.ilga.gov"),
        illinois_years=_parse_int_list(os.getenv("KLS_ILLINOIS_YEARS"), default=(current_year,)),
        north_dakota_site_base=os.getenv("KLS_NORTH_DAKOTA_SITE_BASE", "https://ndlegis.gov"),
        north_dakota_years=_parse_int_list(os.getenv("KLS_NORTH_DAKOTA_YEARS"), default=(current_north_dakota_year,)),
        iowa_site_base=os.getenv("KLS_IOWA_SITE_BASE", "https://www.legis.iowa.gov"),
        iowa_years=_parse_int_list(os.getenv("KLS_IOWA_YEARS"), default=(current_year,)),
        maryland_site_base=os.getenv("KLS_MARYLAND_SITE_BASE", "https://mgaleg.maryland.gov"),
        maryland_years=_parse_int_list(os.getenv("KLS_MARYLAND_YEARS"), default=(current_year,)),
        massachusetts_site_base=os.getenv("KLS_MASSACHUSETTS_SITE_BASE", "https://malegislature.gov"),
        massachusetts_years=_parse_int_list(os.getenv("KLS_MASSACHUSETTS_YEARS"), default=(current_odd_session_year,)),
        michigan_site_base=os.getenv("KLS_MICHIGAN_SITE_BASE", "https://www.legislature.mi.gov"),
        michigan_years=_parse_int_list(os.getenv("KLS_MICHIGAN_YEARS"), default=(current_year,)),
        washington_site_base=os.getenv("KLS_WASHINGTON_SITE_BASE", "https://app.leg.wa.gov"),
        washington_years=_parse_int_list(os.getenv("KLS_WASHINGTON_YEARS"), default=(current_year,)),
        connecticut_site_base=os.getenv("KLS_CONNECTICUT_SITE_BASE", "https://www.cga.ct.gov"),
        connecticut_years=_parse_int_list(os.getenv("KLS_CONNECTICUT_YEARS"), default=(current_year,)),
        new_mexico_site_base=os.getenv("KLS_NEW_MEXICO_SITE_BASE", "https://www.nmlegis.gov"),
        new_mexico_years=_parse_int_list(os.getenv("KLS_NEW_MEXICO_YEARS"), default=(current_year,)),
        nebraska_site_base=os.getenv("KLS_NEBRASKA_SITE_BASE", "https://nebraskalegislature.gov"),
        nebraska_years=_parse_int_list(os.getenv("KLS_NEBRASKA_YEARS"), default=(current_year,)),
        south_carolina_site_base=os.getenv("KLS_SOUTH_CAROLINA_SITE_BASE", "https://www.scstatehouse.gov"),
        south_carolina_years=_parse_int_list(os.getenv("KLS_SOUTH_CAROLINA_YEARS"), default=(current_year,)),
        south_dakota_site_base=os.getenv("KLS_SOUTH_DAKOTA_SITE_BASE", "https://sdlegislature.gov"),
        south_dakota_document_base=os.getenv("KLS_SOUTH_DAKOTA_DOCUMENT_BASE", "https://mylrc.sdlegislature.gov"),
        south_dakota_years=_parse_int_list(os.getenv("KLS_SOUTH_DAKOTA_YEARS"), default=(current_year,)),
        vermont_site_base=os.getenv("KLS_VERMONT_SITE_BASE", "https://legislature.vermont.gov"),
        vermont_years=_parse_int_list(os.getenv("KLS_VERMONT_YEARS"), default=(current_year,)),
        utah_site_base=os.getenv("KLS_UTAH_SITE_BASE", "https://le.utah.gov"),
        utah_years=_parse_int_list(os.getenv("KLS_UTAH_YEARS"), default=(current_year,)),
        virginia_site_base=os.getenv("KLS_VIRGINIA_SITE_BASE", "https://lis.virginia.gov"),
        virginia_years=_parse_int_list(os.getenv("KLS_VIRGINIA_YEARS"), default=(current_year,)),
        rhode_island_site_base=os.getenv("KLS_RHODE_ISLAND_SITE_BASE", "https://webserver.rilegislature.gov"),
        rhode_island_status_base=os.getenv("KLS_RHODE_ISLAND_STATUS_BASE", "https://status.rilegislature.gov"),
        rhode_island_years=_parse_int_list(os.getenv("KLS_RHODE_ISLAND_YEARS"), default=(current_year,)),
        minnesota_site_base=os.getenv("KLS_MINNESOTA_SITE_BASE", "https://www.revisor.mn.gov"),
        minnesota_years=_parse_int_list(os.getenv("KLS_MINNESOTA_YEARS"), default=(current_year,)),
        missouri_house_base=os.getenv("KLS_MISSOURI_HOUSE_BASE", "https://house.mo.gov"),
        missouri_senate_base=os.getenv("KLS_MISSOURI_SENATE_BASE", "https://www.senate.mo.gov"),
        missouri_years=_parse_int_list(os.getenv("KLS_MISSOURI_YEARS"), default=(current_year,)),
        montana_site_base=os.getenv("KLS_MONTANA_SITE_BASE", "https://bearbeta.legmt.gov"),
        montana_years=_parse_int_list(os.getenv("KLS_MONTANA_YEARS"), default=(current_odd_session_year,)),
        nevada_site_base=os.getenv("KLS_NEVADA_SITE_BASE", "https://www.leg.state.nv.us"),
        nevada_years=_parse_int_list(os.getenv("KLS_NEVADA_YEARS"), default=(current_odd_session_year,)),
        new_hampshire_site_base=os.getenv("KLS_NEW_HAMPSHIRE_SITE_BASE", "https://gc.nh.gov"),
        new_hampshire_years=_parse_int_list(os.getenv("KLS_NEW_HAMPSHIRE_YEARS"), default=(current_year,)),
        new_jersey_site_base=os.getenv("KLS_NEW_JERSEY_SITE_BASE", "https://www.njleg.state.nj.us"),
        new_jersey_years=_parse_int_list(os.getenv("KLS_NEW_JERSEY_YEARS"), default=(current_even_session_year,)),
        district_of_columbia_site_base=os.getenv("KLS_DISTRICT_OF_COLUMBIA_SITE_BASE", "https://lims.dccouncil.gov"),
        district_of_columbia_years=_parse_int_list(
            os.getenv("KLS_DISTRICT_OF_COLUMBIA_YEARS"),
            default=(current_odd_session_year,),
        ),
        new_york_site_base=os.getenv("KLS_NEW_YORK_SITE_BASE", "https://www.nysenate.gov"),
        new_york_api_base=os.getenv("KLS_NEW_YORK_API_BASE", "https://legislation.nysenate.gov/api/3"),
        new_york_api_key=(os.getenv("KLS_NEW_YORK_API_KEY", "") or "").strip(),
        new_york_years=_parse_int_list(os.getenv("KLS_NEW_YORK_YEARS"), default=(current_odd_session_year,)),
        ohio_site_base=os.getenv("KLS_OHIO_SITE_BASE", "https://search-prod.lis.state.oh.us"),
        ohio_public_base=os.getenv("KLS_OHIO_PUBLIC_BASE", "https://www.legislature.ohio.gov"),
        ohio_years=_parse_int_list(os.getenv("KLS_OHIO_YEARS"), default=(current_year,)),
        colorado_site_base=os.getenv("KLS_COLORADO_SITE_BASE", "https://leg.colorado.gov"),
        colorado_years=_parse_int_list(os.getenv("KLS_COLORADO_YEARS"), default=(current_year,)),
        texas_site_base=os.getenv("KLS_TEXAS_SITE_BASE", "https://capitol.texas.gov"),
        texas_ftp_host=os.getenv("KLS_TEXAS_FTP_HOST", "ftp.legis.state.tx.us"),
        texas_years=_parse_int_list(os.getenv("KLS_TEXAS_YEARS"), default=(current_odd_session_year,)),
        oklahoma_site_base=os.getenv("KLS_OKLAHOMA_SITE_BASE", "https://www.oklegislature.gov"),
        oklahoma_reports_base=os.getenv("KLS_OKLAHOMA_REPORTS_BASE", "https://webapps.oklegislature.gov"),
        oklahoma_years=_parse_int_list(os.getenv("KLS_OKLAHOMA_YEARS"), default=(current_year,)),
        oregon_site_base=os.getenv("KLS_OREGON_SITE_BASE", "https://olis.oregonlegislature.gov"),
        oregon_years=_parse_int_list(os.getenv("KLS_OREGON_YEARS"), default=(current_odd_session_year,)),
        pennsylvania_site_base=os.getenv("KLS_PENNSYLVANIA_SITE_BASE", "https://www.palegis.us"),
        pennsylvania_years=_parse_int_list(os.getenv("KLS_PENNSYLVANIA_YEARS"), default=(current_odd_session_year,)),
        tennessee_site_base=os.getenv("KLS_TENNESSEE_SITE_BASE", "https://wapp.capitol.tn.gov"),
        tennessee_bill_base=os.getenv("KLS_TENNESSEE_BILL_BASE", "https://capitol.tn.gov"),
        tennessee_years=_parse_int_list(os.getenv("KLS_TENNESSEE_YEARS"), default=(current_tennessee_year,)),
        mississippi_site_base=os.getenv("KLS_MISSISSIPPI_SITE_BASE", "https://billstatus.ls.state.ms.us"),
        mississippi_years=_parse_int_list(os.getenv("KLS_MISSISSIPPI_YEARS"), default=(current_year,)),
        north_carolina_site_base=os.getenv("KLS_NORTH_CAROLINA_SITE_BASE", "https://www.ncleg.gov"),
        north_carolina_webservices_base=os.getenv("KLS_NORTH_CAROLINA_WEBSERVICES_BASE", "https://webservices.ncleg.gov"),
        north_carolina_years=_parse_int_list(os.getenv("KLS_NORTH_CAROLINA_YEARS"), default=(current_odd_session_year,)),
        wisconsin_site_base=os.getenv("KLS_WISCONSIN_SITE_BASE", "https://docs.legis.wisconsin.gov"),
        wisconsin_years=_parse_int_list(os.getenv("KLS_WISCONSIN_YEARS"), default=(current_odd_session_year,)),
        congress_api_base=os.getenv("KLS_CONGRESS_API_BASE", "https://api.congress.gov/v3"),
        congress_api_key=congress_api_key,
        federal_congresses=_parse_int_list(os.getenv("KLS_FEDERAL_CONGRESSES"), default=(current_congress,)),
        federal_sync_limit=int(os.getenv("KLS_FEDERAL_SYNC_LIMIT", federal_sync_limit_default)),
        request_timeout_seconds=float(os.getenv("KLS_REQUEST_TIMEOUT_SECONDS", "90")),
        ollama_base_url=os.getenv(
            "KLS_OLLAMA_BASE_URL", "http://ai-inference-ollama.ai-platform.svc.cluster.local:11434"
        ),
        ollama_model=os.getenv("KLS_OLLAMA_MODEL", "qwen3.5:27b"),
        ollama_timeout_seconds=float(os.getenv("KLS_OLLAMA_TIMEOUT_SECONDS", "180")),
        sync_parallelism=max(1, int(os.getenv("KLS_SYNC_PARALLELISM", "1"))),
        analytics_enabled=_parse_bool(os.getenv("KLS_ANALYTICS_ENABLED"), default=True),
        analytics_retention_days=max(7, int(os.getenv("KLS_ANALYTICS_RETENTION_DAYS", "180"))),
        analytics_country_db_path=os.getenv("KLS_ANALYTICS_COUNTRY_DB_PATH", "/data/geoip/dbip-city-lite.mmdb"),
        analytics_country_db_url=os.getenv("KLS_ANALYTICS_COUNTRY_DB_URL", ""),
        admin_username=(os.getenv("KLS_ADMIN_USERNAME", "") or "").strip(),
        admin_password=(os.getenv("KLS_ADMIN_PASSWORD", "") or "").strip(),
        analytics_hmac_secret=(os.getenv("KLS_ANALYTICS_HMAC_SECRET", "") or "").strip(),
    )
