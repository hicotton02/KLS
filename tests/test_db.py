import app.db as db
from app.db import (
    POSTGRES_SCHEMA_METADATA_NAME,
    StaticCursor,
    _connect_postgres,
    _initialize_postgres_schema,
    _schema_revision,
    _sanitize_db_value,
    connect,
    get_jurisdiction_rollups,
    get_sync_status,
    init_db,
    list_legislator_vote_summaries,
    list_recent_bills,
    replace_bill_roll_calls,
    reset_stale_sync_statuses,
    update_sync_status,
)


class FakePostgresConnection:
    def __init__(self, revision: str | None) -> None:
        self.revision = revision
        self.statements: list[str] = []
        self.scripts: list[str] = []

    def execute(self, sql: str, params: object = None) -> StaticCursor:
        self.statements.append(sql)
        if "SELECT revision FROM kls_schema_metadata" in sql:
            rows = [{"revision": self.revision}] if self.revision else []
            return StaticCursor(rows)
        return StaticCursor([])

    def executescript(self, script: str) -> None:
        self.scripts.append(script)


def test_postgres_connect_retries_transient_operational_errors(monkeypatch) -> None:
    class OperationalError(Exception):
        pass

    class FakePsycopg:
        def __init__(self) -> None:
            self.calls = 0

        def connect(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls < 3:
                raise OperationalError("temporary DNS failure")
            return "connected"

    FakePsycopg.OperationalError = OperationalError
    fake_psycopg = FakePsycopg()
    monkeypatch.setattr(db, "psycopg", fake_psycopg)
    monkeypatch.setattr(db, "dict_row", object())
    sleeps: list[float] = []

    connection = _connect_postgres("postgresql://example", max_attempts=3, sleeper=sleeps.append)

    assert connection == "connected"
    assert fake_psycopg.calls == 3
    assert sleeps == [0.5, 1.0]


def test_postgres_schema_gate_skips_repeated_ddl() -> None:
    connection = FakePostgresConnection(_schema_revision())

    changed = _initialize_postgres_schema(connection)  # type: ignore[arg-type]

    assert changed is False
    assert connection.scripts == []


def test_postgres_schema_gate_records_new_revision() -> None:
    connection = FakePostgresConnection("old-revision")

    changed = _initialize_postgres_schema(connection)  # type: ignore[arg-type]

    assert changed is True
    assert len(connection.scripts) == 1
    assert any("INSERT INTO kls_schema_metadata" in statement for statement in connection.statements)
    assert POSTGRES_SCHEMA_METADATA_NAME == "main"


def test_init_db_adds_bot_reason_before_creating_its_index() -> None:
    with connect() as connection:
        connection.execute("DROP TABLE page_views")
        connection.execute(
            """
            CREATE TABLE page_views (
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
            )
            """
        )
        connection.commit()

    init_db()

    with connect() as connection:
        columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(page_views)").fetchall()}
        indexes = {
            str(row["name"])
            for row in connection.execute("PRAGMA index_list(page_views)").fetchall()
        }
    assert "bot_reason" in columns
    assert "idx_page_views_bot_reason" in indexes


def test_legislator_summary_serves_completed_cache_while_sync_is_running() -> None:
    init_db()
    timestamp = "2026-08-24T12:00:00+00:00"

    def add_vote(bill_num: str, vote_id: str) -> None:
        replace_bill_roll_calls(
            "wy",
            2026,
            bill_num,
            payloads=[
                {
                    "roll_call_key": f"h-{vote_id}",
                    "vote_id": vote_id,
                    "chamber": "H",
                    "vote_date": timestamp,
                    "vote_type": "F",
                    "action": "H 3rd Reading:Passed",
                    "amendment_number": None,
                    "yes_count": 1,
                    "no_count": 0,
                    "absent_count": 0,
                    "conflict_count": 0,
                    "excused_count": 0,
                    "members": [
                        {
                            "member_key": "wy-1",
                            "source_legislator_id": "1",
                            "legislator_name": "Test Member",
                            "vote_label": "Member",
                            "party": "I",
                            "district": "H01",
                            "vote_position": "yes",
                        }
                    ],
                    "source_synced_at": timestamp,
                    "created_at": timestamp,
                    "updated_at": f"{timestamp}:{vote_id}",
                }
            ],
        )

    add_vote("HB1", "1")
    assert list_legislator_vote_summaries("wy")[0]["total_votes"] == 1

    update_sync_status("wy", is_running=True)
    add_vote("HB2", "2")
    assert list_legislator_vote_summaries("wy")[0]["total_votes"] == 1

    update_sync_status("wy", is_running=False)
    assert list_legislator_vote_summaries("wy")[0]["total_votes"] == 2


def test_sanitize_db_value_removes_nul_bytes_recursively() -> None:
    value = {
        "plain": "safe",
        "bad": "north\x00dakota",
        "items": ["one\x00two", ("three\x00four",)],
    }

    assert _sanitize_db_value(value) == {
        "plain": "safe",
        "bad": "northdakota",
        "items": ["onetwo", ("threefour",)],
    }


def test_reset_stale_sync_statuses_clears_old_running_rows() -> None:
    init_db()
    update_sync_status("ma", is_running=True, current_bill_num="H860", last_message="Updated H860.")
    with connect() as connection:
        connection.execute(
            "UPDATE sync_status SET updated_at = ?, started_at = ? WHERE state = ?",
            ("2026-06-10T07:15:24+00:00", "2026-06-10T05:54:27+00:00", "ma"),
        )
        connection.commit()

    cleared = reset_stale_sync_statuses(3600)

    status = get_sync_status("ma")
    assert cleared == 1
    assert status is not None
    assert status["is_running"] is False
    assert status["current_bill_num"] == ""
    assert "Cleared stale running marker" in status["last_message"]


def test_jurisdiction_rollups_and_recent_bills_use_bounded_queries() -> None:
    init_db()
    rows = [
        ("wy", 2025, "HB1", "active", "2025-01-01T00:00:00+00:00"),
        ("wy", 2026, "HB2", "active", "2026-07-01T00:00:00+00:00"),
        ("wy", 2026, "HB3", "passed", "2026-07-02T00:00:00+00:00"),
        ("co", 2026, "SB4", "failed", "2026-06-30T00:00:00+00:00"),
    ]
    with connect() as connection:
        connection.executemany(
            """
            INSERT INTO bills (
                state, year, special_session_key, bill_num, outcome,
                source_synced_at, created_at, updated_at
            ) VALUES (?, ?, -1, ?, ?, ?, ?, ?)
            """,
            [(*row, row[4], row[4]) for row in rows],
        )
        connection.commit()

    rollups = get_jurisdiction_rollups(["wy", "co", "missing"])
    recent = list_recent_bills(limit=2)

    assert rollups["wy"] == {
        "latest_year": 2026,
        "counts": {"total": 2, "active": 1, "passed": 1, "failed": 0},
    }
    assert rollups["co"]["counts"] == {"total": 1, "active": 0, "passed": 0, "failed": 1}
    assert "missing" not in rollups
    assert [bill["bill_num"] for bill in recent] == ["HB3", "HB2"]
