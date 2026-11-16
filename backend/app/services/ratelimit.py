"""In-memory sliding-window rate limiter. Good enough for a single-process local deployment.

Keys are "<bucket>:<principal or ip>". State is lost on restart, which is fine: the limits exist to
stop a leaked `run` key or a runaway script from turning the container into a fetch proxy, not to
meter paying customers.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from fastapi import HTTPException

_hits: dict[str, deque[float]] = {}
_lock = threading.Lock()


def check(bucket: str, key: str, limit: int, window_s: int) -> None:
    """Raise 429 if more than `limit` calls in the last `window_s` seconds for this key."""
    now = time.monotonic()
    k = f"{bucket}:{key}"
    with _lock:
        q = _hits.setdefault(k, deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            retry = int(window_s - (now - q[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit: {limit} per {window_s}s for {bucket}; retry in {retry}s",
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        # Opportunistic cleanup so the dict can't grow without bound.
        if len(_hits) > 5000:
            for stale in [kk for kk, qq in _hits.items() if not qq or now - qq[-1] > 3600]:
                _hits.pop(stale, None)


def reset() -> None:
    with _lock:
        _hits.clear()
