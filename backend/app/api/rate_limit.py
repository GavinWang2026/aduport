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
_redis_client = None
_redis_url: str | None = None
_redis_lock = __import__("threading").Lock()


def _get_redis_client(url: str):
    """Module-level Redis singleton (avoids per-request connect leak)."""
    global _redis_client, _redis_url
    with _redis_lock:
        if _redis_client is not None and _redis_url == url:
            return _redis_client
        import redis  # type: ignore

        if _redis_client is not None:
            try:
                _redis_client.close()
            except Exception:
                pass
        _redis_client = redis.from_url(
            url, socket_connect_timeout=0.4, socket_timeout=0.4
        )
        _redis_url = url
        return _redis_client


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
        # v6/T3-#18 / D-19: honour X-Forwarded-For only for configured proxy hops.
        hops = int(getattr(settings, "trusted_proxy_hops", 0) or 0)
        if hops > 0:
            xff = (request.headers.get("x-forwarded-for") or "").strip()
            if xff:
                parts = [p.strip() for p in xff.split(",") if p.strip()]
                if len(parts) >= hops:
                    client = parts[-hops]
                elif parts:
                    client = parts[0]
        tenant_id = getattr(request.state, "tenant_id", None)
        bucket = f"tenant:{tenant_id}" if tenant_id else f"ip:{client}"
        tenant_limit = getattr(request.state, "tenant_rate_limit", None)
        effective = int(tenant_limit) if tenant_limit else limit

        # Daily conversion quota (tenant JSON daily_quota)
        daily_quota = getattr(request.state, "tenant_daily_quota", None)
        if tenant_id and daily_quota is not None:
            try:
                q = int(daily_quota)
            except (TypeError, ValueError):
                q = 0
            if q > 0:
                from app.core.metrics import metrics as _metrics

                used = _metrics.tenant_daily_count(str(tenant_id))
                if used >= q:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": True,
                            "code": "daily_quota_exceeded",
                            "message": f"租户日配额已用尽：{used}/{q}",
                            "status_code": 429,
                            "tenant_id": tenant_id,
                        },
                    )

        redis_blocked = self._redis_check(bucket, effective)
        if redis_blocked is True:
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "code": "rate_limited",
                    "message": f"请求过于频繁，每分钟最多 {effective} 次",
                    "status_code": 429,
                    "tenant_id": tenant_id,
                },
            )
        if redis_blocked is False:
            return await call_next(request)

        now = time.time()
        window = self._hits[bucket]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= effective:
            return JSONResponse(
                status_code=429,
                content={
                    "error": True,
                    "code": "rate_limited",
                    "message": f"请求过于频繁，每分钟最多 {effective} 次",
                    "status_code": 429,
                    "tenant_id": tenant_id,
                },
            )
        window.append(now)
        if len(self._hits) > _MAX_CLIENT_KEYS // 2 and len(window) == 1:
            self._prune(now)
        return await call_next(request)

    def _redis_check(self, bucket: str, effective: int) -> bool | None:
        """Return True if limited, False if allowed via Redis, None to use memory."""
        url = (settings.redis_url or "").strip()
        if not url:
            return None
        try:
            r = _get_redis_client(url)
            key = f"mdbridge:rl:{bucket}"
            n = int(r.incr(key))
            if n == 1:
                r.expire(key, 60)
            return n > effective
        except Exception:
            return None


def reset_rate_limits() -> None:
    if _active is not None:
        _active.reset()
    global _redis_client, _redis_url
    with _redis_lock:
        if _redis_client is not None:
            try:
                _redis_client.close()
            except Exception:
                pass
        _redis_client = None
        _redis_url = None