from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import AuditLog

MAX_AUDIT_ROWS = 2000


def log_event(db: Session, actor: str, action: str, detail: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, detail=detail[:4000]))
    # Trim oldest rows past retention. Cheap on SQLite.
    count = db.query(AuditLog).count()
    if count > MAX_AUDIT_ROWS:
        overflow = count - MAX_AUDIT_ROWS
        oldest = db.query(AuditLog.id).order_by(AuditLog.id.asc()).limit(overflow).all()
        ids = [row[0] for row in oldest]
        if ids:
            db.query(AuditLog).filter(AuditLog.id.in_(ids)).delete(synchronize_session=False)
