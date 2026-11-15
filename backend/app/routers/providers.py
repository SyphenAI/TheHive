"""Provider tokens (encrypted at rest) and hive seats (members)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import Principal, require_scope
from app.models import ApiToken, HiveMember
from app.schemas import (
    KillSwitchResponse,
    MemberCreate,
    MemberOut,
    MemberUpdate,
    TokenCreate,
    TokenOut,
    TokenTestResult,
)
from app.security import decrypt_secret, encrypt_secret, mask_secret
from app.services.audit import log_event
from app.services.llm import list_models, test_connection
from app.services.registry import load_registry

router = APIRouter(prefix="/api/v1/providers", tags=["providers"])


def _member_out(m: HiveMember) -> MemberOut:
    return MemberOut(
        id=m.id,
        token_id=m.token_id,
        provider=m.token.provider,
        model=m.model,
        nickname=m.nickname,
        enabled=m.enabled,
        can_answer=m.can_answer,
        can_critique=m.can_critique,
        can_judge=m.can_judge,
        can_redteam=bool(getattr(m, "can_redteam", False)),
        max_cost_per_call_usd=m.max_cost_per_call_usd,
    )


def _token_out(t: ApiToken) -> TokenOut:
    try:
        masked = mask_secret(decrypt_secret(t.encrypted_value))
    except ValueError:
        masked = "[decrypt-error]"
    return TokenOut(
        id=t.id,
        provider=t.provider,
        label=t.label,
        masked_value=masked,
        base_url_override=t.base_url_override,
        is_active=t.is_active,
        created_at=t.created_at,
        last_used_at=t.last_used_at,
        members=[_member_out(m) for m in t.members],
    )


@router.get("/registry")
def registry(_: Principal = Depends(require_scope("read"))) -> dict:
    return load_registry().as_public_dict()


# ---- tokens ----
@router.get("/tokens", response_model=list[TokenOut])
def list_tokens(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    rows = db.query(ApiToken).order_by(ApiToken.provider, ApiToken.label).all()
    return [_token_out(t) for t in rows]


@router.post("/tokens", response_model=TokenOut, status_code=201)
def create_token(body: TokenCreate, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))):
    reg = load_registry()
    provider = body.provider.strip().lower()
    if provider not in reg.providers:
        raise HTTPException(status_code=400, detail=f"Unknown provider; known: {sorted(reg.providers)}")
    label = body.label.strip() or "default"
    if db.query(ApiToken).filter(ApiToken.provider == provider, ApiToken.label == label).first():
        raise HTTPException(status_code=400, detail="A token with that provider/label already exists")
    if provider == "custom" and not body.base_url_override.strip():
        raise HTTPException(status_code=400, detail="Custom provider requires base_url_override")
    row = ApiToken(
        provider=provider,
        label=label,
        encrypted_value=encrypt_secret(body.value.strip()),
        base_url_override=body.base_url_override.strip(),
    )
    db.add(row)
    log_event(db, actor=admin.name, action="token_create", detail=f"{provider}/{label}")
    db.commit()
    db.refresh(row)
    return _token_out(row)


@router.post("/tokens/{token_id}/rotate", response_model=TokenOut)
def rotate_token(
    token_id: int, body: TokenCreate, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))
):
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    row.encrypted_value = encrypt_secret(body.value.strip())
    if body.base_url_override:
        row.base_url_override = body.base_url_override.strip()
    log_event(db, actor=admin.name, action="token_rotate", detail=f"{row.provider}/{row.label}")
    db.commit()
    db.refresh(row)
    return _token_out(row)


@router.post("/tokens/{token_id}/toggle", response_model=TokenOut)
def toggle_token(
    token_id: int, active: bool, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))
):
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    row.is_active = active
    log_event(
        db,
        actor=admin.name,
        action="token_enable" if active else "token_disable",
        detail=f"{row.provider}/{row.label}",
    )
    db.commit()
    db.refresh(row)
    return _token_out(row)


@router.post("/tokens/{token_id}/test", response_model=TokenTestResult)
def test_token(token_id: int, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))):
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    provider = load_registry().provider(row.provider)
    result = test_connection(provider, decrypt_secret(row.encrypted_value), row.base_url_override)
    log_event(
        db,
        actor=admin.name,
        action="token_test_ok" if result["ok"] else "token_test_fail",
        detail=f"{row.provider}/{row.label}",
    )
    db.commit()
    return TokenTestResult(**result)


@router.get("/tokens/{token_id}/models")
def discover_models(
    token_id: int, db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))
) -> dict:
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    reg = load_registry()
    provider = reg.provider(row.provider)
    live = list_models(provider, decrypt_secret(row.encrypted_value), row.base_url_override)
    return {"registry": sorted(provider.models), "live": live}


@router.delete("/tokens/{token_id}", status_code=204)
def delete_token(token_id: int, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))):
    row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Token not found")
    log_event(db, actor=admin.name, action="token_delete", detail=f"{row.provider}/{row.label}")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/kill-switch", response_model=KillSwitchResponse)
def kill_switch(db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))):
    """Wipe every stored credential. This does NOT revoke keys at the provider; do that in each vendor console."""
    rows = db.query(ApiToken).all()
    n = len(rows)
    for r in rows:
        db.delete(r)
    log_event(db, actor=admin.name, action="kill_switch", detail=f"wiped {n} token(s)")
    db.commit()
    db.execute(__import__("sqlalchemy").text("VACUUM"))  # drop freed pages so plaintext-free ciphertext isn't lingering
    return KillSwitchResponse(
        ok=True,
        removed_tokens=n,
        message=f"Removed {n} stored token(s). Keys are still live at each provider until you revoke them there.",
    )


# ---- members / seats ----
@router.get("/members", response_model=list[MemberOut])
def list_members(db: Session = Depends(get_db), _: Principal = Depends(require_scope("read"))):
    rows = db.query(HiveMember).join(ApiToken).order_by(ApiToken.provider, HiveMember.model).all()
    return [_member_out(m) for m in rows]


@router.post("/members", response_model=MemberOut, status_code=201)
def create_member(
    body: MemberCreate, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))
):
    token = db.query(ApiToken).filter(ApiToken.id == body.token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    m = HiveMember(
        token_id=token.id,
        model=body.model.strip(),
        nickname=body.nickname.strip(),
        can_answer=body.can_answer,
        can_critique=body.can_critique,
        can_judge=body.can_judge,
        can_redteam=body.can_redteam,
        max_cost_per_call_usd=max(0.0, body.max_cost_per_call_usd),
    )
    db.add(m)
    log_event(db, actor=admin.name, action="member_create", detail=f"{token.provider}/{m.model}")
    db.commit()
    db.refresh(m)
    return _member_out(m)


@router.patch("/members/{member_id}", response_model=MemberOut)
def update_member(
    member_id: int,
    body: MemberUpdate,
    db: Session = Depends(get_db),
    admin: Principal = Depends(require_scope("admin")),
):
    m = db.query(HiveMember).filter(HiveMember.id == member_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(m, k, v.strip() if isinstance(v, str) else v)
    log_event(db, actor=admin.name, action="member_update", detail=f"{m.token.provider}/{m.model}")
    db.commit()
    db.refresh(m)
    return _member_out(m)


@router.delete("/members/{member_id}", status_code=204)
def delete_member(member_id: int, db: Session = Depends(get_db), admin: Principal = Depends(require_scope("admin"))):
    m = db.query(HiveMember).filter(HiveMember.id == member_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    log_event(db, actor=admin.name, action="member_delete", detail=f"{m.token.provider}/{m.model}")
    db.delete(m)
    db.commit()
    return Response(status_code=204)
