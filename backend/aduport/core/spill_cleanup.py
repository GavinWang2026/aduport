"""Orphan temp spill cleanup on startup (report P1-9 / P3-9)."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path

_log = logging.getLogger("aduport.spill")


def cleanup_orphan_spills(*, max_age_seconds: float = 86400.0) -> int:
    """Remove stale aduport temp spill files older than max_age_seconds."""
    tmp = Path(tempfile.gettempdir())
    now = time.time()
    removed = 0
    patterns = ("aduport-iso-in-", "aduport-job-in-", "aduport-job-out-", "aduport-spill-")
    try:
        for entry in tmp.iterdir():
            if not entry.is_file() and not entry.is_dir():
                continue
            name = entry.name
            if not any(name.startswith(p) for p in patterns):
                if entry.is_dir() and name == "aduport-spill":
                    # tenant subdirs under aduport-spill/
                    for sub in entry.iterdir():
                        removed += _maybe_unlink(sub, now, max_age_seconds)
                    continue
                continue
            removed += _maybe_unlink(entry, now, max_age_seconds)
    except OSError as exc:
        _log.warning("orphan spill scan failed: %s", exc)
    if removed:
        _log.info("removed %s orphan spill path(s)", removed)
    return removed


def _maybe_unlink(path: Path, now: float, max_age: float) -> int:
    try:
        age = now - path.stat().st_mtime
        if age < max_age:
            return 0
        if path.is_dir():
            for child in path.iterdir():
                _maybe_unlink(child, now, max_age)
            try:
                path.rmdir()
            except OSError:
                pass
            return 1
        path.unlink(missing_ok=True)
        return 1
    except OSError:
        return 0
