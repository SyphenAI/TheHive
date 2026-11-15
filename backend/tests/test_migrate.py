from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

from app.services.migrate import DowngradeRefused, ensure_schema


def _engine():
    return create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)


def test_adds_missing_columns_to_old_table():
    eng = _engine()
    # Simulate a v0.1 users table that predates token_version / failed_logins / locked_until.
    with eng.begin() as c:
        c.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(64), display_name VARCHAR(128), "
                "password_hash VARCHAR(128), role VARCHAR(16), is_active BOOLEAN, must_change_password BOOLEAN, created_at DATETIME)"
            )
        )
        c.execute(
            text(
                "INSERT INTO users (username, password_hash, role, is_active, must_change_password) VALUES ('old','x','admin',1,0)"
            )
        )
    actions = ensure_schema(eng, "0.2.0")
    cols = {c["name"] for c in inspect(eng).get_columns("users")}
    assert {"token_version", "failed_logins", "locked_until"} <= cols
    assert any("users.token_version" in a for a in actions)
    with eng.connect() as c:
        row = c.execute(text("SELECT token_version, failed_logins FROM users WHERE username='old'")).fetchone()
    assert row == (1, 0)  # defaults applied to the existing row
    # Second run is a no-op.
    assert ensure_schema(eng, "0.2.0") == []


def test_fresh_db_and_version_stamp():
    eng = _engine()
    ensure_schema(eng, "0.2.0")
    assert "api_keys" in inspect(eng).get_table_names()
    with eng.connect() as c:
        assert c.execute(text("SELECT value FROM schema_meta WHERE key='app_version'")).scalar() == "0.2.0"


def test_downgrade_refused():
    eng = _engine()
    ensure_schema(eng, "0.3.0")
    with pytest.raises(DowngradeRefused):
        ensure_schema(eng, "0.2.0")
