"""Job completion webhooks (optional callback_url on POST /api/jobs)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.core.safe_fetch import is_safe_url

_log = logging.getLogger("mdbridge.webhooks")


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


def fire_job_webhook(job_payload: dict[str, Any], callback_url: str | None) -> None:
    """POST job status JSON to callback_url (best-effort, never raises to caller)."""
    if not settings.webhook_enabled:
        return
    url = (callback_url or "").strip()
    if not url:
        return
    # Never leak per-job access tokens to third-party callbacks
    safe_payload = {k: v for k, v in job_payload.items() if k != "access_token"}
    try:
        validate_callback_url(url)
    except ValueError as exc:
        _log.warning("skip webhook for job %s: %s", safe_payload.get("id"), exc)
        return

    body = {
        "event": "job.finished",
        "job": safe_payload,
        "download_hint": f"/api/jobs/{safe_payload.get('id')}/result",
    }
    timeout = float(settings.webhook_timeout_seconds or 10.0)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.post(url, json=body)
            if resp.status_code >= 400:
                _log.warning(
                    "webhook %s returned %s for job %s",
                    url,
                    resp.status_code,
                    safe_payload.get("id"),
                )
            else:
                _log.info("webhook delivered job %s → %s", safe_payload.get("id"), url)
    except Exception as exc:
        _log.warning("webhook failed for job %s: %s", safe_payload.get("id"), exc)
