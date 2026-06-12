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
SYNC_STATUS_JSON_COLUMNS = {"years_json"}
SYNC_STATUS_COLUMN_DEFINITIONS = {
    "source_total": "INTEGER",
    "stored_total": "INTEGER",
}
SEARCH_TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9'-]*")
BILL_COLUMN_DEFINITIONS = {
    "bill_tags_json": "TEXT",
    "search_blob": "TEXT",
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
        _ensure_page_view_columns(connection)
        _ensure_sync_status_columns(connection)
        connection.commit()


def _ensure_bill_columns(connection: sqlite3.Connection) -> None:
    existing_columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(bills)").fetchall()}
    for column, definition in BILL_COLUMN_DEFINITIONS.items():
        if column not in existing_columns:
            connection.execute(f"ALTER TABLE bills ADD COLUMN {column} {definition}")


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
    bills = _query_bills(state=state, year=year, status=status, query=query, include_search_blob=bool(query.strip()))
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
    bills = _query_bills(state=state, year=year, status=status, query=query, include_search_blob=bool(query.strip()))
    return _filter_bill_results(bills, query=query, tag=tag, limit=limit)


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
    include_search_blob: bool = False,
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
    tokens = _search_tokens(query)
    if tokens:
        token_clauses: list[str] = []
        searchable_columns = [
            "bill_num",
            "catch_title",
            "bill_title",
            "sponsor",
            "status_label",
            "status_explainer",
            "search_blob",
        ]
        for token in tokens[:6]:
            pattern = f"%{token}%"
            for column in searchable_columns:
                token_clauses.append(f"LOWER(COALESCE({column}, '')) LIKE ?")
                params.append(pattern)
        clauses.append(f"({' OR '.join(token_clauses)})")

    columns = BILL_SEARCH_COLUMNS if include_search_blob else BILL_LIST_COLUMNS
    sql = f"SELECT {', '.join(columns)} FROM bills"
    if clauses:
        sql += f" WHERE {' AND '.join(clauses)}"
    sql += " ORDER BY year DESC, COALESCE(last_action_date, '') DESC, bill_num ASC"

    with connect() as connection:
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
