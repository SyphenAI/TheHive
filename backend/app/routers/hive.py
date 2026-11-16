"""Hive sessions: submit a problem, run the protocol in the background, read the transcript.

This router is the integration surface. API keys with `run` can start sessions and poll them;
`read` can only observe. Sessions started by an API key are owned by the first admin account.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.deps import Principal, require_scope
from app.hive.estimate import estimate_session
from app.hive.export import session_markdown
from app.hive.orchestrator import Orchestrator, Seat
from app.models import ApiToken, HiveMember, HiveSession, HiveTurn, Lens, PanelTemplate, SeatPreset, User
from app.schemas import (
    BudgetStatus,
    EstimateRequest,
    LensIn,
    LensOut,
    PanelIn,
    PanelOut,
    PresetIn,
    PresetOut,
    SessionCreate,
    SessionOut,
    SessionSummary,
    TurnOut,
)
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
        lenses={str(k): v for k, v in (body.lenses or {}).items()} or None,
        devils_advocate=body.devils_advocate,
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


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: int, after: int = 0, p: Principal = Depends(require_scope("read"))):
    """Server-sent events: `turn` for every new or updated turn, `status` on session status change,
    `done` when the session reaches a terminal state. Resumable with ?after=<last turn id>.
    """
    db0 = SessionLocal()
    try:
        s0 = db0.query(HiveSession).filter(HiveSession.id == session_id).first()
        if not s0 or not _can_see(p, s0):
            raise HTTPException(status_code=404, detail="Session not found")
    finally:
        db0.close()

    async def gen():
        last_id = after
        seen_state: dict[int, tuple[str, int]] = {}
        last_status = None
        idle = 0
        while True:
            db = SessionLocal()
            try:
                s = db.query(HiveSession).filter(HiveSession.id == session_id).first()
                if not s:
                    yield _sse("done", {"status": "deleted"})
                    return
                turns = db.query(HiveTurn).filter(HiveTurn.session_id == session_id).order_by(HiveTurn.id.asc()).all()
                for t in turns:
                    sig = (t.state, len(t.content or ""))
                    if t.id > last_id or seen_state.get(t.id) != sig:
                        seen_state[t.id] = sig
                        last_id = max(last_id, t.id)
                        yield _sse("turn", TurnOut.model_validate(t).model_dump())
                if s.status != last_status:
                    last_status = s.status
                    yield _sse("status", {"status": s.status, "spent_usd": s.spent_usd, "halt_reason": s.halt_reason})
                if s.status in ("done", "halted", "error"):
                    yield _sse("done", {"status": s.status, "verdict": s.verdict})
                    return
            finally:
                db.close()
            idle += 1
            if idle % 15 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/estimate")
def estimate(
    body: EstimateRequest, db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))
) -> dict:
    """Worst-case and likely cost for a run with these seats, using the guard's own math."""
    seats = _load_seats(db, body.member_ids)
    est = estimate_session(
        seats=seats,
        problem=body.problem,
        context=body.context,
        max_rounds=body.max_rounds,
        devils_advocate=body.devils_advocate,
    )
    est["global"] = budget.global_status(db)
    return est


@router.get("/sessions/{session_id}/export.md", response_class=PlainTextResponse)
def export_markdown(
    session_id: int, reveal: bool = False, db: Session = Depends(get_db), p: Principal = Depends(require_scope("read"))
):
    s = db.query(HiveSession).filter(HiveSession.id == session_id).first()
    if not s or not _can_see(p, s):
        raise HTTPException(status_code=404, detail="Session not found")
    members = {m.id: m for m in db.query(HiveMember).all()}
    md = session_markdown(s, members, reveal=reveal)
    fname = f"thehive-session-{s.id}.md"
    return PlainTextResponse(
        md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ---- presets ----
@router.get("/presets", response_model=list[PresetOut])
def list_presets(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    return db.query(SeatPreset).order_by(SeatPreset.name).all()


@router.post("/presets", response_model=PresetOut, status_code=201)
def save_preset(body: PresetIn, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    row = db.query(SeatPreset).filter(SeatPreset.name == body.name.strip()).first()
    if row is None:
        row = SeatPreset(name=body.name.strip())
        db.add(row)
    row.member_ids = body.member_ids
    row.judge_member_id = body.judge_member_id
    row.task_type = body.task_type
    row.max_rounds = body.max_rounds
    row.budget_usd = body.budget_usd
    log_event(db, actor=p.name, action="preset_save", detail=row.name)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/presets/{preset_id}", status_code=204)
def delete_preset(preset_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    row = db.query(SeatPreset).filter(SeatPreset.id == preset_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Preset not found")
    db.delete(row)
    log_event(db, actor=p.name, action="preset_delete", detail=row.name)
    db.commit()


# ---- lenses & panels ----
@router.get("/lenses", response_model=list[LensOut])
def list_lenses(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    return db.query(Lens).order_by(Lens.category, Lens.name).all()


@router.post("/lenses", response_model=LensOut, status_code=201)
def save_lens(body: LensIn, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    row = db.query(Lens).filter(Lens.name == body.name.strip()).first()
    if row is None:
        row = Lens(name=body.name.strip(), builtin=False)
        db.add(row)
    row.category = body.category.strip() or "general"
    row.text = body.text.strip()
    log_event(db, actor=p.name, action="lens_save", detail=row.name)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/lenses/{lens_id}", status_code=204)
def delete_lens(lens_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    row = db.query(Lens).filter(Lens.id == lens_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Lens not found")
    db.delete(row)
    log_event(db, actor=p.name, action="lens_delete", detail=row.name)
    db.commit()


@router.get("/panels", response_model=list[PanelOut])
def list_panels(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    return db.query(PanelTemplate).order_by(PanelTemplate.task_type, PanelTemplate.name).all()


@router.post("/panels", response_model=PanelOut, status_code=201)
def save_panel(body: PanelIn, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    known = {r.name for r in db.query(Lens.name).all()}
    missing = [n for n in body.lens_names if n not in known]
    if missing:
        raise HTTPException(status_code=400, detail=f"Unknown lens(es): {missing}")
    row = db.query(PanelTemplate).filter(PanelTemplate.name == body.name.strip()).first()
    if row is None:
        row = PanelTemplate(name=body.name.strip(), builtin=False)
        db.add(row)
    row.task_type = body.task_type
    row.description = body.description.strip()[:200]
    row.lens_names = body.lens_names
    log_event(db, actor=p.name, action="panel_save", detail=row.name)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/panels/{panel_id}", status_code=204)
def delete_panel(panel_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_scope("run"))):
    row = db.query(PanelTemplate).filter(PanelTemplate.id == panel_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Panel not found")
    db.delete(row)
    log_event(db, actor=p.name, action="panel_delete", detail=row.name)
    db.commit()
