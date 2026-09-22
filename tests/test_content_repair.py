import json

import pytest

from app.content_repair import apply_repair, plan_minnesota_repair
from app.db import connect, upsert_bill
from tests.test_content_quality import bill


def original():
    upsert_bill(bill("SF100", state="mn", enrolled_no="Introduction", status_label="Passed Legislature", outcome="passed"))
    with connect() as connection:
        return dict(connection.execute("SELECT * FROM bills").fetchone())


def detail():
    return dict(bill="SF100", specialSessionValue=0, billStatus="Referred to committee",
                lastAction="Referred to committee", lastActionDate="2026-03-01",
                signedDate="", chapter="", enrolledNumber="", billActions=[])


def test_repair_preserves_record_and_is_idempotent(tmp_path):
    row = original()
    changes = plan_minnesota_repair(row, detail())
    assert changes["outcome"] == "active"
    assert changes["enrolled_no"] == ""
    assert json.loads(changes["interpretation_json"])["fact_check_status"] == "stale"
    path = tmp_path / "backup.jsonl"
    with path.open("x") as backup:
        assert apply_repair(row, changes, backup)
    with connect() as connection:
        repaired = dict(connection.execute("SELECT * FROM bills").fetchone())
    assert repaired["id"] == row["id"]
    assert plan_minnesota_repair(repaired, detail()) == {}
    assert json.loads(path.read_text())["before"] == row


def test_repair_does_not_overwrite_an_intervening_sync(tmp_path):
    row = original()
    with connect() as connection:
        connection.execute("UPDATE bills SET updated_at = ? WHERE id = ?", ("newer", row["id"]))
        connection.commit()
    with (tmp_path / "backup.jsonl").open("x") as backup:
        assert not apply_repair(row, plan_minnesota_repair(row, detail()), backup)
    with connect() as connection:
        assert connection.execute("SELECT updated_at FROM bills").fetchone()["updated_at"] == "newer"


def test_repair_rejects_wrong_bill_or_session():
    row = original()
    with pytest.raises(ValueError):
        plan_minnesota_repair(row, {**detail(), "bill": "SF101"})
    with pytest.raises(ValueError):
        plan_minnesota_repair(row, {**detail(), "specialSessionValue": 1})
