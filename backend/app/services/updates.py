"""Update awareness. Checks GitHub for a newer version; never applies anything itself.

Why not auto-apply: this container holds provider API keys. Code that updates itself from a
remote on a timer is a supply-chain hole. The app tells you; you (or Watchtower, if you opt in
knowingly) apply.

Channels:
  release  -> compares VERSION against the latest GitHub release tag (vX.Y.Z)   [default]
  <branch> -> compares the build's git sha against the branch head (pre-prod / main)
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger("thehive.updates")

REPO = "SyphenAI/TheHive"
API = "https://api.github.com"
CACHE_TTL = 3600  # 1h; GitHub unauthenticated limit is 60 req/h


@dataclass
class UpdateStatus:
    enabled: bool
    channel: str
    current_version: str
    current_sha: str
    latest_version: str | None = None
    latest_sha: str | None = None
    update_available: bool = False
    release_url: str | None = None
    notes: str | None = None
    checked_at: float | None = None
    error: str | None = None


_cache: UpdateStatus | None = None
_lock = threading.Lock()


def current_version() -> str:
    for p in (
        Path(os.environ.get("VERSION_FILE", "")),
        Path("/app/VERSION"),
        Path(__file__).resolve().parents[3] / "VERSION",
    ):
        try:
            if p and p.is_file():
                return p.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    return "0.0.0-dev"


def current_sha() -> str:
    return os.environ.get("GIT_SHA", "")[:12]


def _parse(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v)
    return tuple(int(n) for n in nums[:3]) or (0,)


def _fetch(path: str) -> dict | list:
    headers = {"accept": "application/vnd.github+json", "user-agent": f"TheHive/{current_version()}"}
    tok = os.environ.get("GITHUB_TOKEN")  # optional; raises rate limit and allows private repo
    if tok:
        headers["authorization"] = f"Bearer {tok}"
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{API}{path}", headers=headers)
    r.raise_for_status()
    return r.json()


def check(force: bool = False) -> UpdateStatus:
    global _cache
    cfg = get_settings()
    now = time.time()
    with _lock:
        if _cache and not force and _cache.checked_at and now - _cache.checked_at < CACHE_TTL:
            return _cache
        st = UpdateStatus(
            enabled=cfg.update_check_enabled,
            channel=cfg.update_channel,
            current_version=current_version(),
            current_sha=current_sha(),
            checked_at=now,
        )
        if not cfg.update_check_enabled:
            _cache = st
            return st
        try:
            if cfg.update_channel == "release":
                rel = _fetch(f"/repos/{REPO}/releases/latest")
                st.latest_version = str(rel.get("tag_name", "")).lstrip("v")
                st.release_url = rel.get("html_url")
                st.notes = (rel.get("body") or "")[:2000]
                st.update_available = _parse(st.latest_version) > _parse(st.current_version)
            else:
                head = _fetch(f"/repos/{REPO}/commits/{cfg.update_channel}")
                st.latest_sha = str(head.get("sha", ""))[:12]
                st.notes = (head.get("commit", {}).get("message") or "")[:500]
                st.release_url = head.get("html_url")
                st.update_available = bool(st.current_sha and st.latest_sha and st.current_sha != st.latest_sha)
        except httpx.HTTPStatusError as exc:
            st.error = f"GitHub returned {exc.response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            st.error = f"{type(exc).__name__}: {exc}"[:200]
        _cache = st
        return st


def as_dict(st: UpdateStatus) -> dict:
    return asdict(st)
