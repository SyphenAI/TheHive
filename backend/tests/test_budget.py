from __future__ import annotations

import pytest

from app.hive.orchestrator import Orchestrator
from app.models import HiveTurn, UsageEvent
from app.services import budget
from app.services.registry import load_registry
from tests.conftest import FakeTransport, make_seat


def test_preflight_blocks_session_budget(db, session, seats):
    session.budget_usd = 0.00001
    db.commit()
    info = load_registry().provider("openai").model_info("gpt-4o-mini")
    with pytest.raises(budget.BudgetExceeded) as exc:
        budget.preflight(
            db, session=session, member=seats[0].member, info=info, tokens_in_est=1000, max_output_tokens=2000
        )
    assert exc.value.scope == "session"


def test_preflight_blocks_seat_cap(db, session, seats):
    seats[0].member.max_cost_per_call_usd = 0.0000001
    db.commit()
    info = load_registry().provider("openai").model_info("gpt-4o-mini")
    with pytest.raises(budget.BudgetExceeded) as exc:
        budget.preflight(
            db, session=session, member=seats[0].member, info=info, tokens_in_est=1000, max_output_tokens=2000
        )
    assert exc.value.scope == "seat"


def test_run_halts_cleanly_when_budget_runs_out(db, session, seats, rng):
    # Expensive seat so the second call trips the session cap.
    pricey = make_seat(db, "anthropic", "claude-opus-5-5")
    all_seats = [seats[0], pricey, seats[2]]
    # Opus worst case: 2000 out tokens * $75/M = $0.15 + input. Set budget just under that.
    session.budget_usd = 0.10
    db.commit()
    t = FakeTransport()
    Orchestrator(db, transport=t, rng=rng).run(session, all_seats, judge=None)
    assert session.status == "halted"
    assert "session budget exceeded" in session.halt_reason
    # The halted call left a turn with the reason and did not hit the transport for that seat.
    halted = db.query(HiveTurn).filter(HiveTurn.session_id == session.id, HiveTurn.error != "").all()
    assert len(halted) == 1 and halted[0].member_id == pricey.member.id
    assert all(c["model"] != "claude-opus-5-5" for c in t.calls)


def test_usage_ledger_drives_spent(db, session, seats, rng):
    t = FakeTransport(tokens_out=1000)
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    ledger = sum(e.cost_usd for e in db.query(UsageEvent).filter(UsageEvent.session_id == session.id))
    assert abs(ledger - session.spent_usd) < 1e-9
    assert session.spent_usd <= session.budget_usd


def test_unknown_model_uses_provider_default_price():
    info = load_registry().provider("openai").model_info("gpt-99-ultra")
    assert info.known is False
    assert info.price_in == 2.5 and info.price_out == 10.0
