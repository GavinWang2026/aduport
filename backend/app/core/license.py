"""Enterprise license: machine-bound key with expiry (HMAC, not DRM-grade).

Payload (JSON) fields:
  - mid: machine id (fingerprint)
  - exp: ISO date YYYY-MM-DD (inclusive) or empty = no expiry
  - seats: int
  - customer: str
  - product: md-bridge

Token format: base64url(payload).base64url(hmac_sha256(secret, payload))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import settings
from app.core.exceptions import MdBridgeError

_log = logging.getLogger("mdbridge.license")


class LicenseError(MdBridgeError):
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
    override = (os.environ.get("MDBRIDGE_MACHINE_ID") or "").strip()
    if override:
        parts.insert(0, override)
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _signing_secret() -> bytes:
    raw = (settings.license_secret or "").strip()
    if not raw:
        # Deterministic fallback — enterprises should set MDBRIDGE_LICENSE_SECRET
        raw = "md-bridge-dev-license-secret-change-me"
    return raw.encode("utf-8")


def issue_license(
    *,
    machine_id_value: str,
    expires: str = "",
    seats: int = 1,
    customer: str = "",
    product: str = "md-bridge",
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
        "product": product or "md-bridge",
        "iat": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    body_b = body.encode("utf-8")
    sig = hmac.new(_signing_secret(), body_b, hashlib.sha256).digest()
    return f"{_b64url_encode(body_b)}.{_b64url_encode(sig)}"


def _load_token_raw() -> str:
    env = (settings.license_key or "").strip()
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
        return LicenseInfo("", "", 0, "", "md-bridge", False, "missing_token")
    left, right = token.split(".", 1)
    try:
        body = _b64url_decode(left)
        sig = _b64url_decode(right)
    except Exception:
        return LicenseInfo("", "", 0, "", "md-bridge", False, "malformed_token")
    expect = hmac.new(_signing_secret(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expect):
        return LicenseInfo("", "", 0, "", "md-bridge", False, "bad_signature")
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return LicenseInfo("", "", 0, "", "md-bridge", False, "bad_payload")
    mid = str(data.get("mid") or "")
    exp = str(data.get("exp") or "")
    seats = int(data.get("seats") or 1)
    customer = str(data.get("customer") or "")
    product = str(data.get("product") or "md-bridge")
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


def current_license() -> LicenseInfo | None:
    """Return license info when enforcement is on; None when license not required."""
    if not settings.license_required:
        tok = _load_token_raw()
        if not tok:
            return None
        return parse_and_verify(tok)
    tok = _load_token_raw()
    if not tok:
        return LicenseInfo("", "", 0, "", "md-bridge", False, "missing_token")
    return parse_and_verify(tok)


def assert_license_ok() -> LicenseInfo | None:
    """Raise LicenseError when required and invalid."""
    if not settings.license_required:
        info = current_license()
        if info and not info.valid:
            _log.warning("license present but invalid (%s) — enforcement off", info.reason)
        return info
    secret = (settings.license_secret or "").strip()
    weak = {
        "",
        "md-bridge-dev-license-secret-change-me",
        "change-me",
        "change-me-shared-with-issuer",
    }
    if secret in weak or len(secret) < 16:
        raise LicenseError(
            "企业授权要求配置高强度 MDBRIDGE_LICENSE_SECRET（≥16 字符，禁止占位符）",
            code="license_secret_weak",
            status_code=403,
        )
    info = current_license()
    if info is None or not info.valid:
        reason = info.reason if info else "missing_token"
        raise LicenseError(
            f"企业授权无效（{reason}）。请配置 MDBRIDGE_LICENSE_KEY 或 LICENSE 文件；"
            f"本机 machine_id={machine_id()}",
            code="license_invalid",
            status_code=403,
        )
    return info
