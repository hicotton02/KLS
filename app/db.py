from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from app.settings import get_settings
from app.text_utils import iso_now

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional until KLS_DATABASE_URL is configured.
    psycopg = None
    dict_row = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    bill_type TEXT,
    catch_title TEXT,
    sponsor TEXT,
    bill_title TEXT,
    bill_status TEXT,
    status_label TEXT,
    status_explainer TEXT,
    outcome TEXT,
    last_action TEXT,
    last_action_date TEXT,
    signed_date TEXT,
    effective_date TEXT,
    chapter_no TEXT,
    enrolled_no TEXT,
    sponsor_string_house TEXT,
    sponsor_string_senate TEXT,
    introduced_path TEXT,
    digest_path TEXT,
    summary_path TEXT,
    current_version_path TEXT,
    official_digest_text TEXT,
    official_summary_text TEXT,
    current_bill_text TEXT,
    bill_actions_json TEXT,
    interpretation_json TEXT,
    bill_tags_json TEXT,
    search_blob TEXT,
    source_hash TEXT,
    source_synced_at TEXT,
    vote_data_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num)
);

CREATE INDEX IF NOT EXISTS idx_bills_year ON bills(year);
CREATE INDEX IF NOT EXISTS idx_bills_outcome ON bills(outcome);
CREATE INDEX IF NOT EXISTS idx_bills_last_action_date ON bills(last_action_date);
CREATE INDEX IF NOT EXISTS idx_bills_state_year ON bills(state, year);

CREATE TABLE IF NOT EXISTS bill_amendments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    amendment_number TEXT NOT NULL,
    chamber TEXT,
    reading_order TEXT,
    sequence TEXT,
    status TEXT,
    sponsor TEXT,
    document_url TEXT,
    document_text TEXT,
    interpretation_json TEXT,
    source_hash TEXT,
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num, amendment_number)
);

CREATE INDEX IF NOT EXISTS idx_bill_amendments_bill ON bill_amendments(state, year, bill_num, special_session_key);

CREATE TABLE IF NOT EXISTS bill_roll_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    roll_call_key TEXT NOT NULL,
    vote_id TEXT,
    chamber TEXT,
    vote_date TEXT,
    vote_type TEXT,
    action TEXT,
    amendment_number TEXT,
    yes_count INTEGER NOT NULL DEFAULT 0,
    no_count INTEGER NOT NULL DEFAULT 0,
    absent_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    excused_count INTEGER NOT NULL DEFAULT 0,
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num, roll_call_key)
);

CREATE INDEX IF NOT EXISTS idx_bill_roll_calls_bill
ON bill_roll_calls(state, year, bill_num, special_session_key);

CREATE TABLE IF NOT EXISTS bill_roll_call_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    roll_call_key TEXT NOT NULL,
    vote_id TEXT,
    chamber TEXT,
    member_key TEXT NOT NULL,
    source_legislator_id TEXT,
    legislator_name TEXT NOT NULL,
    vote_label TEXT,
    party TEXT,
    district TEXT,
    vote_position TEXT NOT NULL,
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num, roll_call_key, member_key)
);

CREATE INDEX IF NOT EXISTS idx_bill_roll_call_votes_bill
ON bill_roll_call_votes(state, year, bill_num, special_session_key, roll_call_key);
CREATE INDEX IF NOT EXISTS idx_bill_roll_call_votes_member
ON bill_roll_call_votes(state, member_key, year);
CREATE INDEX IF NOT EXISTS idx_bill_roll_call_votes_state_updated
ON bill_roll_call_votes(state, updated_at);

CREATE TABLE IF NOT EXISTS legislator_member_aliases (
    state TEXT NOT NULL,
    alias_member_key TEXT NOT NULL,
    canonical_member_key TEXT NOT NULL,
    resolution_method TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(state, alias_member_key)
);

CREATE INDEX IF NOT EXISTS idx_legislator_member_aliases_canonical
ON legislator_member_aliases(state, canonical_member_key);

CREATE TABLE IF NOT EXISTS legislator_vote_summary_cache (
    state TEXT NOT NULL,
    member_key TEXT NOT NULL,
    year INTEGER NOT NULL,
    source_legislator_id TEXT,
    legislator_name TEXT NOT NULL,
    party TEXT,
    district TEXT,
    chamber TEXT,
    total_votes INTEGER NOT NULL DEFAULT 0,
    bills_voted INTEGER NOT NULL DEFAULT 0,
    yes_count INTEGER NOT NULL DEFAULT 0,
    no_count INTEGER NOT NULL DEFAULT 0,
    absent_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    excused_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(state, member_key, year)
);

CREATE INDEX IF NOT EXISTS idx_legislator_vote_summary_cache_name
ON legislator_vote_summary_cache(state, legislator_name);

CREATE TABLE IF NOT EXISTS legislator_vote_summary_status (
    state TEXT PRIMARY KEY,
    source_marker TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legislative_media (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    session_date TEXT NOT NULL,
    session_day_number TEXT,
    chamber TEXT NOT NULL,
    time_of_day TEXT,
    display_order INTEGER,
    source_url TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    external_id TEXT,
    mime_type TEXT,
    title TEXT,
    duration_seconds INTEGER,
    transcript_status TEXT NOT NULL DEFAULT 'pending',
    transcript_source TEXT,
    transcript_json TEXT,
    transcript_error TEXT,
    transcript_updated_at TEXT,
    explanation_scan_status TEXT NOT NULL DEFAULT 'pending',
    explanation_scan_error TEXT,
    explanation_scanned_at TEXT,
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, source_url)
);

CREATE INDEX IF NOT EXISTS idx_legislative_media_session
ON legislative_media(state, year, session_date, chamber, time_of_day);
CREATE INDEX IF NOT EXISTS idx_legislative_media_work
ON legislative_media(state, transcript_status, explanation_scan_status, year);

CREATE TABLE IF NOT EXISTS pipeline_circuit_breakers (
    name TEXT PRIMARY KEY,
    open_until TEXT NOT NULL,
    reason TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bill_vote_explanation_scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    scan_status TEXT NOT NULL DEFAULT 'pending',
    media_total INTEGER NOT NULL DEFAULT 0,
    media_transcribed INTEGER NOT NULL DEFAULT 0,
    media_scanned INTEGER NOT NULL DEFAULT 0,
    explanation_count INTEGER NOT NULL DEFAULT 0,
    last_scanned_at TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num)
);

CREATE INDEX IF NOT EXISTS idx_bill_vote_explanation_scans_status
ON bill_vote_explanation_scans(state, year, scan_status);

CREATE TABLE IF NOT EXISTS bill_vote_explanations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key INTEGER NOT NULL DEFAULT -1,
    special_session_value INTEGER,
    bill_num TEXT NOT NULL,
    roll_call_key TEXT NOT NULL,
    member_key TEXT NOT NULL,
    lawmaker_name TEXT NOT NULL,
    vote_position TEXT NOT NULL,
    reason_summary TEXT NOT NULL,
    evidence_text TEXT NOT NULL,
    source_media_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT,
    source_start_seconds INTEGER NOT NULL,
    source_end_seconds INTEGER,
    statement_date TEXT,
    source_kind TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'publishable',
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key, bill_num, roll_call_key, member_key, source_media_id)
);

CREATE INDEX IF NOT EXISTS idx_bill_vote_explanations_bill
ON bill_vote_explanations(state, year, bill_num, special_session_key);
CREATE INDEX IF NOT EXISTS idx_bill_vote_explanations_member
ON bill_vote_explanations(state, member_key, year);
CREATE INDEX IF NOT EXISTS idx_bill_vote_explanations_recent
ON bill_vote_explanations(state, statement_date, year);

CREATE TABLE IF NOT EXISTS bill_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    state TEXT NOT NULL,
    year INTEGER NOT NULL,
    special_session_key_a INTEGER NOT NULL DEFAULT -1,
    special_session_value_a INTEGER,
    bill_num_a TEXT NOT NULL,
    special_session_key_b INTEGER NOT NULL DEFAULT -1,
    special_session_value_b INTEGER,
    bill_num_b TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    relationship_strength TEXT NOT NULL,
    confidence_score REAL NOT NULL DEFAULT 0,
    candidate_score REAL NOT NULL DEFAULT 0,
    needs_human_review INTEGER NOT NULL DEFAULT 1,
    pair_summary TEXT,
    combined_effect TEXT,
    why_review TEXT,
    bill_a_evidence_json TEXT,
    bill_b_evidence_json TEXT,
    limits_and_unknowns_json TEXT,
    heuristic_reasons_json TEXT,
    analysis_version INTEGER NOT NULL DEFAULT 1,
    source_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(state, year, special_session_key_a, bill_num_a, special_session_key_b, bill_num_b)
);

CREATE INDEX IF NOT EXISTS idx_bill_relationships_year ON bill_relationships(state, year);
CREATE INDEX IF NOT EXISTS idx_bill_relationships_bill_a ON bill_relationships(state, year, bill_num_a);
CREATE INDEX IF NOT EXISTS idx_bill_relationships_bill_b ON bill_relationships(state, year, bill_num_b);

