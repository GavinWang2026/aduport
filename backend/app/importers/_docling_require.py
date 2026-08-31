"""Shared soft-import guard for optional docling dependency."""

from __future__ import annotations

from app.core.exceptions import InvalidDocumentError


def require_docling(fmt_label: str = "Office"):
    try:
        import docling.document_converter  # noqa: F401
    except ImportError as exc:
        raise InvalidDocumentError(
            f"{fmt_label} 导入需要安装 docling：pip install 'md-bridge[office]'"
        ) from exc
    from app.importers._docling_converter import get_office_converter, get_pdf_converter

    return get_office_converter, get_pdf_converter
