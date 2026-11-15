"""Application settings.

Secrets are REQUIRED. The app refuses to start with missing or placeholder
values for SECRET_KEY / TOKEN_FERNET_KEY so a leaked .env.example default can
never protect a real token vault.
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_MARKERS = ("change-me", "replace-with", "example", "placeholder", "local-dev")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "TheHive"
    app_env: str = "pre-prod"
    host: str = "127.0.0.1"
    port: int = 50090

    data_dir: Path = Path("/app/data")
    storage_dir: Path = Path("/app/storage")
    database_url: str = ""

    # Required. Validated below.
    secret_key: str = ""
    token_fernet_key: str = ""

    access_token_expire_minutes: int = 60 * 8
    lockout_threshold: int = 8
    lockout_minutes: int = 15
    # Set true when a TLS proxy (compose tls profile) fronts the app: enables HSTS.
    behind_tls: bool = False
    # Max request body (bytes). Problem + context payloads; also caps what reaches the budget guard.
    max_body_bytes: int = 2 * 1024 * 1024
    default_admin_username: str = "hive"
    default_admin_password: str = "change-on-first-login"

    # Comma separated. Never "*" with credentials.
    cors_origins: str = "http://127.0.0.1:50090,http://localhost:50090"

    # Expose /docs and /redoc only when explicitly enabled.
    enable_api_docs: bool = False

    # Hard global ceilings (USD). Per-session budgets live in the DB.
    global_daily_budget_usd: float = 10.0
    global_monthly_budget_usd: float = 100.0

    # Orchestrator defaults
    max_rounds: int = 3
    convergence_threshold: float = 0.92

    registry_path: Path = Path("/app/registry/providers.json")

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) < 32 or any(m in v.lower() for m in PLACEHOLDER_MARKERS):
            raise ValueError(
                "SECRET_KEY must be set to a random value of at least 32 characters. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return v

    @field_validator("token_fernet_key")
    @classmethod
    def _check_fernet(cls, v: str) -> str:
        v = (v or "").strip()
        try:
            raw = base64.urlsafe_b64decode(v.encode("ascii"))
        except (binascii.Error, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("TOKEN_FERNET_KEY is not valid urlsafe base64") from exc
        if len(raw) != 32:
            raise ValueError(
                "TOKEN_FERNET_KEY must be a 32-byte urlsafe-base64 Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
            )
        return v

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{self.data_dir / 'thehive.db'}"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip() and o.strip() != "*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
