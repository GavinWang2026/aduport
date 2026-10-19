"""Simple audit logger for conversion events (compliance trail).

Report P2-11: isolate workers buffer events and the parent replays them, OR
workers that share the log path append under an exclusive flock so rotation
cannot interleave mid-line with another process.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aduport.config import settings

_lock = threading.Lock()
_logger = logging.getLogger("aduport.audit")
_audit_buffer: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "aduport_audit_buffer", default=None
)


def begin_audit_buffer() -> Token:
    """Start buffering audit events (isolate worker) instead of writing the file."""
    return _audit_buffer.set([])


def take_audit_buffer(token: Token | None = None) -> list[dict[str, Any]]:
    events = list(_audit_buffer.get() or [])
    if token is not None:
        _audit_buffer.reset(token)
    else:
        _audit_buffer.set(None)
    return events


def replay_audit_events(events: list[dict[str, Any]]) -> None:
    """Append pre-built audit records from an isolate worker into the parent log."""
    if not settings.audit_enabled or not events:
        return
    for record in events:
        if not isinstance(record, dict):
            continue
        _append_jsonl(record)


def _ensure_logger() -> None:
    # Kept for callers/tests that inspect handlers; primary path is _append_jsonl.
    if _logger.handlers:
        return
    _logger.setLevel(logging.INFO)
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.NullHandler()
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


def _append_jsonl(record: dict[str, Any]) -> None:
    """Atomic-ish multi-process append with size-based rotation under flock."""
    path = Path(settings.audit_log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    max_bytes = max(1_000_000, int(getattr(settings, "audit_max_bytes", 50 * 1024 * 1024)))
    backups = max(1, int(getattr(settings, "audit_backup_count", 10)))
    with _lock:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(str(path), flags, 0o600)
        try:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            try:
                try:
                    size = os.fstat(fd).st_size
                except OSError:
                    size = 0
                if size >= max_bytes:
                    _rotate_locked(path, backups)
                    os.close(fd)
                    fd = os.open(str(path), flags, 0o600)
                    try:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_EX)
                    except (ImportError, OSError):
                        pass
                os.write(fd, line.encode("utf-8"))
            finally:
                try:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass


def _rotate_locked(path: Path, backups: int) -> None:
    """Rotate ``path`` → ``path.1`` … under the caller's flock (best-effort)."""
    for i in range(backups, 0, -1):
        src = path if i == 1 else Path(f"{path}.{i - 1}")
        dst = Path(f"{path}.{i}")
        if src.exists():
            try:
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
            except OSError:
                pass


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
    tenant_id: str | None = None,
    importer: str = "",
) -> None:
    if not settings.audit_enabled:
        return
    _ensure_logger()
    if not client:
        try:
            from aduport.core.request_context import current_client

            client = current_client()
        except Exception:
            client = ""
    if tenant_id is None:
        try:
            from aduport.core.request_context import current_tenant_id

            tenant_id = current_tenant_id()
        except Exception:
            tenant_id = None
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "source_format": source_format,
        "target_format": target_format,
        "filename": filename,
        "success": success,
        "detail": _sanitize_detail(detail) if detail else "",
        "client": client,
        "tenant_id": tenant_id,
        "cached": cached,
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
        # anydoc_fast_md | anydoc | native | native_fallback | "" (passthrough/unknown)
        "importer": importer or "",
    }
    buf = _audit_buffer.get()
    if buf is not None:
        buf.append(record)
        return
    _append_jsonl(record)
