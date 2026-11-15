"""Budget guard: enforced BEFORE every provider call, not just logged after.

Three ceilings, all must pass:
  1. per-seat max_cost_per_call_usd (if set)
  2. per-session budget_usd (minus spent so far, minus committed in-flight estimate)
  3. global daily / monthly ceilings from settings

Pre-flight uses a rough token estimate for input plus the requested max_output_tokens
as worst case. After the call, the real usage is recorded and the session's spent
figure is updated from the ledger, so estimates never drift the bookkeeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import HiveMember, HiveSession, UsageEvent
from app.services.registry import ModelInfo, estimate_cost


class BudgetExceeded(Exception):
    def __init__(self, scope: str, needed: float, available: float):
        self.scope = scope
        self.needed = needed
        self.available = available
        super().__init__(f"{scope} budget exceeded: next call needs ~${needed:.4f}, only ${available:.4f} available")


@dataclass
class Preflight:
    est_cost_usd: float
    tokens_in_est: int
    tokens_out_max: int


def spent_since(db: Session, since: datetime, session_id: int | None = None) -> float:
    q = db.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).filter(UsageEvent.created_at >= since)
    if session_id is not None:
        q = q.filter(UsageEvent.session_id == session_id)
    return float(q.scalar() or 0.0)


def session_spent(db: Session, session_id: int) -> float:
    q = db.query(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).filter(UsageEvent.session_id == session_id)
    return float(q.scalar() or 0.0)


def global_status(db: Session) -> dict:
    cfg = get_settings()
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    return {
        "daily_spent_usd": round(spent_since(db, day_start), 6),
        "daily_limit_usd": cfg.global_daily_budget_usd,
        "monthly_spent_usd": round(spent_since(db, month_start), 6),
        "monthly_limit_usd": cfg.global_monthly_budget_usd,
    }


def preflight(
    db: Session,
    *,
    session: HiveSession,
    member: HiveMember,
    info: ModelInfo,
    tokens_in_est: int,
    max_output_tokens: int,
    in_flight_usd: float = 0.0,
) -> Preflight:
    """Raise BudgetExceeded if the worst-case cost of the next call would cross any ceiling."""
    est = estimate_cost(info, tokens_in_est, max_output_tokens)

    if member.max_cost_per_call_usd and est > member.max_cost_per_call_usd:
        raise BudgetExceeded("seat", est, member.max_cost_per_call_usd)

    remaining_session = session.budget_usd - session_spent(db, session.id) - in_flight_usd
    if est > remaining_session:
        raise BudgetExceeded("session", est, max(remaining_session, 0.0))

    g = global_status(db)
    if est > g["daily_limit_usd"] - g["daily_spent_usd"] - in_flight_usd:
        raise BudgetExceeded("daily", est, max(g["daily_limit_usd"] - g["daily_spent_usd"], 0.0))
    if est > g["monthly_limit_usd"] - g["monthly_spent_usd"] - in_flight_usd:
        raise BudgetExceeded("monthly", est, max(g["monthly_limit_usd"] - g["monthly_spent_usd"], 0.0))

    return Preflight(est_cost_usd=est, tokens_in_est=tokens_in_est, tokens_out_max=max_output_tokens)


def record_usage(
    db: Session,
    *,
    session: HiveSession | None,
    member: HiveMember | None,
    info: ModelInfo,
    phase: str,
    tokens_in: int,
    tokens_out: int,
    ok: bool,
) -> float:
    cost = estimate_cost(info, tokens_in, tokens_out)
    db.add(
        UsageEvent(
            session_id=session.id if session else None,
            member_id=member.id if member else None,
            provider=info.provider,
            model=info.model,
            phase=phase,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            ok=ok,
        )
    )
    db.flush()
    if session:
        session.spent_usd = session_spent(db, session.id)
    return cost


def usage_summary(db: Session, days: int = 30) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (
        db.query(
            UsageEvent.provider,
            UsageEvent.model,
            func.count(UsageEvent.id),
            func.sum(UsageEvent.tokens_in),
            func.sum(UsageEvent.tokens_out),
            func.sum(UsageEvent.cost_usd),
        )
        .filter(UsageEvent.created_at >= since)
        .group_by(UsageEvent.provider, UsageEvent.model)
        .all()
    )
    return {
        "days": days,
        "by_model": [
            {
                "provider": p,
                "model": m,
                "calls": int(c or 0),
                "tokens_in": int(ti or 0),
                "tokens_out": int(to or 0),
                "cost_usd": round(float(cost or 0.0), 6),
            }
            for p, m, c, ti, to, cost in rows
        ],
        "total_usd": round(sum(float(r[5] or 0.0) for r in rows), 6),
        **global_status(db),
    }
