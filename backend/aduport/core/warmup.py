"""Startup warmup for heavy converters / fonts (non-blocking)."""

from __future__ import annotations

import logging
import threading

from aduport.config import settings

_log = logging.getLogger("aduport.warmup")
_done = False
_lock = threading.Lock()


def warmup_sync() -> dict[str, str]:
    """Eagerly construct docling converters, probe WeasyPrint, and hot-path convert.

    v6/T3-#20 / D-22: also run md→html / md→docx so first real request is not
    2× colder than steady state.
    """
    global _done
    status: dict[str, str] = {}
    with _lock:
        if _done and not settings.warmup_force:
            return {"status": "already"}

    # Office + PDF converters (optional [office])
    try:
        from aduport.importers._docling_converter import get_office_converter, get_pdf_converter

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

    # CJK / pdf_simple font path probe (subset gate; skip if unavailable)
    try:
        from aduport.core.document_model import DocumentModel, Paragraph
        from aduport.exporters.pdf_simple import export_pdf_simple

        export_pdf_simple(
            DocumentModel(nodes=[Paragraph(text="Warmup PDF font 中文")])
        )
        status["pdf_simple_font"] = "ok"
    except Exception as exc:
        status["pdf_simple_font"] = f"skip:{type(exc).__name__}"
        _log.info("pdf_simple font warmup skipped: %s", exc)

    # Hot convert paths (md/html/docx/pdf) + long-lived isolate pool prime (T2-#12)
    try:
        from aduport.core.converter import convert

        sample = b"# Warmup\n\n**bold** and _italic_\n\n- item\n"
        convert(sample, "md", "html", filename="warmup.md", skip_magic=True)
        status["md_html"] = "ok"
        convert(sample, "md", "docx", filename="warmup.md", skip_magic=True)
        status["md_docx"] = "ok"
        convert(b"<p>warmup</p>", "html", "md", filename="warmup.html", skip_magic=True)
        status["html_md"] = "ok"
        convert(sample, "md", "pdf", filename="warmup.md", skip_magic=True)
        status["md_pdf"] = "ok"
    except Exception as exc:
        status["hot_paths"] = f"skip:{type(exc).__name__}"
        _log.info("convert hot-path warmup skipped: %s", exc)

    if settings.convert_isolate and not settings.convert_isolate_fork:
        try:
            from aduport.core.convert_isolate import convert_isolated, ensure_isolate_pool

            ensure_isolate_pool()
            status["isolate_pool"] = "ok"
            # Prime one job through the pool so first API request is warm
            convert_isolated(
                b"# isolate warmup\n",
                "md",
                "txt",
                filename="warmup-iso.md",
                skip_magic=True,
            )
            status["isolate_prime"] = "ok"
        except Exception as exc:
            status["isolate_pool"] = f"skip:{type(exc).__name__}"
            _log.info("isolate pool warmup skipped: %s", exc)

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

    threading.Thread(target=_run, name="aduport-warmup", daemon=True).start()
