"""Optional authentication: multi-tenant X-API-Key and/or HTTP Basic Auth + license."""

from __future__ import annotations

import base64
import logging
import os
import secrets
import threading

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from aduport.config import settings
from aduport.core.license import LicenseError, assert_license_ok
from aduport.core.request_context import set_request_context
from aduport.core.secure_compare import secure_compare
from aduport.core.tenants import load_tenant_keys, tenants_configured

_log = logging.getLogger("aduport.auth")

SESSION_COOKIE = "aduport_api_session"
_session_lock = threading.Lock()
# Fallback opaque sid → (api_key, expires) when no tenant registry (single-process).
_sessions: dict[str, tuple[str, float]] = {}
# Revoked signed JTIs until expiry (process-local; Redis when configured).
_revoked_jti: dict[str, float] = {}
_SESSION_TTL_SECONDS = float(os.environ.get("ADUPORT_SESSION_TTL_SECONDS") or 7 * 24 * 3600)
_SESSION_PREFIX = "s1."


def session_ttl_seconds() -> int:
    ttl = float(_SESSION_TTL_SECONDS)
    if ttl < 0:
        ttl = 0.0
    return int(ttl)


def _prune_sessions_locked(now: float | None = None) -> None:
    import time

    ts = time.time() if now is None else now
    dead = [sid for sid, (_, exp) in _sessions.items() if exp <= ts]
    for sid in dead:
        _sessions.pop(sid, None)
    dead_j = [j for j, exp in _revoked_jti.items() if exp <= ts]
    for j in dead_j:
        _revoked_jti.pop(j, None)


def _raw_session_secret() -> str:
    """Resolve a strong session secret (≥16) or empty. Never returns a hardcoded default."""
    from aduport.core.secrets import secret_plain

    raw = (os.environ.get("ADUPORT_SESSION_SECRET") or "").strip()
    if len(raw) >= 16:
        return raw
    cfg = secret_plain(getattr(settings, "session_secret", "")).strip()
    if len(cfg) >= 16:
        return cfg
    lic = secret_plain(settings.license_secret).strip()
    if len(lic) >= 16:
        return lic
    return ""


def session_hmac_configured() -> bool:
    """True when HMAC-signed cookies can be issued/verified safely."""
    return len(_raw_session_secret()) >= 16


def _session_hmac_secret() -> bytes | None:
    """HMAC key for signed cookies, or None when fail-closed (report v13 P1-1).

    Never falls back to a hardcoded string or api_keys material — those made
    forged ``s1.`` cookies trivial under default single-key deploys.
    """
    import hashlib

    raw = _raw_session_secret()
    if len(raw) < 16:
        return None
    return hashlib.sha256(f"aduport.session.v1|{raw}".encode("utf-8")).digest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign_payload(payload: dict[str, object]) -> str:
    import hashlib
    import hmac
    import json

    secret = _session_hmac_secret()
    if secret is None:
        raise RuntimeError("session HMAC secret not configured")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret, body, hashlib.sha256).digest()
    return f"{_SESSION_PREFIX}{_b64url(body)}.{_b64url(sig)}"


