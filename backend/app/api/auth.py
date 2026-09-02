"""Optional authentication: multi-tenant X-API-Key and/or HTTP Basic Auth + license."""

from __future__ import annotations

import base64
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.core.license import LicenseError, assert_license_ok
from app.core.request_context import set_request_context
from app.core.tenants import load_tenant_keys, tenants_configured


def _public_path(path: str) -> bool:
    if path == "/" or path.startswith("/docs") or path.startswith("/openapi") or path.startswith("/redoc"):
        return True
    if path in ("/api/health", "/api/license"):
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


def _match_api_key(provided: str):
    """Constant-time scan across configured tenant keys."""
    if not provided:
        return None
    matched = None
    for item in load_tenant_keys():
        # Always compare every key to avoid short-circuit timing leaks
        ok = hmac.compare_digest(provided, item.api_key)
        if ok and matched is None:
            matched = item
    return matched


def _license_gate(path: str) -> JSONResponse | None:
    if _public_path(path):
        return None
    try:
        assert_license_ok()
    except LicenseError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
    return None


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        blocked = _license_gate(path)
        if blocked is not None:
            return blocked

        basic = (settings.basic_auth or "").strip()
        keys_on = tenants_configured() > 0
        if not keys_on and not basic:
            request.state.tenant_id = None
            request.state.api_key_id = None
            set_request_context(tenant_id=None, client="")
            return await call_next(request)

        if _public_path(path):
            return await call_next(request)

        provided_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key") or ""
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        client_host = request.client.host if request.client else ""

        tenant = _match_api_key(provided_key) if keys_on else None
        ok = tenant is not None
        if basic and _basic_ok(auth_header, basic):
            ok = True
            if tenant is None:
                request.state.tenant_id = "basic"
                request.state.api_key_id = "basic"
        elif tenant is not None:
            request.state.tenant_id = tenant.tenant_id
            request.state.api_key_id = tenant.tenant_id
            request.state.tenant_rate_limit = tenant.rate_limit
            request.state.tenant_daily_quota = tenant.daily_quota
        else:
            request.state.tenant_id = None
            request.state.api_key_id = None

        if not ok:
            headers = {}
            if basic and not keys_on:
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
        set_request_context(
            tenant_id=getattr(request.state, "tenant_id", None),
            client=client_host,
        )
        return await call_next(request)
