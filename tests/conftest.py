from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.db import connect, init_db
from app.settings import get_settings


TESTS_DIR = Path(__file__).resolve().parent
TEST_DB_PATH = TESTS_DIR / ".test-keepinglawsimple.db"
TEST_GEOIP_DIR = TESTS_DIR / ".test-geoip"

os.environ["KLS_DATABASE_PATH"] = str(TEST_DB_PATH)
os.environ["KLS_ANALYTICS_ENABLED"] = "0"
os.environ["KLS_ANALYTICS_COUNTRY_DB_PATH"] = str(TEST_GEOIP_DIR / "dbip-city-lite.mmdb")
os.environ["KLS_ADMIN_USERNAME"] = "admin"
os.environ["KLS_ADMIN_PASSWORD"] = "test-password"
os.environ["KLS_ANALYTICS_HMAC_SECRET"] = "test-hmac-secret"
os.environ["KLS_GOOGLE_ANALYTICS_ID"] = "G-W6NEFX21NR"

get_settings.cache_clear()


@pytest.fixture(autouse=True)
def reset_test_state() -> None:
    shutil.rmtree(TEST_GEOIP_DIR, ignore_errors=True)
    get_settings.cache_clear()
    init_db()
    with connect() as connection:
        tables = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            connection.execute(f"DELETE FROM {table}")
        connection.commit()
    yield
    shutil.rmtree(TEST_GEOIP_DIR, ignore_errors=True)
