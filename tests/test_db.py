from app.db import _sanitize_db_value, connect, get_sync_status, init_db, reset_stale_sync_statuses, update_sync_status


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
