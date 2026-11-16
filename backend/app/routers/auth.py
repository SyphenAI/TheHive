"""Browser auth (JWT), password lifecycle, user admin, and integration API keys.

User and API-key management are JWT-admin only: an API key can never mint or revoke keys or touch users.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from jose import JWTError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user, oauth2_scheme, require_admin
from app.models import ApiKey, RevokedToken, User
from app.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    LoginRequest,
    PasswordChangeRequest,
    PasswordResetRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)
from app.security import create_access_token, decode_access_token, generate_api_key, hash_password, verify_password
from app.services.audit import log_event

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

WEAK = {"password", "change-on-first-login", "password1234", "hivehivehive"}


def _client_ip(request: Request) -> str:
    # Behind Docker port mapping every request looks like it comes from the bridge gateway, so IP is
    # only informational here. Lockout is per account.
    return request.client.host if request.client else "unknown"


def _login(request: Request, username: str, password: str, db: Session) -> TokenResponse:
    cfg = get_settings()
    ip = _client_ip(request)
    now = datetime.now(UTC)
    user = db.query(User).filter(User.username == username).first()

    if user and user.locked_until and user.locked_until.replace(tzinfo=UTC) > now:
        remaining = int((user.locked_until.replace(tzinfo=UTC) - now).total_seconds() // 60) + 1
        raise HTTPException(status_code=423, detail=f"Account locked; try again in {remaining} min")

    if not user or not verify_password(password, user.password_hash):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= cfg.lockout_threshold:
                user.locked_until = now + timedelta(minutes=cfg.lockout_minutes)
                user.failed_logins = 0
                log_event(db, actor=user.username, action="account_locked", detail=f"from {ip}")
        else:
            # Burn roughly the same time as a real bcrypt check so usernames can't be enumerated by timing.
            verify_password(password, "$2b$12$C6UzMDM.H6dfI/f/IKcEeO5pHj5Cf0lQtZkP3dYx1fzVQ9bYQy3O2")
        log_event(db, actor=username[:64], action="login_failed", detail=f"from {ip}")
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    user.failed_logins = 0
    user.locked_until = None
    token, _jti, _exp = create_access_token(user.username, user.token_version, {"role": user.role})
    log_event(db, actor=user.username, action="login", detail=f"from {ip}")
    db.commit()
    return TokenResponse(access_token=token, must_change_password=user.must_change_password)


@router.post("/login", response_model=TokenResponse)
def login_form(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _login(request, form.username, form.password, db)


@router.post("/login/json", response_model=TokenResponse)
def login_json(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    return _login(request, body.username, body.password, db)


@router.post("/logout", status_code=204)
def logout(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """Revoke this JWT server-side. Also prunes expired revocations."""
    try:
        payload = decode_access_token(token or "")
    except JWTError:
        return None
    exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
    db.merge(RevokedToken(jti=payload["jti"], expires_at=exp))
    db.query(RevokedToken).filter(RevokedToken.expires_at < datetime.now(UTC)).delete()
    log_event(db, actor=user.username, action="logout")
    db.commit()
    return None


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


def _check_strength(pw: str, username: str) -> None:
    if len(pw) < 12:
        raise HTTPException(status_code=400, detail="Use at least 12 characters")
    low = pw.lower()
    if low in WEAK or username.lower() in low or len(set(pw)) < 5:
        raise HTTPException(status_code=400, detail="Choose a stronger password")


@router.post("/change-password", response_model=TokenResponse)
def change_password(body: PasswordChangeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    _check_strength(body.new_password, user.username)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # kill every other session
    token, _, _ = create_access_token(user.username, user.token_version, {"role": user.role})
    log_event(db, actor=user.username, action="password_change", detail="all other sessions invalidated")
    db.commit()
    return TokenResponse(access_token=token, must_change_password=False)


# ---- users (JWT admin only) ----
@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.id.asc()).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
    _check_strength(body.password, body.username)
    role = body.role if body.role in {"admin", "member"} else "member"
    user = User(
        username=body.username,
        display_name=body.display_name or body.username,
        password_hash=hash_password(body.password),
        role=role,
        must_change_password=True,
    )
    db.add(user)
    log_event(db, actor=admin.username, action="user_create", detail=f"{body.username} ({role})")
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
def reset_password(
    user_id: int, body: PasswordResetRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _check_strength(body.new_password, user.username)
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = True
    user.token_version += 1
    user.failed_logins = 0
    user.locked_until = None
    log_event(db, actor=admin.username, action="user_password_reset", detail=user.username)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    user.is_active = False
    user.token_version += 1
    log_event(db, actor=admin.username, action="user_deactivate", detail=user.username)
    db.commit()
    db.refresh(user)
    return user


# ---- integration API keys (JWT admin only) ----
VALID_SCOPES = {"read", "run", "admin"}


@router.get("/api-keys", response_model=list[ApiKeyOut])
def list_api_keys(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(ApiKey).order_by(ApiKey.id.desc()).all()


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=201)
def create_api_key(body: ApiKeyCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    scopes = sorted({s.strip() for s in body.scopes if s.strip()})
    if not scopes or not set(scopes) <= VALID_SCOPES:
        raise HTTPException(status_code=400, detail=f"scopes must be a non-empty subset of {sorted(VALID_SCOPES)}")
    plaintext, prefix, digest = generate_api_key()
    expires = datetime.now(UTC) + timedelta(days=body.expires_days) if body.expires_days else None
    row = ApiKey(
        name=body.name.strip()[:64],
        prefix=prefix,
        key_hash=digest,
        scopes=",".join(scopes),
        created_by=admin.id,
        expires_at=expires,
    )
    db.add(row)
    log_event(db, actor=admin.username, action="apikey_create", detail=f"{row.name} [{row.scopes}] prefix {prefix}")
    db.commit()
    db.refresh(row)
    return ApiKeyCreated(key=plaintext, **ApiKeyOut.model_validate(row).model_dump())


@router.post("/api-keys/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(key_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="API key not found")
    row.revoked = True
    log_event(db, actor=admin.username, action="apikey_revoke", detail=f"{row.name} prefix {row.prefix}")
    db.commit()
    db.refresh(row)
    return row
