#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError as exc:  # pragma: no cover - exercised only in deployment image.
    raise RuntimeError("psycopg is required for the KLS Postgres migration.") from exc

from app.db import init_db


TABLES = (
    "bills",
    "bill_amendments",
    "bill_relationships",
    "page_views",
    "sync_status",
)
SERIAL_TABLES = {
    "bills",
    "bill_amendments",
    "bill_relationships",
    "page_views",
}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote_ident(value: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return f'"{value}"'


def sqlite_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()
    return [str(row["name"]) for row in rows]


def clean_postgres_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    return value


def postgres_columns(connection: Any, table: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def copy_table(
    sqlite_connection: sqlite3.Connection,
    postgres_connection: Any,
    table: str,
    *,
    batch_size: int,
) -> int:
    source_columns = sqlite_columns(sqlite_connection, table)
    destination_columns = set(postgres_columns(postgres_connection, table))
    columns = [column for column in source_columns if column in destination_columns]
    if not columns:
        raise RuntimeError(f"No overlapping columns found for table {table}")

    quoted_table = quote_ident(table)
    quoted_columns = ", ".join(quote_ident(column) for column in columns)
    source_sql = f"SELECT {quoted_columns} FROM {quoted_table}"
    count = 0

    source_cursor = sqlite_connection.execute(source_sql)
    with postgres_connection.cursor().copy(f"COPY {quoted_table} ({quoted_columns}) FROM STDIN") as copy:
        while True:
            rows = source_cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                copy.write_row([clean_postgres_value(row[column]) for column in columns])
            count += len(rows)
    return count


def reset_sequence(connection: Any, table: str) -> None:
    quoted_table = quote_ident(table)
    connection.execute(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', 'id'),
            GREATEST(COALESCE((SELECT MAX(id) FROM {quoted_table}), 0), 1),
            COALESCE((SELECT MAX(id) FROM {quoted_table}), 0) > 0
        )
        """
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Keeping Law Simple from SQLite to Postgres.")
    parser.add_argument(
        "--sqlite-path",
        default=os.environ.get("KLS_SQLITE_SOURCE_PATH") or os.environ.get("KLS_DATABASE_PATH") or "/source/keepinglawsimple.db",
    )
    parser.add_argument("--database-url", default=os.environ.get("KLS_DATABASE_URL", ""))
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("KLS_MIGRATION_BATCH_SIZE", "5000")))
    parser.add_argument(
        "--truncate",
        action=argparse.BooleanOptionalAction,
        default=(os.environ.get("KLS_MIGRATION_TRUNCATE", "true").strip().lower() not in {"0", "false", "no", "off"}),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sqlite_path = Path(args.sqlite_path)
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database does not exist: {sqlite_path}")
    if not args.database_url:
        raise RuntimeError("KLS_DATABASE_URL is required.")

    os.environ["KLS_DATABASE_URL"] = args.database_url
    init_db()

    sqlite_connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro&immutable=1", uri=True)
    sqlite_connection.row_factory = sqlite3.Row
    try:
        with psycopg.connect(args.database_url) as postgres_connection:
            if args.truncate:
                postgres_connection.execute(
                    "TRUNCATE bill_relationships, bill_amendments, bills, page_views, sync_status RESTART IDENTITY"
                )
            for table in TABLES:
                copied = copy_table(sqlite_connection, postgres_connection, table, batch_size=max(100, args.batch_size))
                print(f"{table}: copied {copied}", flush=True)
            for table in SERIAL_TABLES:
                reset_sequence(postgres_connection, table)
    finally:
        sqlite_connection.close()

    print("KLS SQLite to Postgres migration complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
