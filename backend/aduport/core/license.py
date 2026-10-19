"""Enterprise license: machine-bound key with expiry (HMAC, not DRM-grade).

Payload (JSON) fields:
  - mid: machine id (fingerprint)
  - exp: ISO date YYYY-MM-DD (inclusive) or empty = no expiry
  - seats: int
  - customer: str
  - product: AduPort

Token format: base64url(payload).base64url(hmac_sha256(secret, payload))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from aduport.config import settings
from aduport.core.exceptions import AduPortError

_log = logging.getLogger("aduport.license")

_license_seats_lock = threading.Lock()
_license_seats_sem: threading.Semaphore | None = None
_license_seats_limit = 0


class LicenseError(AduPortError):
    code = "license_invalid"
    status_code = 403


@dataclass(frozen=True)
class LicenseInfo:
    machine_id: str
    expires: str
    seats: int
    customer: str
    product: str
    valid: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Full dict including machine fingerprints (CLI / internal only)."""
        return {
            "machine_id": self.machine_id,
            "expires": self.expires,
            "seats": self.seats,
            "customer": self.customer,
            "product": self.product,
            "valid": self.valid,
            "reason": self.reason,
            "host_machine_id": machine_id(),
        }

    def public_dict(self) -> dict[str, object]:
        """API-safe dict — no machine_id / host_machine_id (T23)."""
        return {
            "expires": self.expires,
            "seats": self.seats,
            "customer": self.customer,
            "product": self.product,
            "valid": self.valid,
            "reason": self.reason,
        }