CREATE TABLE IF NOT EXISTS page_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    host TEXT NOT NULL,
    path TEXT NOT NULL,
    route_label TEXT NOT NULL,
    method TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    referrer_domain TEXT,
    country_code TEXT,
    country_name TEXT,
    region_code TEXT,
    region_name TEXT,
    city_name TEXT,
    latitude REAL,
    longitude REAL,
    visitor_hash TEXT,
    is_bot INTEGER NOT NULL DEFAULT 0,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS sync_status (
    state TEXT PRIMARY KEY,
    years_json TEXT,
    is_running INTEGER NOT NULL DEFAULT 0,
    current_year INTEGER,
    current_bill_num TEXT,
    seen INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    interpreted INTEGER NOT NULL DEFAULT 0,
    validated INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    source_total INTEGER,
    stored_total INTEGER,
    last_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    last_success_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

"""


NAMED_PARAMETER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
JSON_COLUMNS = {"bill_actions_json", "bill_tags_json", "interpretation_json"}
AMENDMENT_JSON_COLUMNS = {"interpretation_json"}
RELATIONSHIP_JSON_COLUMNS = {
    "bill_a_evidence_json",
    "bill_b_evidence_json",
    "limits_and_unknowns_json",
    "heuristic_reasons_json",
}
LEGISLATIVE_MEDIA_JSON_COLUMNS = {"transcript_json"}
EXPLANATION_SCAN_JSON_COLUMNS = {"details_json"}
SYNC_STATUS_JSON_COLUMNS = {"years_json"}
SYNC_STATUS_COLUMN_DEFINITIONS = {
    "source_total": "INTEGER",
    "stored_total": "INTEGER",
}
SEARCH_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9'-]*")
BILL_COLUMN_DEFINITIONS = {
    "bill_tags_json": "TEXT",
    "search_blob": "TEXT",
    "vote_data_synced_at": "TEXT",
}
PAGE_VIEW_COLUMN_DEFINITIONS = {
    "region_code": "TEXT",
    "region_name": "TEXT",
    "city_name": "TEXT",
    "latitude": "REAL",
    "longitude": "REAL",
}
BILL_LIST_COLUMNS = [
    "state",
    "year",
    "special_session_key",
    "special_session_value",
    "bill_num",
    "bill_type",
    "catch_title",
    "sponsor",
    "bill_title",
    "bill_status",
    "status_label",
    "status_explainer",
    "outcome",
    "last_action",
    "last_action_date",
    "signed_date",
    "effective_date",
    "chapter_no",
    "enrolled_no",
    "interpretation_json",
    "bill_tags_json",
    "source_synced_at",
    "created_at",
    "updated_at",
]
BILL_SEARCH_COLUMNS = [*BILL_LIST_COLUMNS, "search_blob"]
POSTGRES_BILL_SEARCH_VECTOR_SQL = """
to_tsvector(
    'simple',
    COALESCE(bill_num, '') || ' ' ||
    COALESCE(catch_title, '') || ' ' ||
    COALESCE(bill_title, '') || ' ' ||
    COALESCE(sponsor, '') || ' ' ||
    COALESCE(status_label, '') || ' ' ||
    COALESCE(status_explainer, '') || ' ' ||
    COALESCE(bill_tags_json, '') || ' ' ||
    COALESCE(interpretation_json, '')
)
""".strip()


def normalize_special_session(value: int | None) -> int:
    return -1 if value is None else int(value)


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


class PostgresCursor:
    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount or 0)

    def fetchone(self) -> dict[str, Any] | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._cursor.fetchall())


class StaticCursor:
    def __init__(self, rows: list[dict[str, Any]], rowcount: int = 0) -> None:
        self._rows = rows
        self.rowcount = rowcount

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows


def _translate_postgres_sql(sql: str, params: Any = None) -> str:
    translated = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    if isinstance(params, Mapping):
        return NAMED_PARAMETER_PATTERN.sub(r"%(\1)s", translated)
    if params is None:
        return translated
    return translated.replace("?", "%s")


def _sanitize_db_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, Mapping):
        return {key: _sanitize_db_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_sanitize_db_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_db_value(item) for item in value]
    return value


class PostgresConnection:
    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def __enter__(self) -> "PostgresConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self._connection.commit()
        else:
            self._connection.rollback()
        self._connection.close()

    def _pragma_table_info(self, sql: str) -> StaticCursor | None:
        match = re.fullmatch(r"\s*PRAGMA\s+table_info\(([A-Za-z_][A-Za-z0-9_]*)\)\s*;?\s*", sql, re.IGNORECASE)
        if not match:
            return None
        table_name = match.group(1)
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            return StaticCursor(list(cursor.fetchall()))

    def execute(self, sql: str, params: Any = None) -> PostgresCursor | StaticCursor:
        pragma_cursor = self._pragma_table_info(sql)
        if pragma_cursor is not None:
            return pragma_cursor
        sanitized_params = _sanitize_db_value(params)
        cursor = self._connection.cursor()
        cursor.execute(_translate_postgres_sql(sql, sanitized_params), sanitized_params)
        return PostgresCursor(cursor)

    def executemany(self, sql: str, rows: Sequence[Any]) -> PostgresCursor:
        sanitized_rows = [_sanitize_db_value(row) for row in rows]
        cursor = self._connection.cursor()
        params: Any = sanitized_rows[0] if sanitized_rows else None
        cursor.executemany(_translate_postgres_sql(sql, params), sanitized_rows)
        return PostgresCursor(cursor)

    def executescript(self, script: str) -> None:
        translated = script.replace("id INTEGER PRIMARY KEY AUTOINCREMENT", "id BIGSERIAL PRIMARY KEY")
        statements = [statement.strip() for statement in translated.split(";") if statement.strip()]
        for statement in statements:
            self.execute(statement)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def connect() -> sqlite3.Connection | PostgresConnection:
    settings = get_settings()
    if settings.database_url:
        if psycopg is None or dict_row is None:
            raise RuntimeError("KLS_DATABASE_URL is configured, but psycopg is not installed.")
        return PostgresConnection(psycopg.connect(settings.database_url, row_factory=dict_row))
    _ensure_parent_dir(settings.database_path)
    connection = sqlite3.connect(settings.database_path, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA foreign_keys=ON;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    return connection


def init_db() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)
        _ensure_bill_columns(connection)
        _ensure_bill_search_index(connection)
        _ensure_page_view_columns(connection)
        _ensure_sync_status_columns(connection)
        connection.commit()


def _ensure_bill_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(bills)").fetchall()}
    for column, definition in BILL_COLUMN_DEFINITIONS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE bills ADD COLUMN {column} {definition}")


def _ensure_bill_search_index(connection: sqlite3.Connection | PostgresConnection) -> None:
    if isinstance(connection, PostgresConnection):
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_bills_search_vector
            ON bills USING GIN ({POSTGRES_BILL_SEARCH_VECTOR_SQL})
            """
        )


def _ensure_page_view_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(page_views)").fetchall()}
    for column, definition in PAGE_VIEW_COLUMN_DEFINITIONS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE page_views ADD COLUMN {column} {definition}")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_occurred_at ON page_views(occurred_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_route_label ON page_views(route_label, occurred_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_country_code ON page_views(country_code, occurred_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_region_code ON page_views(region_code, occurred_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_city_name ON page_views(city_name, occurred_at)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_page_views_referrer_domain ON page_views(referrer_domain, occurred_at)")


def _ensure_sync_status_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(sync_status)").fetchall()}
    for column, definition in SYNC_STATUS_COLUMN_DEFINITIONS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE sync_status ADD COLUMN {column} {definition}")


def _parse_json_field(parsed: dict[str, Any], column: str, default: Any = None) -> None:
    value = parsed.get(column)
    if not value:
        parsed[column] = default
        return
    try:
        parsed[column] = json.loads(value)
    except json.JSONDecodeError:
        parsed[column] = default


def _parse_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed: dict[str, Any] = dict(row)
    _parse_json_field(parsed, "bill_actions_json", default=[])
    _parse_json_field(parsed, "interpretation_json", default=None)
    _parse_json_field(parsed, "bill_tags_json", default=[])
    return parsed


def _parse_amendment_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed: dict[str, Any] = dict(row)
    _parse_json_field(parsed, "interpretation_json", default=None)
    return parsed


def _parse_relationship_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed: dict[str, Any] = dict(row)
    for column in RELATIONSHIP_JSON_COLUMNS:
        _parse_json_field(parsed, column, default=[])
    parsed["needs_human_review"] = bool(parsed.get("needs_human_review"))
    return parsed


def _parse_sync_status_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed: dict[str, Any] = dict(row)
    for column in SYNC_STATUS_JSON_COLUMNS:
        _parse_json_field(parsed, column, default=[])
    parsed["is_running"] = bool(parsed.get("is_running"))
    return parsed


def list_years(state: str = "wy") -> list[int]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT year FROM bills WHERE state = ? ORDER BY year DESC",
            (state,),
        ).fetchall()
    return [int(row["year"]) for row in rows]


def get_jurisdiction_rollups(states: list[str]) -> dict[str, dict[str, Any]]:
    normalized_states = list(dict.fromkeys(str(state or "").strip() for state in states if str(state or "").strip()))
    if not normalized_states:
        return {}
    rows: list[Mapping[str, Any]] = []
    with connect() as connection:
        if isinstance(connection, sqlite3.Connection):
            for state in normalized_states:
                latest = connection.execute(
                    "SELECT MAX(year) AS latest_year FROM bills WHERE state = ?",
                    (state,),
                ).fetchone()
                latest_year = None if latest is None else latest["latest_year"]
                if latest_year is None:
                    continue
                counts = connection.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN outcome = 'active' THEN 1 ELSE 0 END) AS active_count,
                        SUM(CASE WHEN outcome = 'passed' THEN 1 ELSE 0 END) AS passed_count,
                        SUM(CASE WHEN outcome IN ('failed', 'replaced') THEN 1 ELSE 0 END) AS failed_count
                    FROM bills
                    WHERE state = ? AND year = ?
                    """,
                    (state, latest_year),
                ).fetchone()
                if counts is not None:
                    rows.append({"state": state, "latest_year": latest_year, **dict(counts)})
        else:
            placeholders = ", ".join("?" for _ in normalized_states)
            sql = f"""
                WITH latest_years AS (
                    SELECT state, MAX(year) AS latest_year
                    FROM bills
                    WHERE state IN ({placeholders})
                    GROUP BY state
                )
                SELECT
                    bills.state,
                    latest_years.latest_year,
                    SUM(CASE WHEN bills.year = latest_years.latest_year THEN 1 ELSE 0 END) AS total,
                    SUM(CASE WHEN bills.year = latest_years.latest_year AND bills.outcome = 'active' THEN 1 ELSE 0 END) AS active_count,
                    SUM(CASE WHEN bills.year = latest_years.latest_year AND bills.outcome = 'passed' THEN 1 ELSE 0 END) AS passed_count,
                    SUM(
                        CASE
                            WHEN bills.year = latest_years.latest_year AND bills.outcome IN ('failed', 'replaced') THEN 1
                            ELSE 0
                        END
                    ) AS failed_count
                FROM bills
                JOIN latest_years ON latest_years.state = bills.state
                GROUP BY bills.state, latest_years.latest_year
            """
            rows = connection.execute(sql, normalized_states).fetchall()
    return {
        str(row["state"]): {
            "latest_year": int(row["latest_year"]),
            "counts": {
                "total": int(row["total"] or 0),
                "active": int(row["active_count"] or 0),
                "passed": int(row["passed_count"] or 0),
                "failed": int(row["failed_count"] or 0),
            },
        }
        for row in rows
    }


def list_available_tags(state: str | None = None, year: int | None = None) -> list[str]:
    clauses: list[str] = []
    params: list[Any] = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if year is not None:
        clauses.append("year = ?")
        params.append(year)

    sql = "SELECT bill_tags_json FROM bills"
    if clauses:
        sql += f" WHERE {' AND '.join(clauses)}"

    tags: set[str] = set()
    with connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    for row in rows:
        raw = row["bill_tags_json"]
        if not raw:
            continue
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in values:
            text = str(item or "").strip().lower()
            if text:
                tags.add(text)
    return sorted(tags)


def list_bills(
    state: str,
    year: int,
    query: str = "",
    status: str = "all",
    tag: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    bills = _query_bills(
        state=state,
        year=year,
        status=status,
        query=query,
        tag=tag,
        include_search_blob=bool(query.strip()),
        limit=limit,
    )
    return _filter_bill_results(bills, query=query, tag=tag, limit=limit)


def search_bills(
    query: str = "",
    *,
    state: str | None = None,
    year: int | None = None,
    status: str = "all",
    tag: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    bills = _query_bills(
        state=state,
        year=year,
        status=status,
        query=query,
        tag=tag,
        include_search_blob=bool(query.strip()),
        limit=limit,
    )
    return _filter_bill_results(bills, query=query, tag=tag, limit=limit)


def list_recent_bills(limit: int = 8) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    sql = f"""
        SELECT {', '.join(BILL_LIST_COLUMNS)}
        FROM bills
        ORDER BY last_action_date DESC, updated_at DESC, year DESC, bill_num ASC
        LIMIT ?
    """
    with connect() as connection:
        rows = connection.execute(sql, (safe_limit,)).fetchall()
    return [_parse_row(row) for row in rows if row is not None]


def get_bill(state: str, year: int, bill_num: str, special_session_value: int | None = None) -> dict[str, Any] | None:
    params: list[Any] = [state, year, bill_num]
    sql = "SELECT * FROM bills WHERE state = ? AND year = ? AND bill_num = ?"
    if special_session_value is not None:
        sql += " AND special_session_key = ?"
        params.append(normalize_special_session(special_session_value))
    sql += " ORDER BY special_session_key ASC LIMIT 1"
    with connect() as connection:
        row = connection.execute(sql, params).fetchone()
    return _parse_row(row)


def list_bill_amendments(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
) -> list[dict[str, Any]]:
    params: list[Any] = [state, year, bill_num]
    sql = "SELECT * FROM bill_amendments WHERE state = ? AND year = ? AND bill_num = ?"
    if special_session_value is not None:
        sql += " AND special_session_key = ?"
        params.append(normalize_special_session(special_session_value))
    sql += " ORDER BY chamber ASC, reading_order ASC, sequence ASC, amendment_number ASC"
    with connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_parse_amendment_row(row) for row in rows if row is not None]


def get_dashboard_counts(state: str, year: int) -> dict[str, int]:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome = 'active' THEN 1 ELSE 0 END) AS active_count,
                SUM(CASE WHEN outcome = 'passed' THEN 1 ELSE 0 END) AS passed_count,
                SUM(CASE WHEN outcome IN ('failed', 'replaced') THEN 1 ELSE 0 END) AS failed_count
            FROM bills
            WHERE state = ? AND year = ?
            """,
            (state, year),
        ).fetchone()
    return {
        "total": int(row["total"] or 0),
        "active": int(row["active_count"] or 0),
        "passed": int(row["passed_count"] or 0),
        "failed": int(row["failed_count"] or 0),
    }


