"""Forward-only schema migrator for SQLite.

create_all() builds tables that don't exist. This fills the gap for tables that DO exist but
gained columns in a newer version: it compares the ORM metadata to the live schema and issues
ALTER TABLE ... ADD COLUMN for anything missing, with the model's default. Dropping or renaming
columns is deliberately not supported; add a new column instead and leave the old one.

Also records the app version that last touched the DB so a downgrade can be detected and refused.
"""

from __future__ import annotations

import logging

from sqlalchemy import Column, inspect, text
from sqlalchemy.engine import Engine

from app.database import Base

logger = logging.getLogger("thehive.migrate")


class DowngradeRefused(RuntimeError):
    pass


def _parse(v: str) -> tuple[int, ...]:
    import re

    return tuple(int(n) for n in re.findall(r"\d+", v)[:3]) or (0,)


def _sql_default(col: Column) -> str:
    d = col.default
    if d is None or not getattr(d, "is_scalar", False):
        return ""
    v = d.arg
    if isinstance(v, bool):
        return f" DEFAULT {1 if v else 0}"
    if isinstance(v, int | float):
        return f" DEFAULT {v}"
    if isinstance(v, str):
        return " DEFAULT '" + v.replace("'", "''") + "'"
    return ""


def ensure_schema(engine: Engine, app_version: str) -> list[str]:
    """Create missing tables, add missing columns, stamp version. Returns a list of actions taken."""
    actions: list[str] = []
    Base.metadata.create_all(bind=engine)
    insp = inspect(engine)

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"))
        row = conn.execute(text("SELECT value FROM schema_meta WHERE key='app_version'")).fetchone()
        stored = row[0] if row else None
        if stored and _parse(stored) > _parse(app_version):
            raise DowngradeRefused(
                f"Database was last used by TheHive {stored}; this build is {app_version}. "
                "Restore the pre-update backup from data/backups or run the newer version."
            )

        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ctype = col.type.compile(dialect=engine.dialect)
                null = "" if col.nullable else " NOT NULL"
                default = _sql_default(col)
                if not col.nullable and not default:
                    # SQLite requires a default for NOT NULL ADD COLUMN; fall back to a type-appropriate zero.
                    default = " DEFAULT ''" if "CHAR" in ctype or "TEXT" in ctype else " DEFAULT 0"
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ctype}{null}{default}'
                conn.execute(text(stmt))
                actions.append(f"{table.name}.{col.name} added")

        conn.execute(
            text("INSERT INTO schema_meta(key,value) VALUES('app_version',:v) ON CONFLICT(key) DO UPDATE SET value=:v"),
            {"v": app_version},
        )

    for a in actions:
        logger.info("migration: %s", a)
    return actions
