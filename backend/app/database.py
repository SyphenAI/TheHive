from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()
_connect_args = {"check_same_thread": False} if _settings.db_url.startswith("sqlite") else {}
engine = create_engine(_settings.db_url, connect_args=_connect_args, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
    if _settings.db_url.startswith("sqlite"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
