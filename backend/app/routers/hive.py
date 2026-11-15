"""Hive sessions: submit a problem, run the protocol in the background, read the transcript.

This router is the integration surface. API keys with `run` can start sessions and poll them;
`read` can only observe. Sessions started by an API key are owned by the first admin account.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.deps import Principal, require_scope
from app.hive.orchestrator import Orchestrator, Seat
from app.models import ApiToken, HiveMember, HiveSession, User
from app.schemas import BudgetStatus, SessionCreate, SessionOut, SessionSummary
from app.services import budget
from app.services.audit import log_event

router = APIRouter(prefix="/api/v1/hive", tags=["hive"])
logger = logging.getLogger("thehive.router")


def _can_see(p: Principal, s: HiveSession) -> bool:
    if p.kind == "apikey" or p.role == "admin":
        return True
    return s.owner_id == p.user.id


def _system_owner(db: Session) -> int:
    admin = db.query(User).filter(User.role == "admin").order_by(User.id).first()
    return admin.id if admin else 0


def _load_seats(db: Session, member_ids: list[int] | None) -> list[Seat]:
    q = db.query(HiveMember).join(ApiToken).filter(HiveMember.enabled.is_(True), ApiToken.is_active.is_(True))
    if member_ids:
        q = q.filter(HiveMember.id.in_(member_ids))
    return [Seat(member=m, token=m.token) for m in q.all()]


def _run_in_background(session_id: int) -> None:
    db = SessionLocal()
    try:
        session = db.query(HiveSession).filter(HiveSession.id == session_id).first()
        if not session:
            return
        member_ids = (session.verdict or {}).get("_requested_member_ids")
        seats = _load_seats(db, member_ids)
        judge = None
        if session.judge_member_id:
            judge = next((s for s in seats if s.member.id == session.judge_member_id), None)
            if judge is None:
                jm = db.query(HiveMember).filter(HiveMember.id == session.judge_member_id).first()
                if jm and jm.enabled and jm.token.is_active:
                    judge = Seat(member=jm, token=jm.token)
        session.verdict = None
        Orchestrator(db).run(session, seats, judge)
    except Exception:  # noqa: BLE001
        logger.exception("background run failed for session %s", session_id)
    finally:
        db.close()


@router.get("/budget", response_model=BudgetStatus)
def budget_status(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    return BudgetStatus(**budget.global_status(db))


@router.get("/usage")
def usage(days: int = 30, db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))) -> dict:
    return budget.usage_summary(db, days=min(max(days, 1), 365))


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(limit: int = 50, db: Session = Depends(get_db), p: Principal = Depends(require_scope("read"))):
    q = db.query(HiveSession).order_by(HiveSession.id.desc()).limit(min(max(limit, 1), 200))
    if p.kind == "user" and p.role != "admin":
        q = q.filter(HiveSession.owner_id == p.user.id)
    return q.all()


@router.post("/sessions", response_model=SessionOut, status_code=202)
def create_session(
    body: SessionCreate,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    p: Principal = Depends(require_scope("run")),
):
    if body.task_type not in {"general", "code", "security"}:
        raise HTTPException(status_code=400, detail="task_type must be general|code|security")
    seats = _load_seats(db, body.member_ids)
    if len([s for s in seats if s.member.can_answer]) < 2:
        raise HTTPException(status_code=400, detail="Need at least two enabled answering seats to run a hive")
    g = budget.global_status(db)
    if g["daily_spent_usd"] >= g["daily_limit_usd"]:
        raise HTTPException(status_code=402, detail="Global daily budget exhausted")

    session = HiveSession(
        owner_id=p.owner_id or _system_owner(db),
        title=body.title.strip()[:200] or body.problem.strip()[:80],
        problem=body.problem,
        context=body.context,
        task_type=body.task_type,
        max_rounds=body.max_rounds,
        budget_usd=body.budget_usd,
        judge_member_id=body.judge_member_id,
        # Stash requested seats so the background worker (fresh DB session) can read them.
        verdict={"_requested_member_ids": body.member_ids} if body.member_ids else None,
    )
    db.add(session)
    log_event(
        db,
        actor=p.name,
        action="hive_start",
        detail=f"{body.task_type} · {len(seats)} seat(s) · ${body.budget_usd:.2f} cap",
    )
    db.commit()
    db.refresh(session)
    tasks.add_task(_run_in_background, session.id)
    return session


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_scope("read"))):
    s = db.query(HiveSession).filter(HiveSession.id == session_id).first()
    if not s or not _can_see(p, s):
        raise HTTPException(status_code=404, detail="Session not found")
    return s


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    s = db.query(HiveSession).filter(HiveSession.id == session_id).first()
    if not s or not _can_see(p, s):
        raise HTTPException(status_code=404, detail="Session not found")
    if s.status == "running":
        raise HTTPException(status_code=409, detail="Session is still running")
    db.delete(s)
    log_event(db, actor=p.name, action="hive_delete", detail=str(session_id))
    db.commit()