def get_latest_bill_refresh(state: str) -> str | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT MAX(COALESCE(source_synced_at, updated_at, created_at)) AS latest_refresh
            FROM bills
            WHERE state = ?
            """,
            (state,),
        ).fetchone()
    latest_refresh = None if row is None else row["latest_refresh"]
    if latest_refresh is None:
        return None
    text = str(latest_refresh).strip()
    return text or None


def count_bills_for_year(state: str, year: int) -> int:
    with connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM bills WHERE state = ? AND year = ?",
            (state, year),
        ).fetchone()
    return 0 if row is None else int(row["total"] or 0)


def get_sync_status(state: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM sync_status WHERE state = ?", (state,)).fetchone()
    return _parse_sync_status_row(row)


def list_sync_statuses(states: list[str] | None = None) -> dict[str, dict[str, Any]]:
    params: list[Any] = []
    sql = "SELECT * FROM sync_status"
    if states:
        placeholders = ", ".join("?" for _ in states)
        sql += f" WHERE state IN ({placeholders})"
        params.extend(states)
    sql += " ORDER BY state ASC"
    with connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    parsed_rows = [_parse_sync_status_row(row) for row in rows]
    return {
        str(row["state"]): row
        for row in parsed_rows
        if row is not None and str(row.get("state") or "").strip()
    }


def update_sync_status(state: str, **changes: Any) -> None:
    existing = get_sync_status(state) or {}
    timestamp = iso_now()
    payload: dict[str, Any] = {
        "state": state,
        "years_json": existing.get("years_json") or [],
        "is_running": 1 if existing.get("is_running") else 0,
        "current_year": existing.get("current_year"),
        "current_bill_num": existing.get("current_bill_num"),
        "seen": int(existing.get("seen") or 0),
        "updated": int(existing.get("updated") or 0),
        "skipped": int(existing.get("skipped") or 0),
        "interpreted": int(existing.get("interpreted") or 0),
        "validated": int(existing.get("validated") or 0),
        "failed": int(existing.get("failed") or 0),
        "source_total": existing.get("source_total"),
        "stored_total": existing.get("stored_total"),
        "last_message": existing.get("last_message"),
        "started_at": existing.get("started_at"),
        "finished_at": existing.get("finished_at"),
        "last_success_at": existing.get("last_success_at"),
        "created_at": existing.get("created_at") or timestamp,
        "updated_at": timestamp,
    }
    payload.update(changes)
    payload["state"] = state
    payload["is_running"] = 1 if payload.get("is_running") else 0
    payload["years_json"] = json.dumps(payload.get("years_json") or [])
    payload["updated_at"] = timestamp

    columns = [
        "state",
        "years_json",
        "is_running",
        "current_year",
        "current_bill_num",
        "seen",
        "updated",
        "skipped",
        "interpreted",
        "validated",
        "failed",
        "source_total",
        "stored_total",
        "last_message",
        "started_at",
        "finished_at",
        "last_success_at",
        "created_at",
        "updated_at",
    ]
    update_columns = [column for column in columns if column not in {"state", "created_at"}]
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in update_columns)

    with connect() as connection:
        connection.execute(
            f"""
            INSERT INTO sync_status ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(state)
            DO UPDATE SET {updates}
            """,
            payload,
        )
        connection.commit()


def get_existing_index(years: list[int], state: str = "wy") -> dict[tuple[int, int, str], dict[str, Any]]:
    if not years:
        return {}
    placeholders = ", ".join("?" for _ in years)
    sql = f"""
        SELECT
            year,
            special_session_key,
            bill_num,
            bill_status,
            last_action,
            last_action_date,
            signed_date,
            effective_date,
            chapter_no,
            enrolled_no,
            source_hash,
            vote_data_synced_at,
            interpretation_json
        FROM bills
        WHERE state = ? AND year IN ({placeholders})
    """
    with connect() as connection:
        rows = connection.execute(sql, [state, *years]).fetchall()
    index: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row["year"]), int(row["special_session_key"]), row["bill_num"])
        payload = dict(row)
        raw_interpretation = payload.pop("interpretation_json", None)
        payload["has_interpretation"] = 1 if raw_interpretation else 0
        payload["fact_check_status"] = ""
        payload["fact_check_version"] = 0
        payload["generator_model"] = ""
        if raw_interpretation:
            try:
                interpretation = json.loads(raw_interpretation)
            except json.JSONDecodeError:
                interpretation = None
            if isinstance(interpretation, dict):
                payload["fact_check_status"] = str(interpretation.get("fact_check_status", "")).strip()
                payload["generator_model"] = str(interpretation.get("generator_model", "")).strip()
                version = interpretation.get("fact_check_version")
                if isinstance(version, int):
                    payload["fact_check_version"] = version
                elif isinstance(version, str) and version.isdigit():
                    payload["fact_check_version"] = int(version)
        index[key] = payload
    return index


def upsert_bill(payload: dict[str, Any]) -> None:
    serializable = dict(payload)
    serializable["special_session_key"] = normalize_special_session(payload.get("special_session_value"))
    serializable.setdefault("bill_tags_json", [])
    serializable.setdefault("search_blob", "")
    serializable.setdefault("vote_data_synced_at", None)
    for column in JSON_COLUMNS:
        value = serializable.get(column)
        serializable[column] = json.dumps(value) if value is not None else None

    columns = [
        "state",
        "year",
        "special_session_key",
        "special_session_value",
        "bill_num",
        "bill_type",
        "catch_title",
        "sponsor",
        "bill_title",
        "bill_status",
        "status_label",
        "status_explainer",
        "outcome",
        "last_action",
        "last_action_date",
        "signed_date",
        "effective_date",
        "chapter_no",
        "enrolled_no",
        "sponsor_string_house",
        "sponsor_string_senate",
        "introduced_path",
        "digest_path",
        "summary_path",
        "current_version_path",
        "official_digest_text",
        "official_summary_text",
        "current_bill_text",
        "bill_actions_json",
        "interpretation_json",
        "bill_tags_json",
        "search_blob",
        "source_hash",
        "source_synced_at",
        "vote_data_synced_at",
        "created_at",
        "updated_at",
    ]
    update_columns = [column for column in columns if column not in {"state", "year", "special_session_key", "bill_num", "created_at"}]
    placeholders = ", ".join(f":{column}" for column in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in update_columns)

    with connect() as connection:
        connection.execute(
            f"""
            INSERT INTO bills ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(state, year, special_session_key, bill_num)
            DO UPDATE SET {updates}
            """,
            serializable,
        )
        connection.commit()


def reset_stale_sync_statuses(max_age_seconds: int, states: list[str] | None = None) -> int:
    if max_age_seconds <= 0:
        return 0

    timestamp = iso_now()
    cutoff = (datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=max_age_seconds)).isoformat()
    message = f"Cleared stale running marker after no progress for {max_age_seconds} seconds."
    params: list[Any] = [timestamp, message, timestamp, cutoff]
    sql = """
        UPDATE sync_status
        SET is_running = 0,
            current_bill_num = '',
            finished_at = ?,
            last_message = ?,
            updated_at = ?
        WHERE is_running <> 0
          AND COALESCE(updated_at, started_at, created_at, '') < ?
    """
    normalized_states = [state for state in (states or []) if str(state or "").strip()]
    if normalized_states:
        placeholders = ", ".join("?" for _ in normalized_states)
        sql += f" AND state IN ({placeholders})"
        params.extend(normalized_states)

    with connect() as connection:
        cursor = connection.execute(sql, params)
        connection.commit()
        return int(cursor.rowcount or 0)


def replace_bill_amendments(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
    payloads: list[dict[str, Any]],
) -> None:
    special_session_key = normalize_special_session(special_session_value)
    columns = [
        "state",
        "year",
        "special_session_key",
        "special_session_value",
        "bill_num",
        "amendment_number",
        "chamber",
        "reading_order",
        "sequence",
        "status",
        "sponsor",
        "document_url",
        "document_text",
        "interpretation_json",
        "source_hash",
        "source_synced_at",
        "created_at",
        "updated_at",
    ]

    serializable_rows: list[dict[str, Any]] = []
    for payload in payloads:
        item = dict(payload)
        item["special_session_key"] = normalize_special_session(item.get("special_session_value"))
        for column in AMENDMENT_JSON_COLUMNS:
            value = item.get(column)
            item[column] = json.dumps(value) if value is not None else None
        serializable_rows.append(item)

    placeholders = ", ".join(f":{column}" for column in columns)
    with connect() as connection:
        connection.execute(
            """
            DELETE FROM bill_amendments
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            """,
            (state, year, bill_num, special_session_key),
        )
        if serializable_rows:
            connection.executemany(
                f"""
                INSERT INTO bill_amendments ({', '.join(columns)})
                VALUES ({placeholders})
                """,
                serializable_rows,
            )
        connection.commit()


def replace_bill_roll_calls(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
    payloads: list[dict[str, Any]],
) -> None:
    special_session_key = normalize_special_session(special_session_value)
    roll_call_columns = [
        "state",
        "year",
        "special_session_key",
        "special_session_value",
        "bill_num",
        "roll_call_key",
        "vote_id",
        "chamber",
        "vote_date",
        "vote_type",
        "action",
        "amendment_number",
        "yes_count",
        "no_count",
        "absent_count",
        "conflict_count",
        "excused_count",
        "source_synced_at",
        "created_at",
        "updated_at",
    ]
    member_columns = [
        "state",
        "year",
        "special_session_key",
        "special_session_value",
        "bill_num",
        "roll_call_key",
        "vote_id",
        "chamber",
        "member_key",
        "source_legislator_id",
        "legislator_name",
        "vote_label",
        "party",
        "district",
        "vote_position",
        "source_synced_at",
        "created_at",
        "updated_at",
    ]

    roll_call_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    for payload in payloads:
        item = dict(payload)
        members = list(item.pop("members", []) or [])
        item["state"] = state
        item["year"] = year
        item["bill_num"] = bill_num
        item["special_session_value"] = special_session_value
        item["special_session_key"] = special_session_key
        roll_call_rows.append(item)
        for member_payload in members:
            member = dict(member_payload)
            member.update(
                {
                    "state": state,
                    "year": year,
                    "special_session_key": special_session_key,
                    "special_session_value": special_session_value,
                    "bill_num": bill_num,
                    "roll_call_key": item["roll_call_key"],
                    "vote_id": item.get("vote_id"),
                    "chamber": item.get("chamber"),
                    "source_synced_at": item.get("source_synced_at"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                }
            )
            member_rows.append(member)

    roll_call_placeholders = ", ".join(f":{column}" for column in roll_call_columns)
    member_placeholders = ", ".join(f":{column}" for column in member_columns)
    with connect() as connection:
        connection.execute(
            """
            DELETE FROM bill_roll_call_votes
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            """,
            (state, year, bill_num, special_session_key),
        )
        connection.execute(
            """
            DELETE FROM bill_roll_calls
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            """,
            (state, year, bill_num, special_session_key),
        )
        if roll_call_rows:
            connection.executemany(
                f"""
                INSERT INTO bill_roll_calls ({', '.join(roll_call_columns)})
                VALUES ({roll_call_placeholders})
                """,
                roll_call_rows,
            )
        if member_rows:
            connection.executemany(
                f"""
                INSERT INTO bill_roll_call_votes ({', '.join(member_columns)})
                VALUES ({member_placeholders})
                """,
                member_rows,
            )
        connection.commit()


def mark_bill_vote_data_synced(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
    timestamp: str,
) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE bills
            SET vote_data_synced_at = ?
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            """,
            (timestamp, state, year, bill_num, normalize_special_session(special_session_value)),
        )
        connection.commit()


