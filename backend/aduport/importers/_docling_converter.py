"""Process-wide lazy DocumentConverter singletons (thread-safe)."""

from __future__ import annotations

import threading
from typing import Any

from aduport.config import settings

_lock = threading.Lock()
_office_converter: Any | None = None
_pdf_converter: Any | None = None
_pdf_cache_key: tuple | None = None


def get_office_converter():
    """Shared converter for DOCX / PPTX / EPUB / MOBI (default format options)."""
    global _office_converter
    with _lock:
        if _office_converter is None:
            from docling.document_converter import DocumentConverter

            _office_converter = DocumentConverter()
        return _office_converter


def _pdf_settings_key() -> tuple:
    return (
        bool(settings.ocr_enabled),
        bool(settings.pdf_docling_tables),
        settings.docling_artifacts_path or "",
    )


def get_pdf_converter():
    """Shared PDF converter; rebuilt if OCR/table/artifacts settings change."""
    global _pdf_converter, _pdf_cache_key
    from aduport.importers.docling_ready import resolve_artifacts_path

    artifacts = (settings.docling_artifacts_path or "").strip() or (resolve_artifacts_path() or "")
    key = (
        bool(settings.ocr_enabled),
        bool(settings.pdf_docling_tables),
        artifacts,
    )
    with _lock:
        if _pdf_converter is None or _pdf_cache_key != key:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions
            from docling.document_converter import DocumentConverter, PdfFormatOption

            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = bool(settings.ocr_enabled)
            if hasattr(pipeline_options, "do_table_structure"):
                pipeline_options.do_table_structure = bool(settings.pdf_docling_tables)
            if hasattr(pipeline_options, "enable_remote_services"):
                pipeline_options.enable_remote_services = False
            if artifacts:
                pipeline_options.artifacts_path = artifacts
            if not settings.ocr_enabled and hasattr(pipeline_options, "force_backend_text"):
                pipeline_options.force_backend_text = True

            _pdf_converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
                }
            )
            _pdf_cache_key = key
        return _pdf_converter


def reset_converters_for_tests() -> None:
    """Clear singletons (tests only)."""
    global _office_converter, _pdf_converter, _pdf_cache_key
    with _lock:
        _office_converter = None
        _pdf_converter = None
        _pdf_cache_key = None
