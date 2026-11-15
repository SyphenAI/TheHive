from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, require_admin
from app.models import User
from app.schemas import LoginRequest, PasswordChangeRequest, TokenResponse, UserCreate, UserOut
from app.security import create_access_token, hash_password, verify_password
from app.services.audit import log_event

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Tiny in-memory throttle: 10 failed attempts per IP per 5 minutes. Good enough for a local tool.
_FAILS: dict[str, list[float]] = {}
_WINDOW = 300
_MAX = 10


def _throttled(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _FAILS.get(ip, []) if now - t < _WINDOW]
    _FAILS[ip] = hits
    return len(hits) >= _MAX


def _fail(ip: str) -> None:
    _FAILS.setdefault(ip, []).append(time.time())


def _login(request: Request, username: str, password: str, db: Session) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    if _throttled(ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts; wait a few minutes")
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        _fail(ip)
        log_event(db, actor=username[:64], action="login_failed", detail=f"from {ip}")
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")
    token = create_access_token(user.username, {"role": user.role})
    log_event(db, actor=user.username, action="login", detail=f"from {ip}")
    db.commit()
    return TokenResponse(access_token=token, must_change_password=user.must_change_password)


@router.post("/login", response_model=TokenResponse)
def login_form(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _login(request, form.username, form.password, db)


@router.post("/login/json", response_model=TokenResponse)
def login_json(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    return _login(request, body.username, body.password, db)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(body: PasswordChangeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status_code=400, detail="New password must be different")
    if body.new_password.lower() in {user.username.lower(), "password", "change-on-first-login"}:
        raise HTTPException(status_code=400, detail="Choose a stronger password")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    log_event(db, actor=user.username, action="password_change")
    db.commit()
    db.refresh(user)
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(User).order_by(User.id.asc()).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=400, detail="Username already exists")
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


@router.post("/users/{user_id}/deactivate", response_model=UserOut)
def deactivate_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    user.is_active = False
    log_event(db, actor=admin.username, action="user_deactivate", detail=user.username)
    db.commit()
    db.refresh(user)
    return user