def list_bill_roll_calls(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
) -> list[dict[str, Any]]:
    special_session_key = normalize_special_session(special_session_value)
    params = (state, year, bill_num, special_session_key)
    with connect() as connection:
        roll_call_rows = connection.execute(
            """
            SELECT *
            FROM bill_roll_calls
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            ORDER BY vote_date DESC, roll_call_key DESC
            """,
            params,
        ).fetchall()
        member_rows = connection.execute(
            """
            SELECT *
            FROM bill_roll_call_votes
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            ORDER BY roll_call_key, vote_position, legislator_name
            """,
            params,
        ).fetchall()

    members_by_roll_call: dict[str, list[dict[str, Any]]] = {}
    for row in member_rows:
        member = dict(row)
        members_by_roll_call.setdefault(str(member["roll_call_key"]), []).append(member)

    roll_calls = []
    for row in roll_call_rows:
        roll_call = dict(row)
        roll_call["members"] = members_by_roll_call.get(str(roll_call["roll_call_key"]), [])
        roll_calls.append(roll_call)
    return roll_calls


def _parse_legislative_media_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed = dict(row)
    _parse_json_field(parsed, "transcript_json", default=[])
    return parsed


def _parse_explanation_scan_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    parsed = dict(row)
    _parse_json_field(parsed, "details_json", default={})
    return parsed


def get_pipeline_circuit_breaker(name: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM pipeline_circuit_breakers WHERE name = ?",
            (str(name),),
        ).fetchone()
    return dict(row) if row is not None else None


def pipeline_circuit_breaker_is_open(name: str, *, now: datetime | None = None) -> bool:
    row = get_pipeline_circuit_breaker(name)
    if row is None:
        return False
    try:
        open_until = datetime.fromisoformat(str(row["open_until"]))
    except (TypeError, ValueError):
        return False
    if open_until.tzinfo is None:
        open_until = open_until.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return open_until > current


def open_pipeline_circuit_breaker(name: str, *, cooldown_seconds: int, reason: str | None = None) -> str:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    open_until = (now + timedelta(seconds=max(1, int(cooldown_seconds)))).isoformat()
    payload = {
        "name": str(name),
        "open_until": open_until,
        "reason": str(reason or "")[:1000] or None,
        "updated_at": now.isoformat(),
    }
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO pipeline_circuit_breakers (name, open_until, reason, updated_at)
            VALUES (:name, :open_until, :reason, :updated_at)
            ON CONFLICT(name) DO UPDATE SET
                open_until = excluded.open_until,
                reason = excluded.reason,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        connection.commit()
    return open_until


def upsert_legislative_media(payload: dict[str, Any]) -> int:
    now = iso_now()
    item = {
        "state": str(payload["state"]),
        "year": int(payload["year"]),
        "special_session_key": normalize_special_session(payload.get("special_session_value")),
        "special_session_value": payload.get("special_session_value"),
        "session_date": str(payload["session_date"])[:10],
        "session_day_number": payload.get("session_day_number"),
        "chamber": str(payload["chamber"]),
        "time_of_day": payload.get("time_of_day"),
        "display_order": payload.get("display_order"),
        "source_url": str(payload["source_url"]),
        "source_kind": str(payload["source_kind"]),
        "external_id": payload.get("external_id"),
        "mime_type": payload.get("mime_type"),
        "title": payload.get("title"),
        "source_synced_at": payload.get("source_synced_at") or now,
        "created_at": payload.get("created_at") or now,
        "updated_at": payload.get("updated_at") or now,
    }
    columns = list(item)
    with connect() as connection:
        connection.execute(
            f"""
            INSERT INTO legislative_media ({', '.join(columns)})
            VALUES ({', '.join(f':{column}' for column in columns)})
            ON CONFLICT(state, year, special_session_key, source_url) DO UPDATE SET
                session_date = excluded.session_date,
                session_day_number = excluded.session_day_number,
                chamber = excluded.chamber,
                time_of_day = excluded.time_of_day,
                display_order = excluded.display_order,
                source_kind = excluded.source_kind,
                external_id = excluded.external_id,
                mime_type = excluded.mime_type,
                title = COALESCE(legislative_media.title, excluded.title),
                source_synced_at = excluded.source_synced_at,
                updated_at = excluded.updated_at
            """,
            item,
        )
        row = connection.execute(
            """
            SELECT id FROM legislative_media
            WHERE state = ? AND year = ? AND special_session_key = ? AND source_url = ?
            """,
            (item["state"], item["year"], item["special_session_key"], item["source_url"]),
        ).fetchone()
        connection.commit()
    if row is None:
        raise RuntimeError("Stored legislative media could not be reloaded")
    return int(row["id"])


def get_legislative_media(media_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute("SELECT * FROM legislative_media WHERE id = ?", (media_id,)).fetchone()
    return _parse_legislative_media_row(row)


def list_legislative_media(
    state: str,
    *,
    years: Sequence[int] | None = None,
    transcript_statuses: Sequence[str] | None = None,
    explanation_scan_statuses: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    clauses = ["state = ?"]
    params: list[Any] = [state]
    if years:
        clauses.append(f"year IN ({', '.join('?' for _ in years)})")
        params.extend(int(year) for year in years)
    if transcript_statuses:
        clauses.append(f"transcript_status IN ({', '.join('?' for _ in transcript_statuses)})")
        params.extend(str(status) for status in transcript_statuses)
    if explanation_scan_statuses:
        clauses.append(f"explanation_scan_status IN ({', '.join('?' for _ in explanation_scan_statuses)})")
        params.extend(str(status) for status in explanation_scan_statuses)
    limit_sql = ""
    if limit is not None:
        limit_sql = " LIMIT ?"
        params.append(max(1, int(limit)))
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM legislative_media
            WHERE {' AND '.join(clauses)}
            ORDER BY year DESC, session_date DESC, chamber ASC, time_of_day ASC, id ASC
            {limit_sql}
            """,
            params,
        ).fetchall()
    return [parsed for row in rows if (parsed := _parse_legislative_media_row(row)) is not None]


def _claim_legislative_media(
    state: str,
    *,
    years: Sequence[int] | None,
    status_column: str,
    timestamp_column: str,
    error_column: str,
    ready_statuses: Sequence[str],
    claimed_status: str,
    stale_after_seconds: int,
    retry_statuses: Sequence[str] = (),
    retry_after_seconds: int | None = None,
    required_status_column: str | None = None,
    required_statuses: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    allowed_columns = {
        "transcript_status",
        "transcript_updated_at",
        "transcript_error",
        "explanation_scan_status",
        "explanation_scanned_at",
        "explanation_scan_error",
    }
    selected_columns = {status_column, timestamp_column, error_column}
    if required_status_column:
        selected_columns.add(required_status_column)
    if not selected_columns.issubset(allowed_columns):
        raise ValueError("Unsupported legislative media claim column")

    now = iso_now()
    stale_before = (
        datetime.now(timezone.utc) - timedelta(seconds=max(1, int(stale_after_seconds)))
    ).isoformat(timespec="seconds")
    status_placeholders = ", ".join("?" for _ in ready_statuses)
    claimable_parts = [f"{status_column} IN ({status_placeholders})"]
    claimable_params = [str(status) for status in ready_statuses]
    if retry_statuses:
        if retry_after_seconds is None:
            raise ValueError("retry_after_seconds is required when retry_statuses are configured")
        retry_before = (
            datetime.now(timezone.utc) - timedelta(seconds=max(1, int(retry_after_seconds)))
        ).isoformat(timespec="seconds")
        retry_placeholders = ", ".join("?" for _ in retry_statuses)
        claimable_parts.append(
            f"({status_column} IN ({retry_placeholders}) "
            f"AND ({timestamp_column} IS NULL OR {timestamp_column} <= ?))"
        )
        claimable_params.extend(str(status) for status in retry_statuses)
        claimable_params.append(retry_before)
    claimable_parts.append(
        f"({status_column} = ? AND ({timestamp_column} IS NULL OR {timestamp_column} <= ?))"
    )
    claimable_params.extend((claimed_status, stale_before))
    claimable_sql = f"({' OR '.join(claimable_parts)})"
    clauses = ["state = ?"]
    params: list[Any] = [str(state)]
    if years:
        clauses.append(f"year IN ({', '.join('?' for _ in years)})")
        params.extend(int(year) for year in years)
    if required_status_column and required_statuses:
        clauses.append(f"{required_status_column} IN ({', '.join('?' for _ in required_statuses)})")
        params.extend(str(status) for status in required_statuses)
    clauses.append(claimable_sql)
    params.extend(claimable_params)

    with connect() as connection:
        candidates = connection.execute(
            f"""
            SELECT id FROM legislative_media
            WHERE {' AND '.join(clauses)}
            ORDER BY year DESC, session_date DESC, chamber ASC, time_of_day ASC, id ASC
            LIMIT 100
            """,
            params,
        ).fetchall()
        for candidate in candidates:
            media_id = int(candidate["id"])
            update_clauses = [claimable_sql]
            update_params: list[Any] = [claimed_status, now, now, media_id]
            update_params.extend(claimable_params)
            if required_status_column and required_statuses:
                update_clauses.append(
                    f"{required_status_column} IN ({', '.join('?' for _ in required_statuses)})"
                )
                update_params.extend(str(status) for status in required_statuses)
            cursor = connection.execute(
                f"""
                UPDATE legislative_media
                SET {status_column} = ?, {error_column} = NULL, {timestamp_column} = ?, updated_at = ?
                WHERE id = ? AND {' AND '.join(update_clauses)}
                """,
                update_params,
            )
            if cursor.rowcount != 1:
                continue
            row = connection.execute("SELECT * FROM legislative_media WHERE id = ?", (media_id,)).fetchone()
            connection.commit()
            return _parse_legislative_media_row(row)
        connection.commit()
    return None


def claim_legislative_media_transcription(
    state: str,
    *,
    years: Sequence[int] | None = None,
    stale_after_seconds: int = 3 * 60 * 60,
    retry_after_seconds: int = 6 * 60 * 60,
) -> dict[str, Any] | None:
    return _claim_legislative_media(
        state,
        years=years,
        status_column="transcript_status",
        timestamp_column="transcript_updated_at",
        error_column="transcript_error",
        ready_statuses=("pending", "needs_transcription"),
        claimed_status="transcribing",
        stale_after_seconds=stale_after_seconds,
        retry_statuses=("failed",),
        retry_after_seconds=retry_after_seconds,
    )


def claim_legislative_media_explanation_scan(
    state: str,
    *,
    years: Sequence[int] | None = None,
    stale_after_seconds: int = 6 * 60 * 60,
    retry_after_seconds: int = 6 * 60 * 60,
) -> dict[str, Any] | None:
    return _claim_legislative_media(
        state,
        years=years,
        status_column="explanation_scan_status",
        timestamp_column="explanation_scanned_at",
        error_column="explanation_scan_error",
        ready_statuses=("pending",),
        claimed_status="scanning",
        stale_after_seconds=stale_after_seconds,
        retry_statuses=("failed",),
        retry_after_seconds=retry_after_seconds,
        required_status_column="transcript_status",
        required_statuses=("available",),
    )


def update_legislative_media_transcript(
    media_id: int,
    *,
    status: str,
    transcript_source: str | None = None,
    segments: list[dict[str, Any]] | None = None,
    title: str | None = None,
    duration_seconds: int | None = None,
    error: str | None = None,
) -> None:
    now = iso_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE legislative_media
            SET transcript_status = ?,
                transcript_source = ?,
                transcript_json = ?,
                transcript_error = ?,
                transcript_updated_at = ?,
                title = COALESCE(?, title),
                duration_seconds = COALESCE(?, duration_seconds),
                explanation_scan_status = CASE WHEN ? = 'available' THEN 'pending' ELSE explanation_scan_status END,
                explanation_scan_error = CASE WHEN ? = 'available' THEN NULL ELSE explanation_scan_error END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                transcript_source,
                json.dumps(segments) if segments is not None else None,
                error,
                now,
                title,
                duration_seconds,
                status,
                status,
                now,
                media_id,
            ),
        )
        connection.commit()


def mark_legislative_media_explanation_scan(
    media_id: int,
    *,
    status: str,
    error: str | None = None,
) -> None:
    now = iso_now()
    with connect() as connection:
        connection.execute(
            """
            UPDATE legislative_media
            SET explanation_scan_status = ?, explanation_scan_error = ?, explanation_scanned_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error, now, now, media_id),
        )
        connection.commit()