def _b64url_encode(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    import base64

    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def machine_id() -> str:
    """Stable-ish host fingerprint for one-machine-one-key binding."""
    parts = [
        platform.node(),
        platform.system(),
        platform.machine(),
        str(uuid.getnode()),
    ]
    # Prefer /etc/machine-id when present (Linux containers/VMs)
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            if candidate.is_file():
                parts.append(candidate.read_text(encoding="utf-8").strip())
                break
        except OSError:
            pass
    override = (os.environ.get("ADUPORT_MACHINE_ID") or "").strip()
    if override:
        if settings.debug:
            parts.insert(0, override)
        else:
            _log.warning("ADUPORT_MACHINE_ID ignored unless ADUPORT_DEBUG=true")
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


_ephemeral_lock = threading.Lock()
_ephemeral_secret: bytes | None = None


def _secret_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# SHA-256 of known placeholders. Plaintext is not stored, so it cannot be used as a signing key.
_WEAK_SECRET_SHA256 = frozenset(
    {
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "e2186dbdb1bb4193608605e84f33208765b5693b55edd4f730a719a100eeea6f",
        "aa2f65175ec7f11be8d9ddc895547f06226660c485c85c410c3424de6f9db394",
        "c10d4ea28ff52d55ed9d438cf0a85d87881897501178ee8a863501f12b3b78d0",
        # 改名后的同名占位符：md-bridge → AduPort / aduport。三份都要在表内，
        # 否则改名本身就把黑名单绕过去了（曾实测：测试红、占位符可用作签发密钥）。
        "bb262850854567858a9b8910160f073da6f229555989b63b36f14a159efaa8ed",  # AduPort-...
        "ecac84e32c05ad58019370f6fa34ccf6e49df0d49db10cae7a23f54e4db8aafe",  # aduport-...
    }
)


def _secret_is_weak(raw: str) -> bool:
    text = (raw or "").strip()
    if len(text) < 16:
        return True
    if _secret_digest(text) in _WEAK_SECRET_SHA256:
        return True
    # 结构判据兜底：精确摘要表挡不住“下次再改名”或客户手填的变体。
    lowered = text.lower()
    return "change-me" in lowered or "changeme" in lowered or "placeholder" in lowered


def _signing_secret() -> bytes:
    from aduport.core.secrets import secret_plain

    raw = secret_plain(settings.license_secret).strip()
    if _secret_digest(raw) in _WEAK_SECRET_SHA256:
        raw = ""
    if not raw:
        if settings.license_required:
            raise LicenseError(
                "企业授权要求配置 ADUPORT_LICENSE_SECRET（≥16 字符，禁止占位符）",
                code="license_secret_missing",
                status_code=403,
            )
        global _ephemeral_secret
        with _ephemeral_lock:
            if _ephemeral_secret is None:
                _ephemeral_secret = secrets.token_bytes(32)
            return _ephemeral_secret
    return raw.encode("utf-8")


def issue_license(
    *,
    machine_id_value: str,
    expires: str = "",
    seats: int = 1,
    customer: str = "",
    product: str = "AduPort",
) -> str:
    mid = (machine_id_value or "").strip()
    if len(mid) < 8:
        raise LicenseError("machine_id 过短，拒绝签发")
    if expires:
        try:
            date.fromisoformat(expires)
        except ValueError as exc:
            raise LicenseError(f"expires 必须是 YYYY-MM-DD: {expires}") from exc
    payload = {
        "mid": mid,
        "exp": expires,
        "seats": max(1, int(seats)),
        "customer": customer or "",
        "product": product or "AduPort",
        "iat": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    body_b = body.encode("utf-8")
    sig = hmac.new(_signing_secret(), body_b, hashlib.sha256).digest()
    return f"{_b64url_encode(body_b)}.{_b64url_encode(sig)}"


def _load_token_raw() -> str:
    from aduport.core.secrets import secret_plain

    env = secret_plain(settings.license_key).strip()
    if env:
        return env
    path = (settings.license_file or "").strip()
    if path:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    return ""


def parse_and_verify(token: str) -> LicenseInfo:
    token = (token or "").strip()
    if not token or "." not in token:
        return LicenseInfo("", "", 0, "", "AduPort", False, "missing_token")
    left, right = token.split(".", 1)
    try:
        body = _b64url_decode(left)
        sig = _b64url_decode(right)
    except Exception:
        return LicenseInfo("", "", 0, "", "AduPort", False, "malformed_token")
    expect = hmac.new(_signing_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return LicenseInfo("", "", 0, "", "AduPort", False, "bad_signature")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return LicenseInfo("", "", 0, "", "AduPort", False, "bad_payload")
    mid = str(data.get("mid") or "")
    exp = str(data.get("exp") or "")
    seats = int(data.get("seats") or 1)
    customer = str(data.get("customer") or "")
    product = str(data.get("product") or "AduPort")
    host = machine_id()
    if mid and mid != host:
        return LicenseInfo(mid, exp, seats, customer, product, False, "machine_mismatch")
    if exp:
        try:
            if date.today() > date.fromisoformat(exp):
                return LicenseInfo(mid, exp, seats, customer, product, False, "expired")
        except ValueError:
            return LicenseInfo(mid, exp, seats, customer, product, False, "bad_expiry")
    return LicenseInfo(mid, exp, seats, customer, product, True, "ok")


def _ensure_seats_sem(limit: int) -> threading.Semaphore:
    global _license_seats_sem, _license_seats_limit
    n = max(1, int(limit or 1))
    with _license_seats_lock:
        if _license_seats_sem is None or _license_seats_limit != n:
            _license_seats_sem = threading.Semaphore(n)
            _license_seats_limit = n
        return _license_seats_sem


def _redis_seat_key(info: LicenseInfo) -> str:
    cust = (info.customer or "default").replace(" ", "_")[:64]
    return f"aduport:license:seats:{cust}"


def _redis_acquire_seat(info: LicenseInfo) -> bool | None:
    """Return True if acquired, False if full, None if Redis unavailable."""
    url = (settings.redis_url or "").strip()
    if not url:
        return None
    try:
        from aduport.api.rate_limit import _get_redis_client

        r = _get_redis_client(url)
        key = _redis_seat_key(info)
        n = int(r.incr(key))
        if n == 1:
            r.expire(key, 86400)
        if n > max(1, int(info.seats or 1)):
            r.decr(key)
            return False
        return True
    except Exception as exc:
        _log.debug("license seat redis unavailable, using process semaphore: %s", exc)
        return None


def _redis_release_seat(info: LicenseInfo | None = None) -> None:
    url = (settings.redis_url or "").strip()
    if not url:
        return
    try:
        from aduport.api.rate_limit import _get_redis_client

        r = _get_redis_client(url)
        # Best-effort: decrement shared counter (floor at 0).
        key = _redis_seat_key(info) if info is not None else "aduport:license:seats:default"
        n = int(r.decr(key))
        if n < 0:
            r.set(key, 0)
    except Exception as exc:
        _log.debug("license seat redis release failed: %s", exc)


# Track whether the current acquire used Redis (thread-local) so release matches.
_seat_backend = threading.local()


def acquire_license_seat(info: LicenseInfo) -> None:
    """Enforce license seats as concurrent conversion slots (P2-3 / P3-3).

    When ``ADUPORT_REDIS_URL`` is set, seats are shared across workers via Redis.
    Otherwise a process-local Semaphore is used (document multi-worker limitation).
    """
    if not settings.license_required or not info.valid:
        return
    redis_ok = _redis_acquire_seat(info)
    if redis_ok is True:
        _seat_backend.redis = True
        _seat_backend.info = info
        return
    if redis_ok is False:
        raise LicenseError(
            f"授权并发席位已满（{info.seats}）",
            code="license_seats_exceeded",
            status_code=403,
        )
    _seat_backend.redis = False
    sem = _ensure_seats_sem(info.seats)
    if not sem.acquire(blocking=False):
        raise LicenseError(
            f"授权并发席位已满（{info.seats}）",
            code="license_seats_exceeded",
            status_code=403,
        )


def release_license_seat() -> None:
    if getattr(_seat_backend, "redis", False):
        _redis_release_seat(getattr(_seat_backend, "info", None))
        _seat_backend.redis = False
        return
    sem = _license_seats_sem
    if sem is not None:
        try:
            sem.release()
        except ValueError:
            pass


@contextmanager
def license_seat():
    """Acquire one license seat for the duration of a conversion (P2-3)."""
    if not settings.license_required:
        yield
        return
    info = current_license()
    if info is None or not info.valid:
        yield
        return
    acquire_license_seat(info)
    try:
        yield
    finally:
        release_license_seat()


def current_license() -> LicenseInfo | None:
    """Return license info when enforcement is on; None when license not required."""
    if not settings.license_required:
        tok = _load_token_raw()
        if not tok:
            return None
        return parse_and_verify(tok)
    tok = _load_token_raw()
    if not tok:
        return LicenseInfo("", "", 0, "", "AduPort", False, "missing_token")
    return parse_and_verify(tok)


def assert_license_ok() -> LicenseInfo | None:
    """Raise LicenseError when required and invalid."""
    if not settings.license_required:
        info = current_license()
        if info and not info.valid:
            _log.warning("license present but invalid (%s) — enforcement off", info.reason)
        return info
    from aduport.core.secrets import secret_plain

    secret = secret_plain(settings.license_secret).strip()
    if _secret_is_weak(secret):
        raise LicenseError(
            "企业授权要求配置高强度 ADUPORT_LICENSE_SECRET（≥16 字符，禁止占位符）",
            code="license_secret_weak",
            status_code=403,
        )
    info = current_license()
    if info is None or not info.valid:
        reason = info.reason if info else "missing_token"
        raise LicenseError(
            f"企业授权无效（{reason}）。请配置 ADUPORT_LICENSE_KEY 或 LICENSE 文件。",
            code="license_invalid",
            status_code=403,
        )
    return info
