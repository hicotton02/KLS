"""Short-lived official detail checkpoints shared by Alaska source workers."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from app.db import connect


def catalog_signature(item: dict) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()


def ensure_source_cache() -> None:
    # Owned by this adapter, not the shared schema revision used by older workers.
    with connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS alaska_source_checkpoints (
            detail_url TEXT PRIMARY KEY, catalog_hash TEXT NOT NULL,
            detail_json TEXT NOT NULL, checked_at TEXT NOT NULL, expires_at TEXT NOT NULL
        )""")
        c.commit()


def cached_detail(url: str, signature: str, *, now: datetime | None = None) -> dict | None:
    with connect() as c:
        row = c.execute("""SELECT detail_json FROM alaska_source_checkpoints
            WHERE detail_url=? AND catalog_hash=? AND expires_at>?""",
            (url, signature, (now or datetime.now(timezone.utc)).isoformat())).fetchone()
    return json.loads(row['detail_json']) if row else None


def save_detail(url: str, signature: str, detail: dict, *, now: datetime | None = None) -> None:
    checked = now or datetime.now(timezone.utc)
    with connect() as c:
        c.execute("""INSERT INTO alaska_source_checkpoints
            (detail_url,catalog_hash,detail_json,checked_at,expires_at) VALUES (?,?,?,?,?)
            ON CONFLICT(detail_url) DO UPDATE SET catalog_hash=excluded.catalog_hash,
              detail_json=excluded.detail_json,checked_at=excluded.checked_at,expires_at=excluded.expires_at""",
            (url, signature, json.dumps(detail), checked.isoformat(),
             (checked + timedelta(hours=6)).isoformat()))
        c.commit()