def list_roll_calls_for_session(
    state: str,
    year: int,
    session_date: str,
    chamber: str,
    *,
    special_session_value: int | None = None,
) -> list[dict[str, Any]]:
    special_session_key = normalize_special_session(special_session_value)
    params = (state, year, special_session_key, chamber, f"{session_date[:10]}%")
    with connect() as connection:
        roll_call_rows = connection.execute(
            """
            SELECT roll_calls.*, bills.catch_title, bills.bill_title
            FROM bill_roll_calls AS roll_calls
            JOIN bills
              ON bills.state = roll_calls.state
             AND bills.year = roll_calls.year
             AND bills.special_session_key = roll_calls.special_session_key
             AND bills.bill_num = roll_calls.bill_num
            WHERE roll_calls.state = ?
              AND roll_calls.year = ?
              AND roll_calls.special_session_key = ?
              AND roll_calls.chamber = ?
              AND roll_calls.vote_date LIKE ?
            ORDER BY roll_calls.vote_date ASC, roll_calls.bill_num ASC, roll_calls.roll_call_key ASC
            """,
            params,
        ).fetchall()
        member_rows = connection.execute(
            """
            SELECT * FROM bill_roll_call_votes
            WHERE state = ? AND year = ? AND special_session_key = ? AND chamber = ?
              AND roll_call_key IN (
                SELECT roll_call_key FROM bill_roll_calls
                WHERE state = ? AND year = ? AND special_session_key = ? AND chamber = ? AND vote_date LIKE ?
              )
            ORDER BY roll_call_key, legislator_name
            """,
            (*params[:4], *params),
        ).fetchall()

    members_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in member_rows:
        item = dict(row)
        key = (str(item["bill_num"]), str(item["roll_call_key"]))
        members_by_key.setdefault(key, []).append(item)
    results: list[dict[str, Any]] = []
    for row in roll_call_rows:
        item = dict(row)
        key = (str(item["bill_num"]), str(item["roll_call_key"]))
        item["members"] = members_by_key.get(key, [])
        results.append(item)
    return results


def replace_media_vote_explanations(
    media_id: int,
    payloads: list[dict[str, Any]],
    *,
    replace_non_curated: bool = True,
) -> None:
    columns = [
        "state",
        "year",
        "special_session_key",
        "special_session_value",
        "bill_num",
        "roll_call_key",
        "member_key",
        "lawmaker_name",
        "vote_position",
        "reason_summary",
        "evidence_text",
        "source_media_id",
        "source_url",
        "source_title",
        "source_start_seconds",
        "source_end_seconds",
        "statement_date",
        "source_kind",
        "review_status",
        "source_synced_at",
        "created_at",
        "updated_at",
    ]
    now = iso_now()
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        row = dict(payload)
        row["special_session_key"] = normalize_special_session(row.get("special_session_value"))
        row["source_media_id"] = media_id
        row.setdefault("review_status", "publishable")
        row.setdefault("source_synced_at", now)
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        rows.append(row)
    with connect() as connection:
        if replace_non_curated:
            connection.execute(
                "DELETE FROM bill_vote_explanations WHERE source_media_id = ? AND review_status <> 'curated'",
                (media_id,),
            )
        if rows:
            connection.executemany(
                f"""
                INSERT INTO bill_vote_explanations ({', '.join(columns)})
                VALUES ({', '.join(f':{column}' for column in columns)})
                ON CONFLICT(state, year, special_session_key, bill_num, roll_call_key, member_key, source_media_id)
                DO NOTHING
                """,
                rows,
            )
        connection.commit()


def upsert_bill_vote_explanation_scan(payload: dict[str, Any]) -> None:
    upsert_bill_vote_explanation_scans([payload])


def upsert_bill_vote_explanation_scans(payloads: Sequence[dict[str, Any]]) -> None:
    if not payloads:
        return
    now = iso_now()
    items = [
        {
            "state": str(payload["state"]),
            "year": int(payload["year"]),
            "special_session_key": normalize_special_session(payload.get("special_session_value")),
            "special_session_value": payload.get("special_session_value"),
            "bill_num": str(payload["bill_num"]),
            "scan_status": str(payload["scan_status"]),
            "media_total": int(payload.get("media_total") or 0),
            "media_transcribed": int(payload.get("media_transcribed") or 0),
            "media_scanned": int(payload.get("media_scanned") or 0),
            "explanation_count": int(payload.get("explanation_count") or 0),
            "last_scanned_at": payload.get("last_scanned_at"),
            "details_json": json.dumps(payload.get("details") or {}),
            "created_at": payload.get("created_at") or now,
            "updated_at": payload.get("updated_at") or now,
        }
        for payload in payloads
    ]
    columns = list(items[0])
    sql = f"""
        INSERT INTO bill_vote_explanation_scans ({', '.join(columns)})
        VALUES ({', '.join(f':{column}' for column in columns)})
        ON CONFLICT(state, year, special_session_key, bill_num) DO UPDATE SET
            scan_status = excluded.scan_status,
            media_total = excluded.media_total,
            media_transcribed = excluded.media_transcribed,
            media_scanned = excluded.media_scanned,
            explanation_count = excluded.explanation_count,
            last_scanned_at = excluded.last_scanned_at,
            details_json = excluded.details_json,
            updated_at = excluded.updated_at
        """
    with connect() as connection:
        connection.executemany(sql, items)
        connection.commit()


def get_bill_vote_explanation_scan(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM bill_vote_explanation_scans
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
            """,
            (state, year, bill_num, normalize_special_session(special_session_value)),
        ).fetchone()
    return _parse_explanation_scan_row(row)


def list_bill_vote_explanations(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT explanations.*,
                   votes.party,
                   votes.district,
                   roll_calls.chamber,
                   roll_calls.vote_date,
                   roll_calls.action
            FROM bill_vote_explanations AS explanations
            LEFT JOIN bill_roll_call_votes AS votes
              ON votes.state = explanations.state
             AND votes.year = explanations.year
             AND votes.special_session_key = explanations.special_session_key
             AND votes.bill_num = explanations.bill_num
             AND votes.roll_call_key = explanations.roll_call_key
             AND votes.member_key = explanations.member_key
            LEFT JOIN bill_roll_calls AS roll_calls
              ON roll_calls.state = explanations.state
             AND roll_calls.year = explanations.year
             AND roll_calls.special_session_key = explanations.special_session_key
             AND roll_calls.bill_num = explanations.bill_num
             AND roll_calls.roll_call_key = explanations.roll_call_key
            WHERE explanations.state = ?
              AND explanations.year = ?
              AND explanations.bill_num = ?
              AND explanations.special_session_key = ?
              AND explanations.review_status IN ('publishable', 'curated')
            ORDER BY explanations.statement_date DESC, explanations.source_start_seconds ASC, explanations.lawmaker_name ASC
            """,
            (state, year, bill_num, normalize_special_session(special_session_value)),
        ).fetchall()
    return [dict(row) for row in rows]


