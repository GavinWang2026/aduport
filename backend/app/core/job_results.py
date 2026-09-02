"""Persist async job result blobs to disk (survives process restart within TTL)."""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings

_log = logging.getLogger("mdbridge.job_results")


def _root() -> Path | None:
    raw = (getattr(settings, "job_result_dir", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def result_path_for(job_id: str) -> Path | None:
    root = _root()
    if root is None:
        return None
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    if not safe:
        return None
    return root / f"{safe}.bin"


def save_job_result(job_id: str, data: bytes) -> str | None:
    """Write result bytes; return relative/absolute path string or None if disabled."""
    path = result_path_for(job_id)
    if path is None:
        return None
    try:
        path.write_bytes(data)
        return str(path)
    except OSError as exc:
        _log.warning("failed to persist job result %s: %s", job_id, exc)
        return None


def load_job_result(job_id: str) -> bytes | None:
    path = result_path_for(job_id)
    if path is None or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError as exc:
        _log.warning("failed to load job result %s: %s", job_id, exc)
        return None


def delete_job_result(job_id: str) -> None:
    path = result_path_for(job_id)
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("failed to delete job result %s: %s", job_id, exc)
