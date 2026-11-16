"""HTTP-level tests for auth hardening and API-key scopes, using FastAPI's TestClient."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

from app import database
from app.database import Base


@pytest.fixture()
def client(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "engine", engine)
    monkeypatch.setattr(main_mod, "SessionLocal", database.SessionLocal)

    def override_db():
        db = database.SessionLocal()
        try:
            yield db
        finally:
            db.close()

    main_mod.app.dependency_overrides[database.get_db] = override_db
    with TestClient(main_mod.app) as c:
        yield c
    main_mod.app.dependency_overrides.clear()


def _login(c, pw="change-on-first-login"):
    return c.post("/api/v1/auth/login/json", json={"username": "hive", "password": pw})


def _admin(c):
    tok = _login(c).json()["access_token"]
    h = {"authorization": f"Bearer {tok}"}
    r = c.post(
        "/api/v1/auth/change-password",
        headers=h,
        json={"current_password": "change-on-first-login", "new_password": "Correct-Horse-Battery-9"},
    )
    assert r.status_code == 200
    return {"authorization": f"Bearer {r.json()['access_token']}"}


def test_public_health_only(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/health/detail").status_code == 401
    assert client.get("/docs").status_code == 404


def test_security_headers_and_body_limit(client):
    r = client.get("/api/v1/health")
    assert "Content-Security-Policy" in r.headers and r.headers["X-Frame-Options"] == "DENY"
    big = "x" * (3 * 1024 * 1024)
    r = client.post("/api/v1/auth/login/json", json={"username": "hive", "password": big})
    assert r.status_code == 413


def test_password_change_invalidates_old_token(client):
    old = {"authorization": f"Bearer {_login(client).json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=old).status_code == 200
    new = _admin(client)
    assert client.get("/api/v1/auth/me", headers=old).status_code == 401
    assert client.get("/api/v1/auth/me", headers=new).status_code == 200


def test_logout_revokes_token(client):
    h = _admin(client)
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 204
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_api_key_scopes(client):
    h = _admin(client)
    r = client.post("/api/v1/auth/api-keys", headers=h, json={"name": "ci", "scopes": ["read"], "expires_days": 30})
    assert r.status_code == 201
    key = r.json()["key"]
    assert key.startswith("hive_sk_")
    kh = {"X-API-Key": key}
    # read ok
    assert client.get("/api/v1/hive/sessions", headers=kh).status_code == 200
    assert client.get("/api/v1/hive/budget", headers=kh).status_code == 200
    # run forbidden
    r = client.post("/api/v1/hive/sessions", headers=kh, json={"problem": "x"})
    assert r.status_code == 403
    # keys never manage users/keys
    assert client.get("/api/v1/auth/users", headers=kh).status_code == 401
    assert client.get("/api/v1/auth/api-keys", headers=kh).status_code == 401
    # revoke -> dead
    kid = r.json() and client.get("/api/v1/auth/api-keys", headers=h).json()[0]["id"]
    assert client.post(f"/api/v1/auth/api-keys/{kid}/revoke", headers=h).status_code == 200
    assert client.get("/api/v1/hive/sessions", headers=kh).status_code == 401


def test_api_key_admin_scope_manages_seats_but_not_users(client):
    h = _admin(client)
    key = client.post("/api/v1/auth/api-keys", headers=h, json={"name": "ops", "scopes": ["admin", "run"]}).json()[
        "key"
    ]
    kh = {"X-API-Key": key}
    r = client.post("/api/v1/providers/tokens", headers=kh, json={"provider": "openai", "value": "sk-test-1234567890"})
    assert r.status_code == 201
    assert r.json()["masked_value"].startswith("sk-t")
    assert client.get("/api/v1/auth/users", headers=kh).status_code == 401


def test_bad_or_malformed_key(client):
    assert client.get("/api/v1/hive/sessions", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/api/v1/hive/sessions", headers={"X-API-Key": "hive_sk_deadbeef_zzz"}).status_code == 401


def test_stream_requires_auth_and_ends_on_terminal(client):
    h = _admin(client)
    assert client.get("/api/v1/hive/sessions/1/stream").status_code == 401
    assert client.get("/api/v1/hive/sessions/999/stream", headers=h).status_code == 404


def test_estimate_export_presets(client):
    h = _admin(client)
    # two seats via API
    for prov, val, model in (
        ("openai", "sk-test-1234567890", "gpt-4o-mini"),
        ("anthropic", "sk-ant-test-123456", "claude-haiku-4-5-20251001"),
    ):
        tid = client.post("/api/v1/providers/tokens", headers=h, json={"provider": prov, "value": val}).json()["id"]
        client.post("/api/v1/providers/members", headers=h, json={"token_id": tid, "model": model})
    est = client.post("/api/v1/hive/estimate", headers=h, json={"problem": "x" * 400, "max_rounds": 3}).json()
    assert est["worst_case_usd"] > 0 and est["calls"] == 2 + 2 * (2 + 2) + 1  # answers + 2 rounds*(crit+revise) + judge
    assert est["phases"][0]["phase"] == "answer"
    # preset round-trip
    ids = [m["id"] for m in client.get("/api/v1/providers/members", headers=h).json()]
    r = client.post("/api/v1/hive/presets", headers=h, json={"name": "cheap", "member_ids": ids, "budget_usd": 0.5})
    assert r.status_code == 201 and r.json()["member_ids"] == ids
    assert client.get("/api/v1/hive/presets", headers=h).json()[0]["name"] == "cheap"
    # export of a (quickly failing) session
    sid = client.post("/api/v1/hive/sessions", headers=h, json={"problem": "Is 7 prime?", "budget_usd": 0.2}).json()[
        "id"
    ]
    import time

    for _ in range(40):
        if client.get(f"/api/v1/hive/sessions/{sid}", headers=h).json()["status"] in ("done", "halted", "error"):
            break
        time.sleep(0.25)
    md = client.get(f"/api/v1/hive/sessions/{sid}/export.md", headers=h)
    assert md.status_code == 200 and md.text.startswith("# Is 7 prime?") and "## Transcript" in md.text
    assert "attachment" in md.headers["content-disposition"]


def test_lockout_is_indistinguishable_from_bad_password(client):
    for _ in range(8):
        assert _login(client, "wrong-wrong-wrong").status_code == 401
    r = _login(client)  # correct password, but locked
    assert r.status_code == 401 and r.json()["detail"] == "Invalid credentials"


def test_context_rate_limit(client):
    from app.services import ratelimit

    ratelimit.reset()
    h = _admin(client)
    codes = [
        client.post(
            "/api/v1/context/scholar", headers=h, json={"query": "zzz-no-such-topic-zzz", "limit": 1}
        ).status_code
        for _ in range(31)
    ]
    assert codes[-1] == 429 and all(c in (200, 429) for c in codes)
    ratelimit.reset()


def test_outbound_allowlist(monkeypatch):
    from app.services.context import UnsafeURL, _check

    monkeypatch.setenv("OUTBOUND_ALLOWED_HOSTS", "example.org")
    with pytest.raises(UnsafeURL, match="OUTBOUND_ALLOWED_HOSTS"):
        _check("https://example.com/")
