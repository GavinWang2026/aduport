"""Persist async job result blobs to disk (survives process restart within TTL)."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from aduport.config import settings

_log = logging.getLogger("aduport.job_results")


def _root() -> Path | None:
    raw = (getattr(settings, "job_result_dir", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def _safe_job_id(job_id: str) -> str | None:
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    return safe or None


def _safe_tenant(tenant_id: str | None) -> str:
    """Sanitize tenant segment — no dots (blocks ``..`` path traversal)."""
    tid = (tenant_id or "").strip()
    if not tid or tid in (".", ".."):
        return "_default"
    safe = "".join(c for c in tid if c.isalnum() or c in "-_")[:64]
    return safe or "_default"


def result_path_for(job_id: str, tenant_id: str | None = None) -> Path | None:
    root = _root()
    if root is None:
        return None
    safe = _safe_job_id(job_id)
    if not safe:
        return None
    # Tenant subdirectory (v13 P2-6); legacy flat files remain readable via load_*.
    return root / _safe_tenant(tenant_id) / f"{safe}.bin"


def input_path_for(job_id: str, tenant_id: str | None = None) -> Path | None:
    root = _root()
    if root is None:
        return None
    safe = _safe_job_id(job_id)
    if not safe:
        return None
    return root / _safe_tenant(tenant_id) / f"{safe}.in.bin"


def _legacy_result_path(job_id: str) -> Path | None:
    root = _root()
    safe = _safe_job_id(job_id)
    if root is None or not safe:
        return None
    return root / f"{safe}.bin"


def _legacy_input_path(job_id: str) -> Path | None:
    root = _root()
    safe = _safe_job_id(job_id)
    if root is None or not safe:
        return None
    return root / f"{safe}.in.bin"


def _atomic_write(path: Path, data: bytes) -> None:
    """Temp file + os.replace (v13 P2-9)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".bin", dir=str(path.parent))
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp_name, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_job_result(
    job_id: str, data: bytes, *, tenant_id: str | None = None
) -> str | None:
    """Write result bytes; return path string or None if disabled."""
    path = result_path_for(job_id, tenant_id)
    if path is None:
        return None
    try:
        _atomic_write(path, data)
        return str(path)
    except OSError as exc:
        _log.warning("failed to persist job result %s: %s", job_id, exc)
        return None


def load_job_result(job_id: str, *, tenant_id: str | None = None) -> bytes | None:
    path = result_path_for(job_id, tenant_id)
    candidates = [p for p in (path, _legacy_result_path(job_id)) if p is not None]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return candidate.read_bytes()
        except OSError as exc:
            _log.warning("failed to load job result %s: %s", job_id, exc)
    return None


def delete_job_result(job_id: str, *, tenant_id: str | None = None) -> None:
    for path in (result_path_for(job_id, tenant_id), _legacy_result_path(job_id)):
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.warning("failed to delete job result %s: %s", job_id, exc)


def save_job_input(
    job_id: str, data: bytes, *, tenant_id: str | None = None
) -> str | None:
    """Persist queued job input for crash recovery replay (P1-9)."""
    path = input_path_for(job_id, tenant_id)
    if path is None:
        return None
    try:
        _atomic_write(path, data)
        return str(path)
    except OSError as exc:
        _log.warning("failed to persist job input %s: %s", job_id, exc)
        return None


def load_job_input(job_id: str, *, tenant_id: str | None = None) -> bytes | None:
    path = input_path_for(job_id, tenant_id)
    candidates = [p for p in (path, _legacy_input_path(job_id)) if p is not None]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return candidate.read_bytes()
        except OSError as exc:
            _log.warning("failed to load job input %s: %s", job_id, exc)
    return None


def delete_job_input(job_id: str, *, tenant_id: str | None = None) -> None:
    for path in (input_path_for(job_id, tenant_id), _legacy_input_path(job_id)):
        if path is None:
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.warning("failed to delete job input %s: %s", job_id, exc)
