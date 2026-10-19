"""Detect whether local docling models are present (avoid Hub timeouts)."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from aduport.config import settings

_log = logging.getLogger("aduport.docling")


def _candidate_model_dirs() -> list[Path]:
    dirs: list[Path] = []
    art = (settings.docling_artifacts_path or "").strip()
    if art:
        dirs.append(Path(art).expanduser())
    env = (os.environ.get("DOCLING_ARTIFACTS_PATH") or "").strip()
    if env:
        dirs.append(Path(env).expanduser())
    home = Path.home() / ".cache" / "docling" / "models"
    dirs.append(home)
    # Common docker bake path
    dirs.append(Path("/opt/docling/models"))
    return dirs


def _dir_looks_ready(path: Path) -> bool:
    if not path.is_dir():
        return False
    try:
        # layout / tableformer / RapidOCR leave nested dirs or weight files
        entries = list(path.iterdir())
    except OSError:
        return False
    if not entries:
        return False
    # Any non-empty models tree counts as ready (offline bake or prior download)
    return True


@lru_cache(maxsize=1)
def docling_models_ready() -> bool:
    """True when a local model directory exists — safe to call DocumentConverter offline."""
    for d in _candidate_model_dirs():
        if _dir_looks_ready(d):
            _log.info("docling models ready at %s", d)
            return True
    return False


def resolve_artifacts_path() -> str | None:
    for d in _candidate_model_dirs():
        if _dir_looks_ready(d):
            return str(d)
    return None


def clear_docling_ready_cache() -> None:
    docling_models_ready.cache_clear()
