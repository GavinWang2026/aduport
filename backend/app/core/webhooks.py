"""Job completion webhooks (optional callback_url on POST /api/jobs)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

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
    return raw


def fire_job_webhook(job_payload: dict[str, Any], callback_url: str | None) -> None:
    """POST job status JSON to callback_url (best-effort, never raises to caller)."""
    if not settings.webhook_enabled:
        return
    url = (callback_url or "").strip()
    if not url:
        return
    try:
        validate_callback_url(url)
    except ValueError as exc:
        _log.warning("skip webhook for job %s: %s", job_payload.get("id"), exc)
        return

    body = {
        "event": "job.finished",
        "job": job_payload,
        "download_hint": f"/api/jobs/{job_payload.get('id')}/result",
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
                    job_payload.get("id"),
                )
            else:
                _log.info("webhook delivered job %s → %s", job_payload.get("id"), url)
    except Exception as exc:
        _log.warning("webhook failed for job %s: %s", job_payload.get("id"), exc)
