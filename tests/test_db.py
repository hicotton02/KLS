from app.db import (
    _sanitize_db_value,
    connect,
    get_jurisdiction_rollups,
    get_sync_status,
    init_db,
    list_recent_bills,
    reset_stale_sync_statuses,
    update_sync_status,
)


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
        "latest_refresh": "2026-07-02T00:00:00+00:00",
    }
    assert rollups["co"]["counts"] == {"total": 1, "active": 0, "passed": 0, "failed": 1}
    assert "missing" not in rollups
    assert [bill["bill_num"] for bill in recent] == ["HB3", "HB2"]