def list_legislator_vote_explanations(
    state: str,
    member_key: str,
    *,
    year: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    canonical_member_key = _canonical_legislator_member_key(state, member_key)
    clauses = [
        "explanations.state = ?",
        """(
            explanations.member_key = ?
            OR explanations.member_key IN (
                SELECT alias_member_key
                FROM legislator_member_aliases
                WHERE state = ? AND canonical_member_key = ?
            )
        )""",
        "explanations.review_status IN ('publishable', 'curated')",
    ]
    params: list[Any] = [state, canonical_member_key, state, canonical_member_key]
    if year is not None:
        clauses.append("explanations.year = ?")
        params.append(year)
    params.append(max(1, min(int(limit), 100)))

    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT explanations.*,
                   votes.party,
                   votes.district,
                   roll_calls.chamber,
                   roll_calls.vote_date,
                   roll_calls.action
            FROM bill_vote_explanations AS explanations
            LEFT JOIN bill_roll_call_votes AS votes
              ON votes.state = explanations.state
             AND votes.year = explanations.year
             AND votes.special_session_key = explanations.special_session_key
             AND votes.bill_num = explanations.bill_num
             AND votes.roll_call_key = explanations.roll_call_key
             AND votes.member_key = explanations.member_key
            LEFT JOIN bill_roll_calls AS roll_calls
              ON roll_calls.state = explanations.state
             AND roll_calls.year = explanations.year
             AND roll_calls.special_session_key = explanations.special_session_key
             AND roll_calls.bill_num = explanations.bill_num
             AND roll_calls.roll_call_key = explanations.roll_call_key
            WHERE {' AND '.join(clauses)}
            ORDER BY explanations.statement_date DESC,
                     explanations.source_start_seconds ASC,
                     explanations.bill_num ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def list_vote_explanation_bill_keys(
    state: str,
    *,
    year: int | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    clauses = ["state = ?", "review_status IN ('publishable', 'curated')"]
    params: list[Any] = [state]
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    params.append(max(1, min(int(limit), 200)))
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT year, special_session_key, special_session_value, bill_num,
                   COUNT(*) AS explanation_count,
                   MAX(statement_date) AS latest_statement_date,
                   MAX(updated_at) AS latest_explanation_at
            FROM bill_vote_explanations
            WHERE {' AND '.join(clauses)}
            GROUP BY year, special_session_key, special_session_value, bill_num
            ORDER BY latest_statement_date DESC, year DESC, bill_num ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_vote_explanation_overview(state: str) -> dict[str, Any]:
    with connect() as connection:
        media = connection.execute(
            """
            SELECT COUNT(*) AS media_total,
                   SUM(CASE WHEN transcript_status = 'available' THEN 1 ELSE 0 END) AS media_transcribed,
                   SUM(CASE WHEN explanation_scan_status = 'complete' THEN 1 ELSE 0 END) AS media_scanned,
                   SUM(CASE WHEN transcript_status IN ('pending', 'transcribing') THEN 1 ELSE 0 END) AS transcription_backlog,
                   SUM(CASE WHEN transcript_status = 'available' AND explanation_scan_status IN ('pending', 'scanning') THEN 1 ELSE 0 END) AS reasoning_backlog,
                   MAX(explanation_scanned_at) AS last_scanned_at
            FROM legislative_media WHERE state = ?
            """,
            (state,),
        ).fetchone()
        scans = connection.execute(
            """
            SELECT COUNT(*) AS bills_tracked,
                   SUM(CASE WHEN scan_status = 'complete' THEN 1 ELSE 0 END) AS bills_scanned
            FROM bill_vote_explanation_scans WHERE state = ?
            """,
            (state,),
        ).fetchone()
        explanations = connection.execute(
            """
            SELECT COUNT(*) AS explanation_count, MAX(source_synced_at) AS latest_explanation_at
            FROM bill_vote_explanations
            WHERE state = ? AND review_status IN ('publishable', 'curated')
            """,
            (state,),
        ).fetchone()
        years = connection.execute(
            "SELECT DISTINCT year FROM bills WHERE state = ? ORDER BY year DESC",
            (state,),
        ).fetchall()
    values = {
        **dict(media or {}),
        **dict(scans or {}),
        **dict(explanations or {}),
        "available_years": [int(row["year"]) for row in years],
    }
    timestamps = [
        str(value)
        for value in (values.get("last_scanned_at"), values.get("latest_explanation_at"))
        if value
    ]
    values["last_scanned_at"] = max(timestamps) if timestamps else None
    return values


def list_bill_roll_call_targets(state: str, years: Sequence[int]) -> list[dict[str, Any]]:
    if not years:
        return []
    params: list[Any] = [state, *(int(year) for year in years)]
    with connect() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT year, special_session_key, special_session_value, bill_num, chamber,
                            SUBSTR(vote_date, 1, 10) AS session_date
            FROM bill_roll_calls
            WHERE state = ? AND year IN ({', '.join('?' for _ in years)}) AND vote_date IS NOT NULL
            ORDER BY year DESC, session_date DESC, bill_num ASC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def count_bill_vote_explanations(
    state: str,
    year: int,
    bill_num: str,
    *,
    special_session_value: int | None = None,
) -> int:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total FROM bill_vote_explanations
            WHERE state = ? AND year = ? AND bill_num = ? AND special_session_key = ?
              AND review_status IN ('publishable', 'curated')
            """,
            (state, year, bill_num, normalize_special_session(special_session_value)),
        ).fetchone()
    return int(row["total"] or 0) if row is not None else 0


def _legislator_vote_source_marker(
    connection: sqlite3.Connection | PostgresConnection,
    state: str,
) -> str:
    row = connection.execute(
        """
        SELECT COUNT(*) AS total, MAX(updated_at) AS latest_updated_at
        FROM bill_roll_call_votes
        WHERE state = ?
        """,
        (state,),
    ).fetchone()
    values = dict(row) if row is not None else {}
    return f"{int(values.get('total') or 0)}:{str(values.get('latest_updated_at') or '')}"


def _single_surname(value: object) -> str | None:
    name = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z][A-Za-z'-]*", name) is None:
        return None
    return name.casefold()


def _last_name(value: object) -> str:
    parts = str(value or "").strip().split()
    return parts[-1].strip(".,").casefold() if parts else ""


def refresh_legislator_vote_summaries(state: str) -> None:
    refreshed_at = iso_now()
    with connect() as connection:
        if isinstance(connection, PostgresConnection):
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(?))",
                (f"legislator-vote-summary:{state}",),
            )
        source_marker = _legislator_vote_source_marker(connection, state)
        identity_rows = connection.execute(
            """
            SELECT member_key,
                   MAX(source_legislator_id) AS source_legislator_id,
                   MAX(legislator_name) AS legislator_name
            FROM bill_roll_call_votes
            WHERE state = ?
            GROUP BY member_key
            """,
            (state,),
        ).fetchall()
        identities = [dict(row) for row in identity_rows]

        canonical_by_surname: dict[str, set[str]] = {}
        for row in identities:
            if not row.get("source_legislator_id"):
                continue
            surname = _last_name(row.get("legislator_name"))
            if surname:
                canonical_by_surname.setdefault(surname, set()).add(str(row["member_key"]))

        aliases: list[dict[str, str]] = []
        for row in identities:
            if row.get("source_legislator_id"):
                continue
            surname = _single_surname(row.get("legislator_name"))
            candidates = canonical_by_surname.get(surname or "", set())
            if surname and len(candidates) == 1:
                aliases.append(
                    {
                        "state": state,
                        "alias_member_key": str(row["member_key"]),
                        "canonical_member_key": next(iter(candidates)),
                        "resolution_method": "unique_surname",
                        "updated_at": refreshed_at,
                    }
                )

        connection.execute("DELETE FROM legislator_member_aliases WHERE state = ?", (state,))
        if aliases:
            connection.executemany(
                """
                INSERT INTO legislator_member_aliases (
                    state, alias_member_key, canonical_member_key, resolution_method, updated_at
                ) VALUES (
                    :state, :alias_member_key, :canonical_member_key, :resolution_method, :updated_at
                )
                """,
                aliases,
            )

        summary_rows = connection.execute(
            """
            SELECT
                COALESCE(aliases.canonical_member_key, votes.member_key) AS member_key,
                votes.year,
                COALESCE(
                    MAX(CASE WHEN aliases.alias_member_key IS NULL THEN votes.source_legislator_id END),
                    MAX(votes.source_legislator_id)
                ) AS source_legislator_id,
                COALESCE(
                    MAX(CASE WHEN aliases.alias_member_key IS NULL THEN votes.legislator_name END),
                    MAX(votes.legislator_name)
                ) AS legislator_name,
                COALESCE(
                    MAX(CASE WHEN aliases.alias_member_key IS NULL THEN votes.party END),
                    MAX(votes.party)
                ) AS party,
                COALESCE(
                    MAX(CASE WHEN aliases.alias_member_key IS NULL THEN votes.district END),
                    MAX(votes.district)
                ) AS district,
                COALESCE(
                    MAX(CASE WHEN aliases.alias_member_key IS NULL THEN votes.chamber END),
                    MAX(votes.chamber)
                ) AS chamber,
                COUNT(*) AS total_votes,
                COUNT(DISTINCT CAST(votes.year AS TEXT) || ':' || votes.bill_num || ':' || CAST(votes.special_session_key AS TEXT)) AS bills_voted,
                SUM(CASE WHEN votes.vote_position = 'yes' THEN 1 ELSE 0 END) AS yes_count,
                SUM(CASE WHEN votes.vote_position = 'no' THEN 1 ELSE 0 END) AS no_count,
                SUM(CASE WHEN votes.vote_position = 'absent' THEN 1 ELSE 0 END) AS absent_count,
                SUM(CASE WHEN votes.vote_position = 'conflict' THEN 1 ELSE 0 END) AS conflict_count,
                SUM(CASE WHEN votes.vote_position = 'excused' THEN 1 ELSE 0 END) AS excused_count
            FROM bill_roll_call_votes AS votes
            LEFT JOIN legislator_member_aliases AS aliases
              ON aliases.state = votes.state
             AND aliases.alias_member_key = votes.member_key
            WHERE votes.state = ?
            GROUP BY COALESCE(aliases.canonical_member_key, votes.member_key), votes.year
            """,
            (state,),
        ).fetchall()

        connection.execute("DELETE FROM legislator_vote_summary_cache WHERE state = ?", (state,))
        if summary_rows:
            connection.executemany(
                """
                INSERT INTO legislator_vote_summary_cache (
                    state, member_key, year, source_legislator_id, legislator_name,
                    party, district, chamber, total_votes, bills_voted, yes_count,
                    no_count, absent_count, conflict_count, excused_count, updated_at
                ) VALUES (
                    :state, :member_key, :year, :source_legislator_id, :legislator_name,
                    :party, :district, :chamber, :total_votes, :bills_voted, :yes_count,
                    :no_count, :absent_count, :conflict_count, :excused_count, :updated_at
                )
                """,
                [
                    {
                        **dict(row),
                        "state": state,
                        "updated_at": refreshed_at,
                    }
                    for row in summary_rows
                ],
            )
        connection.execute(
            """
            INSERT INTO legislator_vote_summary_status (state, source_marker, refreshed_at)
            VALUES (?, ?, ?)
            ON CONFLICT(state) DO UPDATE SET
                source_marker = excluded.source_marker,
                refreshed_at = excluded.refreshed_at
            """,
            (state, source_marker, refreshed_at),
        )
        connection.commit()


def _ensure_legislator_vote_summaries(state: str) -> None:
    with connect() as connection:
        source_marker = _legislator_vote_source_marker(connection, state)
        status = connection.execute(
            "SELECT source_marker FROM legislator_vote_summary_status WHERE state = ?",
            (state,),
        ).fetchone()
    status_values = dict(status) if status is not None else {}
    if str(status_values.get("source_marker") or "") != source_marker:
        refresh_legislator_vote_summaries(state)


def _canonical_legislator_member_key(state: str, member_key: str) -> str:
    _ensure_legislator_vote_summaries(state)
    with connect() as connection:
        row = connection.execute(
            """
            SELECT canonical_member_key
            FROM legislator_member_aliases
            WHERE state = ? AND alias_member_key = ?
            """,
            (state, member_key),
        ).fetchone()
    return str(row["canonical_member_key"]) if row is not None else member_key


def list_legislator_vote_summaries(
    state: str,
    *,
    query: str = "",
    year: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    _ensure_legislator_vote_summaries(state)
    clauses = ["state = ?"]
    params: list[Any] = [state]
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    normalized_query = str(query or "").strip().casefold()
    if normalized_query:
        clauses.append("(LOWER(legislator_name) LIKE ? OR LOWER(COALESCE(district, '')) LIKE ?)")
        search_value = f"%{normalized_query}%"
        params.extend([search_value, search_value])

    safe_limit = max(1, min(int(limit), 250))
    params.append(safe_limit)
    sql = f"""
        SELECT
            member_key,
            MAX(source_legislator_id) AS source_legislator_id,
            MAX(legislator_name) AS legislator_name,
            MAX(party) AS party,
            MAX(district) AS district,
            MAX(chamber) AS chamber,
            MAX(year) AS latest_year,
            SUM(total_votes) AS total_votes,
            SUM(bills_voted) AS bills_voted,
            SUM(yes_count) AS yes_count,
            SUM(no_count) AS no_count,
            SUM(absent_count) AS absent_count,
            SUM(conflict_count) AS conflict_count,
            SUM(excused_count) AS excused_count
        FROM legislator_vote_summary_cache
        WHERE {' AND '.join(clauses)}
        GROUP BY member_key
        ORDER BY legislator_name ASC
        LIMIT ?
    """
    with connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_legislator_voting_record(
    state: str,
    member_key: str,
    *,
    year: int | None = None,
    latest_year_only: bool = False,
    limit: int = 200,
) -> dict[str, Any] | None:
    canonical_member_key = _canonical_legislator_member_key(state, member_key)
    member_params = (state, canonical_member_key, state, canonical_member_key)
    with connect() as connection:
        year_rows = connection.execute(
            """
            SELECT DISTINCT votes.year
            FROM bill_roll_call_votes AS votes
            WHERE votes.state = ?
              AND (
                  votes.member_key = ?
                  OR votes.member_key IN (
                      SELECT alias_member_key
                      FROM legislator_member_aliases
                      WHERE state = ? AND canonical_member_key = ?
                  )
              )
            ORDER BY votes.year DESC
            """,
            member_params,
        ).fetchall()
        available_years = [int(row["year"]) for row in year_rows]
        if not available_years:
            return None

        selected_year = year
        if latest_year_only and selected_year is None:
            selected_year = available_years[0]
        if selected_year is not None and selected_year not in available_years:
            return None

        year_clause = " AND votes.year = ?" if selected_year is not None else ""
        vote_params = (*member_params, selected_year) if selected_year is not None else member_params
        rows = connection.execute(
            f"""
            SELECT
                votes.member_key,
                votes.source_legislator_id,
                votes.legislator_name,
                votes.party,
                votes.district,
                votes.chamber,
                votes.vote_position,
                votes.year,
                votes.special_session_value,
                votes.bill_num,
                votes.roll_call_key,
                votes.vote_id,
                roll_calls.vote_date,
                roll_calls.vote_type,
                roll_calls.action,
                roll_calls.amendment_number,
                roll_calls.yes_count,
                roll_calls.no_count,
                roll_calls.absent_count,
                roll_calls.conflict_count,
                roll_calls.excused_count,
                bills.catch_title,
                bills.bill_title,
                bills.outcome,
                bills.status_label
            FROM bill_roll_call_votes AS votes
            JOIN bill_roll_calls AS roll_calls
              ON roll_calls.state = votes.state
             AND roll_calls.year = votes.year
             AND roll_calls.special_session_key = votes.special_session_key
             AND roll_calls.bill_num = votes.bill_num
             AND roll_calls.roll_call_key = votes.roll_call_key
            JOIN bills
              ON bills.state = votes.state
             AND bills.year = votes.year
             AND bills.special_session_key = votes.special_session_key
             AND bills.bill_num = votes.bill_num
            WHERE votes.state = ?
              AND (
                  votes.member_key = ?
                  OR votes.member_key IN (
                      SELECT alias_member_key
                      FROM legislator_member_aliases
                      WHERE state = ? AND canonical_member_key = ?
                  )
              )
              {year_clause}
            ORDER BY roll_calls.vote_date DESC, votes.year DESC, votes.bill_num ASC
            """,
            vote_params,
        ).fetchall()

    selected_votes = [dict(row) for row in rows]
    if not selected_votes:
        return None

    positions = ("yes", "no", "absent", "conflict", "excused")
    counts = {position: 0 for position in positions}
    counts["other"] = 0
    year_counts: dict[int, dict[str, int]] = {}
    for vote in selected_votes:
        position = str(vote.get("vote_position") or "other")
        counts[position if position in counts else "other"] += 1
        vote_year = int(vote["year"])
        summary = year_counts.setdefault(vote_year, {item: 0 for item in (*positions, "other")})
        summary[position if position in summary else "other"] += 1
    counts["total"] = len(selected_votes)

    latest = selected_votes[0]
    coverage_years = [selected_year] if selected_year is not None else available_years
    year_placeholders = ", ".join("?" for _ in coverage_years)
    with connect() as connection:
        identity_row = connection.execute(
            """
            SELECT source_legislator_id, legislator_name, party, district, chamber
            FROM legislator_vote_summary_cache
            WHERE state = ? AND member_key = ?
            ORDER BY year DESC
            LIMIT 1
            """,
            (state, canonical_member_key),
        ).fetchone()
        identity = dict(identity_row) if identity_row is not None else {}
        coverage_chamber = identity.get("chamber") or latest.get("chamber")
        coverage_row = connection.execute(
            f"""
            SELECT COUNT(*) AS unattributed_roll_calls
            FROM bill_roll_calls AS roll_calls
            LEFT JOIN (
                SELECT state, year, special_session_key, bill_num, roll_call_key, COUNT(*) AS attributed_votes
                FROM bill_roll_call_votes
                GROUP BY state, year, special_session_key, bill_num, roll_call_key
            ) AS vote_counts
              ON vote_counts.state = roll_calls.state
             AND vote_counts.year = roll_calls.year
             AND vote_counts.special_session_key = roll_calls.special_session_key
             AND vote_counts.bill_num = roll_calls.bill_num
             AND vote_counts.roll_call_key = roll_calls.roll_call_key
            WHERE roll_calls.state = ?
              AND roll_calls.chamber = ?
              AND roll_calls.year IN ({year_placeholders})
              AND (
                  roll_calls.yes_count + roll_calls.no_count + roll_calls.absent_count
                  + roll_calls.conflict_count + roll_calls.excused_count
              ) > COALESCE(vote_counts.attributed_votes, 0)
            """,
            (state, coverage_chamber, *coverage_years),
        ).fetchone()
    return {
        "legislator": {
            "member_key": canonical_member_key,
            "source_legislator_id": identity.get("source_legislator_id") or latest.get("source_legislator_id"),
            "name": identity.get("legislator_name") or latest.get("legislator_name"),
            "party": identity.get("party") or latest.get("party"),
            "district": identity.get("district") or latest.get("district"),
            "chamber": coverage_chamber,
        },
        "available_years": available_years,
        "selected_year": selected_year,
        "counts": counts,
        "coverage": {
            "unattributed_roll_calls": int(coverage_row["unattributed_roll_calls"] or 0) if coverage_row else 0,
        },
        "year_breakdown": [
            {"year": vote_year, **year_counts[vote_year], "total": sum(year_counts[vote_year].values())}
            for vote_year in sorted(year_counts, reverse=True)
        ],
        "votes": selected_votes[: max(1, min(int(limit), 500))],
    }


def replace_bill_relationships(state: str, year: int, payloads: list[dict[str, Any]]) -> None:
    columns = [
        "state",
        "year",
        "special_session_key_a",
        "special_session_value_a",
        "bill_num_a",
        "special_session_key_b",
        "special_session_value_b",
        "bill_num_b",
        "relationship_type",
        "relationship_strength",
        "confidence_score",
        "candidate_score",
        "needs_human_review",
        "pair_summary",
        "combined_effect",
        "why_review",
        "bill_a_evidence_json",
        "bill_b_evidence_json",
        "limits_and_unknowns_json",
        "heuristic_reasons_json",
        "analysis_version",
        "source_synced_at",
        "created_at",
        "updated_at",
    ]

    serializable_rows: list[dict[str, Any]] = []
    for payload in payloads:
        item = dict(payload)
        item["special_session_key_a"] = normalize_special_session(item.get("special_session_value_a"))
        item["special_session_key_b"] = normalize_special_session(item.get("special_session_value_b"))
        item["needs_human_review"] = 1 if item.get("needs_human_review") else 0
        for column in RELATIONSHIP_JSON_COLUMNS:
            value = item.get(column)
            item[column] = json.dumps(value) if value is not None else None
        serializable_rows.append(item)

    placeholders = ", ".join(f":{column}" for column in columns)
    with connect() as connection:
        connection.execute("DELETE FROM bill_relationships WHERE state = ? AND year = ?", (state, year))
        if serializable_rows:
            connection.executemany(
                f"""
                INSERT INTO bill_relationships ({', '.join(columns)})
                VALUES ({placeholders})
                """,
                serializable_rows,
            )
        connection.commit()


def list_bill_relationships(state: str, year: int, limit: int = 8) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                r.*,
                a.catch_title AS bill_a_catch_title,
                a.outcome AS bill_a_outcome,
                a.status_label AS bill_a_status_label,
                b.catch_title AS bill_b_catch_title,
                b.outcome AS bill_b_outcome,
                b.status_label AS bill_b_status_label
            FROM bill_relationships r
            JOIN bills a
              ON a.state = r.state
             AND a.year = r.year
             AND a.bill_num = r.bill_num_a
             AND a.special_session_key = r.special_session_key_a
            JOIN bills b
              ON b.state = r.state
             AND b.year = r.year
             AND b.bill_num = r.bill_num_b
             AND b.special_session_key = r.special_session_key_b
            WHERE r.state = ? AND r.year = ?
            ORDER BY
                CASE r.relationship_strength
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 2
                    ELSE 1
                END DESC,
                r.confidence_score DESC,
                r.bill_num_a ASC,
                r.bill_num_b ASC
            LIMIT ?
            """,
            (state, year, limit),
        ).fetchall()
    return [_parse_relationship_row(row) for row in rows if row is not None]


def get_bill_relationships_for_bill(
    state: str,
    year: int,
    bill_num: str,
    special_session_value: int | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    special_session_key = normalize_special_session(special_session_value)
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT
                r.*,
                a.catch_title AS bill_a_catch_title,
                a.outcome AS bill_a_outcome,
                a.status_label AS bill_a_status_label,
                b.catch_title AS bill_b_catch_title,
                b.outcome AS bill_b_outcome,
                b.status_label AS bill_b_status_label
            FROM bill_relationships r
            JOIN bills a
              ON a.state = r.state
             AND a.year = r.year
             AND a.bill_num = r.bill_num_a
             AND a.special_session_key = r.special_session_key_a
            JOIN bills b
              ON b.state = r.state
             AND b.year = r.year
             AND b.bill_num = r.bill_num_b
             AND b.special_session_key = r.special_session_key_b
            WHERE r.state = ? AND r.year = ?
              AND (
                    (r.bill_num_a = ? AND r.special_session_key_a = ?)
                 OR (r.bill_num_b = ? AND r.special_session_key_b = ?)
              )
            ORDER BY
                CASE r.relationship_strength
                    WHEN 'high' THEN 3
                    WHEN 'medium' THEN 2
                    ELSE 1
                END DESC,
                r.confidence_score DESC,
                r.bill_num_a ASC,
                r.bill_num_b ASC
            LIMIT ?
            """,
            (state, year, bill_num, special_session_key, bill_num, special_session_key, limit),
        ).fetchall()
    return [_parse_relationship_row(row) for row in rows if row is not None]


def _query_bills(
    *,
    state: str | None = None,
    year: int | None = None,
    status: str = "all",
    query: str = "",
    tag: str = "",
    include_search_blob: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if state:
        clauses.append("state = ?")
        params.append(state)
    if year is not None:
        clauses.append("year = ?")
        params.append(year)
    if status and status != "all":
        if status == "failed":
            clauses.append("outcome IN ('failed', 'replaced')")
        else:
            clauses.append("outcome = ?")
            params.append(status)
    with connect() as connection:
        tokens = _search_tokens(query)
        if tokens and isinstance(connection, PostgresConnection):
            full_text_query = " OR ".join(f'"{token}"' for token in tokens[:6])
            clauses.append(
                f"{POSTGRES_BILL_SEARCH_VECTOR_SQL} @@ websearch_to_tsquery('simple', ?)"
            )
            params.append(full_text_query)
            order_sql = "year DESC, COALESCE(last_action_date, '') DESC, bill_num ASC"
        elif tokens:
            token_clauses = []
            searchable_columns = [
                "bill_num",
                "catch_title",
                "bill_title",
                "sponsor",
                "status_label",
                "status_explainer",
                "bill_tags_json",
                "interpretation_json",
            ]
            for token in tokens[:6]:
                for column in searchable_columns:
                    token_clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                    params.append(f"%{token}%")
            clauses.append(f"({' OR '.join(token_clauses)})")
            order_sql = ""
        else:
            order_sql = "year DESC, COALESCE(last_action_date, '') DESC, bill_num ASC"

        normalized_tag = str(tag or "").strip().lower()
        if normalized_tag:
            clauses.append("LOWER(COALESCE(bill_tags_json, '')) LIKE ?")
            params.append(f'%"{normalized_tag}"%')

        columns = BILL_SEARCH_COLUMNS if include_search_blob and not tokens else BILL_LIST_COLUMNS
        sql = f"SELECT {', '.join(columns)} FROM bills"
        if clauses:
            sql += f" WHERE {' AND '.join(clauses)}"
        if order_sql:
            sql += f" ORDER BY {order_sql}"
        if limit is not None:
            candidate_limit = max(int(limit), 1)
            if tokens:
                candidate_limit = min(max(candidate_limit * 5, 100), 500)
            sql += " LIMIT ?"
            params.append(candidate_limit)
        rows = connection.execute(sql, params).fetchall()
    return [_parse_row(row) for row in rows if row is not None]


def _filter_bill_results(
    bills: list[dict[str, Any]],
    *,
    query: str = "",
    tag: str = "",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    normalized_tag = str(tag or "").strip().lower()
    filtered: list[tuple[int, dict[str, Any]]] = []
    for bill in bills:
        tags = [str(item or "").strip().lower() for item in bill.get("bill_tags_json") or []]
        if normalized_tag and normalized_tag not in tags:
            continue
        score = _search_score(bill, query)
        if query.strip() and score is None:
            continue
        filtered.append((score or 0, bill))

    rows = [bill for _, bill in filtered]
    if query.strip():
        score_map = {id(bill): score for score, bill in filtered}
        rows.sort(key=lambda bill: str(bill.get("bill_num") or ""))
        rows.sort(key=lambda bill: str(bill.get("last_action_date") or ""), reverse=True)
        rows.sort(key=lambda bill: score_map.get(id(bill), 0), reverse=True)
    else:
        rows.sort(key=lambda bill: str(bill.get("bill_num") or ""))
        rows.sort(key=lambda bill: str(bill.get("last_action_date") or ""), reverse=True)
    if limit is not None:
        return rows[:limit]
    return rows


def _search_score(bill: dict[str, Any], query: str) -> int | None:
    normalized_query = " ".join(_search_tokens(query)).strip()
    if not normalized_query:
        return 0

    bill_num = str(bill.get("bill_num") or "").strip().lower()
    catch_title = str(bill.get("catch_title") or "").strip().lower()
    bill_title = str(bill.get("bill_title") or "").strip().lower()
    sponsor = str(bill.get("sponsor") or "").strip().lower()
    summary = ""
    interpretation = bill.get("interpretation_json")
    if isinstance(interpretation, dict):
        summary = str(interpretation.get("one_sentence_summary") or "").strip().lower()
    tags_text = " ".join(str(item or "").strip().lower() for item in bill.get("bill_tags_json") or [])
    haystack = str(bill.get("search_blob") or "").strip().lower()

    score = 0
    matched_any = False
    if bill_num == normalized_query:
        score += 140
        matched_any = True
    elif bill_num.startswith(normalized_query):
        score += 110
        matched_any = True
    if normalized_query in tags_text:
        score += 80
        matched_any = True
    if normalized_query in catch_title:
        score += 75
        matched_any = True
    if normalized_query in bill_title:
        score += 55
        matched_any = True
    if normalized_query in sponsor:
        score += 65
        matched_any = True
    if normalized_query in summary:
        score += 45
        matched_any = True

    for token in _search_tokens(query):
        if token in bill_num:
            score += 28
            matched_any = True
        elif token in tags_text:
            score += 24
            matched_any = True
        elif token in catch_title:
            score += 22
            matched_any = True
        elif token in bill_title:
            score += 18
            matched_any = True
        elif token in sponsor:
            score += 16
            matched_any = True
        elif token in haystack:
            score += 8
            matched_any = True
    if not matched_any:
        return None
    return score


def _search_tokens(query: str) -> list[str]:
    return [
        token
        for token in SEARCH_TOKEN_PATTERN.findall(str(query or "").lower())
        if token and (len(token) > 1 or any(character.isdigit() for character in token))
    ]


def record_page_view(payload: dict[str, Any]) -> None:
    columns = [
        "occurred_at",
        "created_at",
        "host",
        "path",
        "route_label",
        "method",
        "status_code",
        "referrer_domain",
        "country_code",
        "country_name",
        "region_code",
        "region_name",
        "city_name",
        "latitude",
        "longitude",
        "visitor_hash",
        "is_bot",
        "user_agent",
    ]
    serializable = {
        "occurred_at": str(payload.get("occurred_at") or ""),
        "created_at": str(payload.get("created_at") or payload.get("occurred_at") or ""),
        "host": str(payload.get("host") or ""),
        "path": str(payload.get("path") or ""),
        "route_label": str(payload.get("route_label") or "other"),
        "method": str(payload.get("method") or "GET"),
        "status_code": int(payload.get("status_code") or 0),
        "referrer_domain": str(payload.get("referrer_domain") or "") or None,
        "country_code": str(payload.get("country_code") or "") or None,
        "country_name": str(payload.get("country_name") or "") or None,
        "region_code": str(payload.get("region_code") or "") or None,
        "region_name": str(payload.get("region_name") or "") or None,
        "city_name": str(payload.get("city_name") or "") or None,
        "latitude": float(payload["latitude"]) if payload.get("latitude") is not None else None,
        "longitude": float(payload["longitude"]) if payload.get("longitude") is not None else None,
        "visitor_hash": str(payload.get("visitor_hash") or "") or None,
        "is_bot": 1 if payload.get("is_bot") else 0,
        "user_agent": str(payload.get("user_agent") or "")[:300] or None,
    }
    placeholders = ", ".join(f":{column}" for column in columns)
    with connect() as connection:
        connection.execute(
            f"INSERT INTO page_views ({', '.join(columns)}) VALUES ({placeholders})",
            serializable,
        )
        connection.commit()


def cleanup_page_views(retention_cutoff: str) -> int:
    with connect() as connection:
        cursor = connection.execute("DELETE FROM page_views WHERE occurred_at < ?", (retention_cutoff,))
        connection.commit()
    return int(cursor.rowcount or 0)


def get_analytics_overview(
    *,
    internal_hosts: tuple[str, ...],
    since_24h: str,
    since_7d: str,
    since_30d: str,
) -> dict[str, Any]:
    windows = {
        "24h": since_24h,
        "7d": since_7d,
        "30d": since_30d,
    }
    summary: dict[str, Any] = {"windows": {}}
    normalized_internal_hosts = tuple(dict.fromkeys(host.strip().lower() for host in internal_hosts if host and host.strip()))
    with connect() as connection:
        for label, cutoff in windows.items():
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total_views,
                    SUM(CASE WHEN is_bot = 0 THEN 1 ELSE 0 END) AS human_views,
                    SUM(CASE WHEN is_bot = 1 THEN 1 ELSE 0 END) AS bot_views,
                    COUNT(DISTINCT CASE WHEN is_bot = 0 THEN visitor_hash END) AS human_visitors
                FROM page_views
                WHERE occurred_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            summary["windows"][label] = {
                "total_views": int(row["total_views"] or 0),
                "human_views": int(row["human_views"] or 0),
                "bot_views": int(row["bot_views"] or 0),
                "human_visitors": int(row["human_visitors"] or 0),
            }

        top_countries = connection.execute(
            """
            SELECT
                COALESCE(NULLIF(country_name, ''), 'Unknown') AS country_name,
                COALESCE(NULLIF(country_code, ''), '--') AS country_code,
                COUNT(*) AS hits
            FROM page_views
            WHERE occurred_at >= ? AND is_bot = 0
            GROUP BY country_name, country_code
            ORDER BY hits DESC, country_name ASC
            LIMIT 12
            """,
            (since_30d,),
        ).fetchall()
        summary["top_countries"] = [dict(row) for row in top_countries]

        top_pages = connection.execute(
            """
            SELECT path, COUNT(*) AS hits
            FROM page_views
            WHERE occurred_at >= ? AND is_bot = 0
            GROUP BY path
            ORDER BY hits DESC, path ASC
            LIMIT 12
            """,
            (since_30d,),
        ).fetchall()
        summary["top_pages"] = [dict(row) for row in top_pages]

        top_referrers_sql = """
            SELECT referrer_domain, COUNT(*) AS hits
            FROM page_views
            WHERE occurred_at >= ?
              AND is_bot = 0
              AND referrer_domain IS NOT NULL
              AND referrer_domain != ''
        """
        top_referrers_params: list[Any] = [since_30d]
        if normalized_internal_hosts:
            placeholders = ", ".join("?" for _ in normalized_internal_hosts)
            top_referrers_sql += f" AND referrer_domain NOT IN ({placeholders})"
            top_referrers_params.extend(normalized_internal_hosts)
        top_referrers_sql += """
            GROUP BY referrer_domain
            ORDER BY hits DESC, referrer_domain ASC
            LIMIT 12
        """
        top_referrers = connection.execute(top_referrers_sql, top_referrers_params).fetchall()
        summary["top_referrers"] = [dict(row) for row in top_referrers]

        recent_visits = connection.execute(
            """
            SELECT occurred_at, path, country_name, referrer_domain
            FROM page_views
            WHERE occurred_at >= ? AND is_bot = 0
            ORDER BY occurred_at DESC
            LIMIT 20
            """,
            (since_7d,),
        ).fetchall()
        summary["recent_visits"] = [dict(row) for row in recent_visits]

    return summary
