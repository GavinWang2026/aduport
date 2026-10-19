"""Job completion webhooks (optional callback_url on POST /api/jobs)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlparse

from aduport.config import settings
from aduport.core.safe_fetch import is_safe_url, post_json_pinned
from aduport.core.secrets import secret_plain

_log = logging.getLogger("aduport.webhooks")

# Fields that must never leave the process toward third-party callbacks.
_REDACT_KEYS = frozenset(
    {
        "access_token",
        "result_path",
        "input_path",
        "object_uri",
        "archive_uri",
        "tenant_id",
    }
)


def validate_callback_url(url: str | None) -> str | None:
    """Return normalized URL or None; raise ValueError if invalid when non-empty."""
    raw = (url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("callback_url 仅支持 http/https")
    if not parsed.netloc:
        raise ValueError("callback_url 无效")
    port = parsed.port
    if port is not None and port not in (80, 443):
        raise ValueError("callback_url 仅允许 80/443 端口")
    if not is_safe_url(raw):
        raise ValueError("callback_url 指向内网或不可达地址，已拒绝")
    return raw


def _redact_job(job_payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job_payload.items() if k not in _REDACT_KEYS}


def _sign_body(body_bytes: bytes) -> dict[str, str]:
    secret = secret_plain(getattr(settings, "webhook_secret", None)).strip()
    if not secret:
        return {}
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return {"X-AduPort-Signature": f"sha256={digest}"}


def fire_job_webhook(job_payload: dict[str, Any], callback_url: str | None) -> None:
    """POST job status JSON to callback_url (best-effort, never raises to caller)."""
    if not settings.webhook_enabled:
        return
    url = (callback_url or "").strip()
    if not url:
        return
    safe_payload = _redact_job(job_payload)
    try:
        validate_callback_url(url)
    except ValueError as exc:
        _log.warning("skip webhook for job %s: %s", safe_payload.get("id"), exc)
        return

    body: dict[str, Any] = {
        "event": "job.finished",
        "job": safe_payload,
        "download_hint": f"/api/jobs/{safe_payload.get('id')}/result",
    }
    tgt = str(safe_payload.get("target_format") or "").lower().lstrip(".")
    if tgt in ("rag", "elements", "rag_elements", "rag-json"):
        body["result_format"] = {
            "schema": "aduport.rag_elements.v1",
            "schema_version": "1.1",
            "schema_version_embed": "1.2",
            "content_type": "application/json",
            "hint": "RAG connector; set Form embed=true for vectors (1.2) + optional VECTOR_DIR persist",
        }
    timeout = float(settings.webhook_timeout_seconds or 10.0)
    retries = max(0, int(getattr(settings, "webhook_retries", 2) or 0))
    import json

    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    sig_headers = _sign_body(body_bytes)

    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            status = post_json_pinned(
                url, body, timeout=timeout, extra_headers=sig_headers or None
            )
            if status >= 400:
                _log.warning(
                    "webhook %s returned %s for job %s (attempt %s)",
                    url,
                    status,
                    safe_payload.get("id"),
                    attempt + 1,
                )
                if status >= 500 and attempt < retries:
                    time.sleep(min(2.0, 0.4 * (2**attempt)))
                    continue
            else:
                _log.info("webhook delivered job %s → %s", safe_payload.get("id"), url)
            return
        except Exception as exc:
            last_exc = exc
            _log.warning(
                "webhook failed for job %s (attempt %s): %s",
                safe_payload.get("id"),
                attempt + 1,
                exc,
            )
            if attempt < retries:
                time.sleep(min(2.0, 0.4 * (2**attempt)))
    if last_exc is not None:
        _log.warning(
            "webhook exhausted retries for job %s: %s",
            safe_payload.get("id"),
            last_exc,
        )
