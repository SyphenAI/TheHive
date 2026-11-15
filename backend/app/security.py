from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from jose import jwt

from app.config import get_settings

ALGORITHM = "HS256"
API_KEY_PREFIX = "hive_sk_"


# ---------- passwords ----------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


# ---------- JWT ----------
def create_access_token(
    subject: str, token_version: int, extra: dict[str, Any] | None = None
) -> tuple[str, str, datetime]:
    """Returns (token, jti, exp)."""
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=settings.access_token_expire_minutes)
    jti = secrets.token_urlsafe(24)
    payload: dict[str, Any] = {"sub": subject, "iat": now, "exp": exp, "jti": jti, "ver": token_version}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM), jti, exp


def decode_access_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, get_settings().secret_key, algorithms=[ALGORITHM])


# ---------- provider-key vault ----------
def _fernet() -> Fernet:
    return Fernet(get_settings().token_fernet_key.encode("ascii"))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Unable to decrypt secret; TOKEN_FERNET_KEY may have changed.") from exc


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


# ---------- integration API keys ----------
def generate_api_key() -> tuple[str, str, str]:
    """Returns (plaintext, prefix, sha256_hex). Plaintext is shown to the admin exactly once."""
    prefix = secrets.token_hex(4)  # 8 chars
    secret = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_PREFIX}{prefix}_{secret}"
    return plaintext, prefix, hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    # Keys are 256-bit random; a keyed-less sha256 is fine (no dictionary to attack). HMAC with
    # SECRET_KEY would tie the key table to the signing secret, which we deliberately avoid.
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def parse_api_key_prefix(plaintext: str) -> str | None:
    if not plaintext.startswith(API_KEY_PREFIX):
        return None
    rest = plaintext[len(API_KEY_PREFIX) :]
    prefix, _, secret = rest.partition("_")
    if len(prefix) != 8 or not secret:
        return None
    return prefix


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
