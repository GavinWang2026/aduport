"""Importer / exporter plugin registry (replaces hard-coded if/elif chains)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.core.document_model import DocumentModel

ImporterFn = Callable[..., DocumentModel]
ExporterFn = Callable[..., bytes | str]

_IMPORTERS: dict[str, ImporterFn] = {}
_EXPORTERS: dict[str, ExporterFn] = {}


def register_importer(fmt: str, fn: ImporterFn) -> ImporterFn:
    _IMPORTERS[fmt.lower().lstrip(".")] = fn
    return fn


def register_exporter(fmt: str, fn: ExporterFn) -> ExporterFn:
    _EXPORTERS[fmt.lower().lstrip(".")] = fn
    return fn


def get_importer(fmt: str) -> ImporterFn | None:
    return _IMPORTERS.get(fmt.lower().lstrip("."))


def get_exporter(fmt: str) -> ExporterFn | None:
    return _EXPORTERS.get(fmt.lower().lstrip("."))


def registered_import_formats() -> list[str]:
    return sorted(_IMPORTERS)


def registered_export_formats() -> list[str]:
    return sorted(_EXPORTERS)


def ensure_builtin_plugins() -> None:
    """Lazy-load built-in plugins once."""
    needed = {"md", "docx", "pdf", "html", "txt", "epub", "pptx", "mobi", "azw3", "kf8"}
    if needed.issubset(_EXPORTERS) and _IMPORTERS:
        return
    from app.importers.docx_importer import import_docx
    from app.importers.epub_importer import import_epub
    from app.importers.html_importer import import_html
    from app.importers.md_importer import import_md
    from app.importers.mobi_importer import import_mobi
    from app.importers.pdf_importer import import_pdf
    from app.importers.pptx_importer import import_pptx
    from app.importers.txt_importer import import_txt

    register_importer("md", import_md)
    register_importer("txt", import_txt)
    register_importer("html", import_html)
    register_importer("docx", import_docx)
    register_importer("pdf", import_pdf)
    register_importer("pptx", import_pptx)
    register_importer("epub", import_epub)
    register_importer("mobi", import_mobi)

    from app.exporters.azw3_exporter import export_azw3, export_kf8
    from app.exporters.docx_exporter import export_docx
    from app.exporters.epub_exporter import export_epub
    from app.exporters.html_exporter import export_html
    from app.exporters.md_exporter import export_md
    from app.exporters.mobi_exporter import export_mobi
    from app.exporters.pdf_exporter import export_pdf
    from app.exporters.pptx_exporter import export_pptx
    from app.exporters.txt_exporter import export_txt

    def _export_md_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_md(model).encode("utf-8")

    def _export_docx_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_docx(model)

    def _export_pptx_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_pptx(model)

    def _export_mobi_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_mobi(model)

    def _export_azw3_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_azw3(model)

    def _export_kf8_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_kf8(model)

    def _export_html_bytes(
        model: DocumentModel,
        *,
        pdf_font_family: str | None = None,
        pdf_custom_css: str | None = None,
        pdf_toc: bool | None = None,
        **_: Any,
    ) -> bytes:
        return export_html(
            model,
            font_family=pdf_font_family,
            custom_css=pdf_custom_css,
            include_toc=pdf_toc,
        ).encode("utf-8")

    def _export_txt_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_txt(model).encode("utf-8")

    def _export_epub_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_epub(model)

    def _export_pdf_bytes(
        model: DocumentModel,
        *,
        pdf_font_family: str | None = None,
        pdf_custom_css: str | None = None,
        pdf_page_size: str | None = None,
        pdf_orientation: str | None = None,
        pdf_header: str | None = None,
        pdf_footer: str | None = None,
        pdf_page_numbers: bool | None = None,
        pdf_toc: bool | None = None,
        **_: Any,
    ) -> bytes:
        return export_pdf(
            model,
            font_family=pdf_font_family,
            custom_css=pdf_custom_css,
            page_size=pdf_page_size,
            orientation=pdf_orientation,
            header=pdf_header,
            footer=pdf_footer,
            page_numbers=pdf_page_numbers,
            toc=pdf_toc,
        )

    register_exporter("md", _export_md_bytes)
    register_exporter("docx", _export_docx_bytes)
    register_exporter("pdf", _export_pdf_bytes)
    register_exporter("html", _export_html_bytes)
    register_exporter("txt", _export_txt_bytes)
    register_exporter("epub", _export_epub_bytes)
    register_exporter("pptx", _export_pptx_bytes)
    register_exporter("mobi", _export_mobi_bytes)
    register_exporter("azw3", _export_azw3_bytes)
    register_exporter("kf8", _export_kf8_bytes)
