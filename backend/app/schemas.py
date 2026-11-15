from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


# ---- auth ----
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    scopes: list[str] = ["read"]
    expires_days: int | None = Field(default=90, ge=1, le=3650)


class ApiKeyOut(BaseModel):
    id: int
    name: str
    prefix: str
    scopes: str
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyOut):
    key: str  # plaintext, returned exactly once


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = ""
    password: str = Field(min_length=12, max_length=256)
    role: str = "member"


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    is_active: bool
    must_change_password: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---- provider tokens ----
class TokenCreate(BaseModel):
    provider: str
    label: str = "default"
    value: str = Field(min_length=8)
    base_url_override: str = ""


class TokenOut(BaseModel):
    id: int
    provider: str
    label: str
    masked_value: str
    base_url_override: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None
    members: list[MemberOut] = []


class TokenTestResult(BaseModel):
    ok: bool
    latency_ms: int | None = None
    message: str = ""
    models_seen: list[str] = []


# ---- hive members ----
class MemberCreate(BaseModel):
    token_id: int
    model: str = Field(min_length=1, max_length=96)
    nickname: str = ""
    can_answer: bool = True
    can_critique: bool = True
    can_judge: bool = True
    can_redteam: bool = False
    max_cost_per_call_usd: float = 0.0


class MemberUpdate(BaseModel):
    model: str | None = None
    nickname: str | None = None
    enabled: bool | None = None
    can_answer: bool | None = None
    can_critique: bool | None = None
    can_judge: bool | None = None
    can_redteam: bool | None = None
    max_cost_per_call_usd: float | None = None


class MemberOut(BaseModel):
    id: int
    token_id: int
    provider: str
    model: str
    nickname: str
    enabled: bool
    can_answer: bool
    can_critique: bool
    can_judge: bool
    can_redteam: bool = False
    max_cost_per_call_usd: float

    model_config = {"from_attributes": True}


# ---- hive sessions ----
class SessionCreate(BaseModel):
    title: str = ""
    problem: str = Field(min_length=1)
    context: str = ""
    task_type: str = "general"
    max_rounds: int = Field(default=3, ge=1, le=5)
    budget_usd: float = Field(default=1.0, gt=0, le=1000)
    member_ids: list[int] | None = None  # None = all enabled members
    judge_member_id: int | None = None  # None = rotate


class TurnOut(BaseModel):
    id: int
    round_no: int
    phase: str
    member_id: int
    alias: str
    content: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int
    error: str
    state: str = "done"
    created_at: datetime
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: int
    title: str
    problem: str
    context: str
    task_type: str
    status: str
    max_rounds: int
    budget_usd: float
    spent_usd: float
    judge_member_id: int | None
    verdict: dict | None
    halt_reason: str
    created_at: datetime
    finished_at: datetime | None
    turns: list[TurnOut] = []

    model_config = {"from_attributes": True}


class SessionSummary(BaseModel):
    id: int
    title: str
    task_type: str
    status: str
    spent_usd: float
    budget_usd: float
    created_at: datetime

    model_config = {"from_attributes": True}


class PresetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    member_ids: list[int]
    judge_member_id: int | None = None
    task_type: str = "general"
    max_rounds: int = Field(default=3, ge=1, le=5)
    budget_usd: float = Field(default=1.0, gt=0, le=1000)


class PresetOut(PresetIn):
    id: int

    model_config = {"from_attributes": True}


class EstimateRequest(BaseModel):
    problem: str = ""
    context: str = ""
    max_rounds: int = Field(default=3, ge=1, le=5)
    member_ids: list[int] | None = None


# ---- misc ----
class BudgetStatus(BaseModel):
    daily_spent_usd: float
    daily_limit_usd: float
    monthly_spent_usd: float
    monthly_limit_usd: float


class HealthOut(BaseModel):
    status: str
    version: str
    app_env: str
    checks: dict


class KillSwitchResponse(BaseModel):
    ok: bool
    removed_tokens: int
    message: str
