"""Startup warmup for heavy converters / fonts (non-blocking)."""

from __future__ import annotations

import logging
import threading

from app.config import settings

_log = logging.getLogger("mdbridge.warmup")
_done = False
_lock = threading.Lock()


def warmup_sync() -> dict[str, str]:
    """Eagerly construct docling converters and probe WeasyPrint fonts."""
    global _done
    status: dict[str, str] = {}
    with _lock:
        if _done and not settings.warmup_force:
            return {"status": "already"}

    # Office + PDF converters (optional [office])
    try:
        from app.importers._docling_converter import get_office_converter, get_pdf_converter

        get_office_converter()
        status["office_converter"] = "ok"
        get_pdf_converter()
        status["pdf_converter"] = "ok"
    except Exception as exc:
        status["docling"] = f"skip:{type(exc).__name__}"
        _log.info("docling warmup skipped: %s", exc)

    # WeasyPrint / fontconfig probe
    try:
        from weasyprint import HTML

        HTML(string="<html><body><p>warmup</p></body></html>").write_pdf()
        status["weasyprint"] = "ok"
    except Exception as exc:
        status["weasyprint"] = f"skip:{type(exc).__name__}"
        _log.info("weasyprint warmup skipped: %s", exc)

    with _lock:
        _done = True
    status["status"] = "done"
    return status


def start_warmup_background() -> None:
    if not settings.warmup_on_startup:
        return

    def _run() -> None:
        try:
            result = warmup_sync()
            _log.info("warmup finished: %s", result)
        except Exception as exc:
            _log.warning("warmup failed: %s", exc)

    threading.Thread(target=_run, name="mdbridge-warmup", daemon=True).start()
