"""Optional authentication: X-API-Key and/or HTTP Basic Auth."""

from __future__ import annotations

import base64
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings


def _public_path(path: str) -> bool:
    if path == "/" or path.startswith("/docs") or path.startswith("/openapi") or path.startswith("/redoc"):
        return True
    if path == "/api/health":
        return True
    return False


def _basic_ok(header: str | None, expected: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
    except Exception:
        return False
    return hmac.compare_digest(decoded, expected)


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        api_key = (settings.api_key or "").strip()
        basic = (settings.basic_auth or "").strip()
        if not api_key and not basic:
            return await call_next(request)

        path = request.url.path
        if _public_path(path):
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key") or ""
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")

        ok = False
        if api_key and provided_key and hmac.compare_digest(provided_key, api_key):
            ok = True
        if basic and _basic_ok(auth_header, basic):
            ok = True
        # If only one method configured, the other alone cannot pass (handled above).
        # If both configured, either is enough.

        if not ok:
            headers = {}
            if basic and not api_key:
                headers["WWW-Authenticate"] = 'Basic realm="md-bridge"'
            return JSONResponse(
                status_code=401,
                content={
                    "error": True,
                    "code": "unauthorized",
                    "message": "缺少或无效的鉴权（X-API-Key 或 Basic Auth）",
                    "status_code": 401,
                },
                headers=headers,
            )
        return await call_next(request)
