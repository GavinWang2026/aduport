"""Simple fixed-window rate limiter middleware."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

_active: "RateLimitMiddleware | None" = None
_MAX_CLIENT_KEYS = 10_000


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        global _active
        _active = self

    def reset(self) -> None:
        self._hits.clear()

    def _prune(self, now: float) -> None:
        """Drop empty windows and cap key count to bound memory."""
        stale = [k for k, w in self._hits.items() if not w or now - w[-1] > 120]
        for k in stale:
            del self._hits[k]
        if len(self._hits) > _MAX_CLIENT_KEYS:
            # Evict oldest by last hit
            ordered = sorted(self._hits.items(), key=lambda kv: kv[1][-1] if kv[1] else 0)
            overflow = len(self._hits) - _MAX_CLIENT_KEYS
            for k, _ in ordered[:overflow]:
                self._hits.pop(k, None)

    async def dispatch(self, request: Request, call_next) -> Response:
        limit = settings.rate_limit_per_minute
        if limit <= 0:
            return await call_next(request)
        path = request.url.path
        if not (
            path.startswith("/api/convert")
            or (path == "/api/jobs" and request.method == "POST")
        ):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = time.time()
        window = self._hits[client]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "code": "rate_limited",
                    "message": f"请求过于频繁，每分钟最多 {limit} 次",
                    "status_code": 429,
                },
            )
        window.append(now)
        if len(self._hits) > _MAX_CLIENT_KEYS // 2 and len(window) == 1:
            self._prune(now)
        return await call_next(request)


def reset_rate_limits() -> None:
    if _active is not None:
        _active.reset()
