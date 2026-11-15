from __future__ import annotations

import os
import random
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Must be set before app modules import settings.
os.environ.setdefault("SECRET_KEY", "test-" + "x" * 48)
os.environ.setdefault("TOKEN_FERNET_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATA_DIR", str(Path("/tmp/thehive-test-data")))
os.environ.setdefault("STORAGE_DIR", str(Path("/tmp/thehive-test-storage")))
os.environ.setdefault("DATABASE_URL", "sqlite://")  # in-memory
os.environ.setdefault("GLOBAL_DAILY_BUDGET_USD", "5")
os.environ.setdefault("GLOBAL_MONTHLY_BUDGET_USD", "50")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database import Base  # noqa: E402
from app.models import ApiToken, HiveMember, HiveSession, User  # noqa: E402
from app.security import encrypt_secret, hash_password  # noqa: E402
from app.services.llm import ChatError, ChatResult  # noqa: E402


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def user(db):
    u = User(username="tester", password_hash=hash_password("x" * 12), role="admin")
    db.add(u)
    db.commit()
    return u


def make_seat(db, provider: str, model: str, **flags):
    tok = db.query(ApiToken).filter(ApiToken.provider == provider).first()
    if not tok:
        tok = ApiToken(provider=provider, label="default", encrypted_value=encrypt_secret("sk-test"))
        db.add(tok)
        db.flush()
    m = HiveMember(token_id=tok.id, model=model, nickname=f"{provider}:{model}", **flags)
    db.add(m)
    db.commit()
    db.refresh(m)
    from app.hive.orchestrator import Seat

    return Seat(member=m, token=tok)


@pytest.fixture()
def seats(db):
    return [
        make_seat(db, "openai", "gpt-4o-mini"),
        make_seat(db, "anthropic", "claude-haiku-4-5-20251001"),
        make_seat(db, "google", "gemini-2.5-flash", can_answer=False),  # judge-only seat
    ]


@pytest.fixture()
def session(db, user):
    s = HiveSession(
        owner_id=user.id, title="t", problem="What is 2+2?", task_type="general", max_rounds=3, budget_usd=1.0
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


class FakeTransport:
    """Scripted transport. Records every call. Never touches the network."""

    def __init__(
        self, tokens_out: int = 100, fail_models: set[str] | None = None, judge_json: bool = True, drift: bool = False
    ):
        self.calls: list[dict] = []
        self.tokens_out = tokens_out
        self.fail_models = fail_models or set()
        self.judge_json = judge_json
        self.drift = drift

    def chat(self, *, provider, info, api_key, system, messages, max_output_tokens, temperature, base_url_override=""):
        self.calls.append(
            {
                "provider": provider.key,
                "model": info.model,
                "system": system,
                "user": messages[-1]["content"],
                "max_out": max_output_tokens,
                "temp": temperature,
            }
        )
        if info.model in self.fail_models:
            raise ChatError("boom", status=500, retryable=True)
        n = len(self.calls)
        if "judge" in system.lower() and "STRICT JSON" in system:
            content = (
                (
                    '{"scores": {"Response A": {"total": 30}, "Response B": {"total": 25}}, '
                    '"winner": "Response A", "rationale": "r", "synthesis": "s"}'
                )
                if self.judge_json
                else "nope"
            )
        elif "rigorous reviewer" in system:
            content = f"## Critique of Response A\nfine\n## Critique of Response B\nfine ({n})"
        elif "REVISED" in system:
            content = f"Revised answer {n}" if self.drift else "Four."
        else:
            content = "Four."
        return ChatResult(content=content, tokens_in=50, tokens_out=self.tokens_out, latency_ms=5, raw_model=info.model)


@pytest.fixture()
def rng():
    return random.Random(1234)
