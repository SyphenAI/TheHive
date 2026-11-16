"""Auth dependencies.

Two kinds of principal:
  * a User, authenticated by a JWT (browser sessions)         -> full access by role
  * an ApiKey, authenticated by X-API-Key header (integrations) -> access by scopes

Scopes: read (list/get sessions, usage, registry), run (create sessions), admin (manage providers/seats).
API keys can never manage users or other API keys; that is JWT-admin only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, RevokedToken, User
from app.security import constant_time_eq, decode_access_token, hash_api_key, parse_api_key_prefix

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

SCOPE_RANK = {"read": 1, "run": 2, "admin": 3}


@dataclass
class Principal:
    kind: str  # "user" | "apikey"
    name: str
    role: str  # admin | member  (apikey: derived from scopes)
    scopes: set[str]
    user: User | None = None
    api_key: ApiKey | None = None

    def has_scope(self, scope: str) -> bool:
        if self.kind == "user":
            return self.role == "admin" or scope in {"read", "run"}
        need = SCOPE_RANK[scope]
        return any(SCOPE_RANK.get(s, 0) >= need for s in self.scopes)

    @property
    def owner_id(self) -> int | None:
        return self.user.id if self.user else None


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def get_current_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    if not token:
        raise _unauthorized()
    try:
        payload = decode_access_token(token)
    except JWTError as exc:
        raise _unauthorized() from exc
    username, jti, ver = payload.get("sub"), payload.get("jti"), payload.get("ver")
    if not username or not jti:
        raise _unauthorized()
    if db.query(RevokedToken).filter(RevokedToken.jti == jti).first():
        raise _unauthorized("Session has been signed out")
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.is_active:
        raise _unauthorized()
    if ver != user.token_version:
        raise _unauthorized("Session expired; sign in again")
    return user


def _resolve_api_key(db: Session, raw: str) -> ApiKey:
    prefix = parse_api_key_prefix(raw)
    if not prefix:
        raise _unauthorized("Malformed API key")
    row = db.query(ApiKey).filter(ApiKey.prefix == prefix, ApiKey.revoked.is_(False)).first()
    if not row or not constant_time_eq(row.key_hash, hash_api_key(raw)):
        raise _unauthorized("Invalid API key")
    if row.expires_at and row.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise _unauthorized("API key expired")
    row.last_used_at = datetime.now(UTC)
    db.commit()
    return row


def get_principal(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Principal:
    if x_api_key:
        key = _resolve_api_key(db, x_api_key.strip())
        scopes = {s for s in key.scopes.split(",") if s}
        return Principal(
            kind="apikey",
            name=f"key:{key.name}",
            role="admin" if "admin" in scopes else "member",
            scopes=scopes,
            api_key=key,
        )
    user = get_current_user(token, db)
    return Principal(
        kind="user",
        name=user.username,
        role=user.role,
        scopes={"read", "run", "admin"} if user.role == "admin" else {"read", "run"},
        user=user,
    )


def require_scope(scope: str):
    def _dep(p: Principal = Depends(get_principal)) -> Principal:
        if not p.has_scope(scope):
            raise HTTPException(status_code=403, detail=f"Requires '{scope}' scope")
        return p

    return _dep


def require_admin(user: User = Depends(get_current_user)) -> User:
    """JWT-only admin. Used for user and API-key management."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
