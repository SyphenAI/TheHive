from __future__ import annotations

from app.hive.orchestrator import Orchestrator
from app.models import HiveTurn
from tests.conftest import FakeTransport


def test_full_run_blind_critique_revise_judge(db, session, seats, rng):
    t = FakeTransport()
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)

    assert session.status == "done", session.halt_reason
    phases = [c["system"][:30] for c in t.calls]
    # Round 1: two answering seats, blind.
    answer_calls = [c for c in t.calls if "one member of a panel of independent" in c["system"]]
    assert len(answer_calls) == 2
    for c in answer_calls:
        assert "peer_response" not in c["user"], "round-1 answers must not see peers"

    # Judge is the non-answering seat (google), and verdict is parsed.
    assert session.judge_member_id == seats[2].member.id
    assert session.verdict["winner"] == "Response A"
    assert session.verdict["judge_excluded_own_answer"] is False
    assert set(session.verdict["alias_map"].values()) == {"openai:gpt-4o-mini", "anthropic:claude-haiku-4-5-20251001"}

    # Converged after first revise (answers identical) -> only one critique/revise cycle.
    turns = db.query(HiveTurn).filter(HiveTurn.session_id == session.id).all()
    rounds = {t.round_no for t in turns if t.phase in ("critique", "revise")}
    assert rounds == {2}
    assert session.spent_usd > 0
    assert phases  # sanity


def test_critique_is_anonymized(db, session, seats, rng):
    t = FakeTransport()
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    critique_calls = [c for c in t.calls if "rigorous reviewer" in c["system"]]
    assert critique_calls
    for c in critique_calls:
        assert "openai" not in c["user"].lower() and "anthropic" not in c["user"].lower()
        assert 'alias="Response A"' in c["user"] and 'alias="Response B"' in c["user"]


def test_runs_all_rounds_when_answers_drift(db, session, seats, rng):
    t = FakeTransport(drift=True)
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    turns = db.query(HiveTurn).filter(HiveTurn.session_id == session.id).all()
    rounds = {t.round_no for t in turns if t.phase == "revise"}
    assert rounds == {2, 3}


def test_seat_failure_does_not_kill_session(db, session, seats, rng):
    t = FakeTransport(fail_models={"gpt-4o-mini"})
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    assert session.status == "done"
    errs = db.query(HiveTurn).filter(HiveTurn.session_id == session.id, HiveTurn.error != "").all()
    assert errs and all("boom" in e.error for e in errs)
    # Only one answer survived, judge still judges it.
    assert len(session.verdict["final_answers"]) == 1


def test_bad_judge_json_is_captured(db, session, seats, rng):
    t = FakeTransport(judge_json=False)
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=None)
    assert session.status == "done"
    assert "error" in session.verdict and session.verdict["raw"] == "nope"


def test_explicit_judge_that_also_answered_is_excluded(db, session, seats, rng):
    t = FakeTransport()
    judge = seats[0]  # openai, which also answers
    Orchestrator(db, transport=t, rng=rng).run(session, seats, judge=judge)
    assert session.judge_member_id == judge.member.id
    assert session.verdict["judge_excluded_own_answer"] is True
    assert "openai:gpt-4o-mini" not in session.verdict["alias_map"].values()
