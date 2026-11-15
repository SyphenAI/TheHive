from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change / admin reset: every JWT minted before is dead.
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RevokedToken(Base):
    """JWT ids revoked by explicit logout. Rows are pruned once past their exp."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(48), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ApiKey(Base):
    """Integration credential. Only the sha256 of the key is stored; shown once at creation.

    Format: hive_sk_<prefix8>_<secret>. Scopes: read | run | admin (admin excludes key management).
    """

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(8), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(String(64), default="read")  # comma separated
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiToken(Base):
    """One provider credential. The plaintext never leaves the server."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    label: Mapped[str] = mapped_column(String(64), default="default")
    encrypted_value: Mapped[str] = mapped_column(Text)
    base_url_override: Mapped[str] = mapped_column(String(256), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    members: Mapped[list[HiveMember]] = relationship(back_populates="token", cascade="all, delete-orphan")


class HiveMember(Base):
    """A model seat in the hive: provider token + model id + role flags."""

    __tablename__ = "hive_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_id: Mapped[int] = mapped_column(ForeignKey("api_tokens.id", ondelete="CASCADE"))
    model: Mapped[str] = mapped_column(String(96))
    nickname: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    can_answer: Mapped[bool] = mapped_column(Boolean, default=True)
    can_critique: Mapped[bool] = mapped_column(Boolean, default=True)
    can_judge: Mapped[bool] = mapped_column(Boolean, default=True)
    can_redteam: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-call ceiling for this seat (USD). 0 = inherit session budget only.
    max_cost_per_call_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    token: Mapped[ApiToken] = relationship(back_populates="members")


class HiveSession(Base):
    """One problem run through the hive."""

    __tablename__ = "hive_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    problem: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")  # retrieved prior work, pasted code, etc.
    task_type: Mapped[str] = mapped_column(String(32), default="general")  # general | code | security
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending|running|done|halted|error
    max_rounds: Mapped[int] = mapped_column(Integer, default=3)
    budget_usd: Mapped[float] = mapped_column(Float, default=1.0)
    spent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    judge_member_id: Mapped[int | None] = mapped_column(ForeignKey("hive_members.id"), nullable=True)
    verdict: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    halt_reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    turns: Mapped[list[HiveTurn]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="HiveTurn.id"
    )


class HiveTurn(Base):
    """One model output within a round. Anonymized alias is what other models see."""

    __tablename__ = "hive_turns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("hive_sessions.id", ondelete="CASCADE"), index=True)
    round_no: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(16))  # answer | critique | revise | judge
    member_id: Mapped[int] = mapped_column(ForeignKey("hive_members.id"))
    alias: Mapped[str] = mapped_column(String(16))  # "Response A", "Response B" ...
    content: Mapped[str] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(String(12), default="done")  # pending | done | error
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[HiveSession] = relationship(back_populates="turns")


class UsageEvent(Base):
    """Ledger. Every provider call lands here whether or not it succeeded."""

    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("hive_sessions.id"), nullable=True, index=True)
    member_id: Mapped[int | None] = mapped_column(ForeignKey("hive_members.id"), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(96))
    phase: Mapped[str] = mapped_column(String(16), default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(48), index=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