def _verify_signed(token: str) -> dict[str, object] | None:
    import hashlib
    import hmac
    import json

    secret = _session_hmac_secret()
    if secret is None:
        # Fail closed: reject all signed cookies when no strong secret is set.
        return None
    if not token.startswith(_SESSION_PREFIX):
        return None
    rest = token[len(_SESSION_PREFIX) :]
    if "." not in rest:
        return None
    body_b64, sig_b64 = rest.rsplit(".", 1)
    try:
        body = _b64url_decode(body_b64)
        sig = _b64url_decode(sig_b64)
    except Exception:
        return None
    expect = hmac.new(secret, body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _jti_revoked(jti: str) -> bool:
    if not jti:
        return True
    import time

    now = time.time()
    url = (settings.redis_url or "").strip()
    if url:
        try:
            from aduport.api.rate_limit import _get_redis_client

            r = _get_redis_client(url)
            if r.get(f"aduport:session:revoke:{jti}"):
                return True
        except Exception as exc:
            _log.debug("session revoke redis check failed: %s", exc)
    with _session_lock:
        exp = _revoked_jti.get(jti)
        if exp is None:
            return False
        if exp <= now:
            _revoked_jti.pop(jti, None)
            return False
        return True


def _revoke_jti(jti: str, exp: float) -> None:
    if not jti:
        return
    import time

    now = time.time()
    ttl = max(1, int(exp - now))
    url = (settings.redis_url or "").strip()
    if url:
        try:
            from aduport.api.rate_limit import _get_redis_client

            r = _get_redis_client(url)
            r.setex(f"aduport:session:revoke:{jti}", ttl, "1")
        except Exception as exc:
            _log.debug("session revoke redis set failed: %s", exc)
    with _session_lock:
        _prune_sessions_locked(now)
        _revoked_jti[jti] = exp


def issue_session(api_key: str) -> str:
    """Issue a session cookie value.

    Prefer HMAC-signed tenant-bound tokens when a strong secret is configured
    (multi-worker safe). Otherwise use opaque in-memory handles — never sign
    with a predictable fallback key (v13 P1-1).
    """
    import time

    ttl = float(_SESSION_TTL_SECONDS)
    if ttl < 0:
        ttl = 0.0
    exp = time.time() + ttl
    jti = secrets.token_urlsafe(18)

    tenant = _match_api_key(api_key) if api_key else None
    if tenant is not None and _session_hmac_secret() is not None:
        return _sign_payload(
            {
                "t": tenant.tenant_id,
                "e": int(exp),
                "j": jti,
            }
        )

    # Opaque handle (single-process / tests / no SESSION_SECRET).
    if tenant is not None and _session_hmac_secret() is None:
        _log.warning(
            "issuing opaque session (set ADUPORT_SESSION_SECRET≥16 for multi-worker HMAC)"
        )
    sid = secrets.token_urlsafe(32)
    with _session_lock:
        _prune_sessions_locked()
        _sessions[sid] = (api_key, exp)
    return sid


def resolve_session(sid: str) -> str:
    if not sid:
        return ""
    import time

    now = time.time()
    signed = _verify_signed(sid)
    if signed is not None:
        try:
            exp = float(signed.get("e") or 0)
        except (TypeError, ValueError):
            return ""
        if exp <= now:
            return ""
        jti = str(signed.get("j") or "")
        if _jti_revoked(jti):
            return ""
        tid = str(signed.get("t") or "").strip()
        if not tid:
            return ""
        from aduport.core.tenants import get_tenant

        tenant = get_tenant(tid)
        return tenant.api_key if tenant is not None else ""

    with _session_lock:
        item = _sessions.get(sid)
        if item is None:
            return ""
        api_key, exp = item
        if exp <= now:
            _sessions.pop(sid, None)
            return ""
        return api_key


def revoke_session(sid: str) -> None:
    if not sid:
        return
    signed = _verify_signed(sid)
    if signed is not None:
        try:
            exp = float(signed.get("e") or 0)
        except (TypeError, ValueError):
            exp = 0.0
        _revoke_jti(str(signed.get("j") or ""), exp)
        return
    with _session_lock:
        _sessions.pop(sid, None)


def _dev_ok() -> bool:
    """v6/T3-#17 / D-18: DEV_OK only bypasses auth when debug=True.

    Previously ``ADUPORT_DEV_OK=1`` alone disabled auth in production.
    """
    if not settings.debug:
        if (os.environ.get("ADUPORT_DEV_OK") or "").strip() in ("1", "true", "TRUE", "yes"):
            _log.warning("ADUPORT_DEV_OK ignored because ADUPORT_DEBUG is not true")
        return False
    return (os.environ.get("ADUPORT_DEV_OK") or "").strip() in ("1", "true", "TRUE", "yes")


def _public_path(path: str) -> bool:
    if path == "/":
        return True
    # D-27: /docs /redoc /openapi.json are public only when explicitly enabled.
    if settings.api_docs_enabled and (
        path.startswith("/docs")
        or path.startswith("/openapi")
        or path.startswith("/redoc")
    ):
        return True
    if path in ("/api/health", "/api/health/ready", "/api/license"):
        return True
    return False


def _basic_ok(header: str | None, expected: str) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header.split(" ", 1)[1].strip()).decode("utf-8")
    except Exception:
        return False
    return secure_compare(decoded, expected)


def _match_api_key(provided: str):
    """Constant-time scan across configured tenant keys."""
    if not provided:
        return None
    matched = None
    for item in load_tenant_keys():
        # Always compare every key to avoid short-circuit timing leaks
        ok = secure_compare(provided, item.api_key)
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


def _unauthorized(basic: str, keys_on: bool) -> JSONResponse:
    headers = {}
    if basic and not keys_on:
        headers["WWW-Authenticate"] = 'Basic realm="AduPort"'
    from aduport.core.i18n_errors import error_body

    return JSONResponse(
        status_code=401,
        content=error_body(
            "unauthorized",
            "缺少或无效的鉴权（X-API-Key 或 Basic Auth）",
            401,
        ),
        headers=headers,
    )


class APIKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # CORS preflight must never be blocked by auth (middleware-order safety net)
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        blocked = _license_gate(path)
        if blocked is not None:
            return blocked

        from aduport.core.secrets import secret_plain

        basic = secret_plain(settings.basic_auth).strip()
        keys_on = tenants_configured() > 0
        if not keys_on and not basic:
            # Fail closed unless explicitly disabled for local/dev/tests
            if settings.require_auth and not _dev_ok():
                if _public_path(path):
                    return await call_next(request)
                _log.warning("reject anonymous %s %s (require_auth, no credentials configured)", request.method, path)
                return _unauthorized("", False)
            request.state.tenant_id = None
            request.state.api_key_id = None
            set_request_context(tenant_id=None, client="")
            return await call_next(request)

        if _public_path(path):
            return await call_next(request)

        cookie_raw = request.cookies.get(SESSION_COOKIE, "") or ""
        provided_key = (
            request.headers.get("X-API-Key")
            or request.headers.get("x-api-key")
            or resolve_session(cookie_raw)
            or ""
        )
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
            return _unauthorized(basic, keys_on)
        set_request_context(
            tenant_id=getattr(request.state, "tenant_id", None),
            client=client_host,
        )
        return await call_next(request)
