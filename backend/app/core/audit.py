"""Simple audit logger for conversion events (compliance trail)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

_lock = threading.Lock()
_logger = logging.getLogger("mdbridge.audit")


def _ensure_logger() -> None:
    if _logger.handlers:
        return
    _logger.setLevel(logging.INFO)
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.propagate = False


def _sanitize_detail(detail: str) -> str:
    """Strip absolute paths / home dirs from audit detail for safer shared logs."""
    import re

    text = detail[:500]
    text = re.sub(r"/Users/[^/\s]+/", "/Users/***/", text)
    text = re.sub(r"/home/[^/\s]+/", "/home/***/", text)
    text = re.sub(r"[A-Za-z]:\\\\[^\\\s]+", r"?:\\***", text)
    text = re.sub(r"/var/folders/[^\s]+", "/var/folders/***", text)
    text = re.sub(r"/tmp/[^\s]+", "/tmp/***", text)
    return text


def audit_event(
    *,
    action: str,
    source_format: str,
    target_format: str,
    filename: str = "",
    success: bool = True,
    detail: str = "",
    client: str = "",
    cached: bool = False,
    bytes_in: int = 0,
    bytes_out: int = 0,
) -> None:
    if not settings.audit_enabled:
        return
    _ensure_logger()
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "source_format": source_format,
        "target_format": target_format,
        "filename": filename,
        "success": success,
        "detail": _sanitize_detail(detail) if detail else "",
        "client": client,
        "cached": cached,
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
    }
    with _lock:
        _logger.info(json.dumps(record, ensure_ascii=False))
