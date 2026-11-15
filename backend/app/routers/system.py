from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import Principal, require_admin, require_scope
from app.models import AuditLog, User
from app.schemas import HealthOut
from app.services.registry import load_registry

router = APIRouter(prefix="/api/v1", tags=["system"])
VERSION = "0.1.0"


@router.get("/health")
def health_public() -> dict:
    """Unauthenticated liveness probe for Docker. Reveals nothing but 'up'."""
    return {"status": "ok"}


@router.get("/health/detail", response_model=HealthOut)
def health_detail(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    cfg = get_settings()
    checks: dict = {"ok": True}
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"
        checks["ok"] = False
    try:
        reg = load_registry()
        checks["registry"] = f"{len(reg.providers)} provider(s), schema v{reg.version}"
    except Exception as exc:  # noqa: BLE001
        checks["registry"] = f"error: {exc}"
        checks["ok"] = False
    checks["data_dir_writable"] = cfg.data_dir.exists()
    return HealthOut(status="ok" if checks["ok"] else "degraded", version=VERSION, app_env=cfg.app_env, checks=checks)


@router.get("/audit")
def audit(limit: int = 50, db: Session = Depends(get_db), _: User = Depends(require_admin)) -> dict:
    cap = min(max(limit, 1), 500)
    rows = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(cap).all()
    return {
        "events": [
            {"id": r.id, "actor": r.actor, "action": r.action, "detail": r.detail, "created_at": r.created_at}
            for r in rows
        ]
    }
