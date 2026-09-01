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


def _call_native(
    native: ImporterFn, source_bytes: bytes, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> DocumentModel:
    """Invoke a native importer, matching its own arity.

    Importers are inconsistent: some take (bytes, filename), others (bytes)
    only. A wrapped format may be routed either way, so extra arguments are
    dropped rather than raising TypeError on the fallback path.
    """
    import inspect

    try:
        params = inspect.signature(native).parameters
    except (TypeError, ValueError):  # builtins / C callables
        return native(source_bytes, *args, **kwargs)
    accepts_varargs = any(p.kind is p.VAR_POSITIONAL for p in params.values())
    positional = [
        p
        for p in params.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    if accepts_varargs or len(positional) > 1:
        return native(source_bytes, *args, **kwargs)
    return native(source_bytes)


def _anydoc_first(fmt: str, native: ImporterFn | None) -> ImporterFn:
    """Wrap an importer so anydoc parses first and the native one is the net.

    anydoc handles the format only when configuration allows it. Recoverable
    declines (unknown format, PDF needing OCR) fall through to `native`;
    hard failures (encrypted, malformed, over a safety limit) propagate, since
    no other importer would do better and hiding them would mislead callers.
    """

    def _wrapped(source_bytes: bytes, *args: Any, **kwargs: Any) -> DocumentModel:
        from app.config import settings
        from app.importers.anydoc_bridge import (
            AnydocFallback,
            AnydocUnavailable,
            import_via_anydoc,
            should_handle,
        )

        filename = args[0] if args and isinstance(args[0], str) else ""
        if should_handle(fmt):
            try:
                return import_via_anydoc(source_bytes, filename, fmt)
            except AnydocUnavailable:
                if native is None:
                    raise
            except AnydocFallback:
                if native is None or not settings.anydoc_fallback:
                    raise
        if native is None:
            from app.core.exceptions import UnsupportedFormatError

            raise UnsupportedFormatError(
                f"格式 {fmt} 需要内置原生解析扩展：执行 scripts/build_native.sh 编译 "
                f"mdbridge_native._doc"
            )
        model = _call_native(native, source_bytes, args, kwargs)
        # Mark native / fallback path for audit & metrics (anydoc sets importer itself).
        if not model.metadata.get("importer"):
            model.metadata["importer"] = (
                "native_fallback" if should_handle(fmt) else "native"
            )
        return model

    _wrapped.__name__ = f"anydoc_or_{getattr(native, '__name__', fmt)}"
    return _wrapped


def _wire_anydoc() -> None:
    """Route anydoc-capable formats through the bridge, keeping natives as fallback.

    Formats md-bridge never had an importer for (ppt, xls, ods, odp, and the
    macro / binary variants) become available for the first time; the rest gain
    span-aware tables, real list numbering, LaTeX equations and asset bytes.
    """
    from app.importers.anydoc_bridge import (
        ANYDOC_ONLY_SIBLINGS,
        supports as anydoc_supports,
    )

    # Formats with a native importer: anydoc leads, native catches.
    for fmt in ("doc", "docx", "odt", "rtf", "pptx", "epub", "csv", "xlsx"):
        native = _IMPORTERS.get(fmt)
        if native is not None and anydoc_supports(fmt):
            register_importer(fmt, _anydoc_first(fmt, native))

    # Formats only anydoc can read — no native alternative exists. The sibling
    # mapping lets disabling anydoc degrade to a compatible native importer
    # (docm->docx, pptm/ppsx->pptx, xlsm->xlsx) instead of breaking; None means
    # the format is simply unavailable without anydoc.
    for fmt, sibling in ANYDOC_ONLY_SIBLINGS.items():
        if not anydoc_supports(fmt):
            continue
        native = _IMPORTERS.get(sibling) if sibling else None
        register_importer(fmt, _anydoc_first(fmt, native))


def ensure_builtin_plugins() -> None:
    """Lazy-load built-in plugins once."""
    needed = {
        "md",
        "docx",
        "pdf",
        "html",
        "txt",
        "epub",
        "pptx",
        "mobi",
        "azw3",
        "kf8",
        "latex",
        "csv",
        "odt",
        "json",
        "xlsx",
    }
    if needed.issubset(_EXPORTERS) and {
        "odt",
        "rtf",
        "doc",
        "json",
        "xlsx",
        "csv",
        "latex",
        "azw3",
        "kf8",
    }.issubset(_IMPORTERS):
        return
    from app.exporters.json_exporter import import_json
    from app.exporters.xlsx_exporter import import_xlsx
    from app.importers.csv_importer import import_csv
    from app.importers.doc_importer import import_doc
    from app.importers.docx_importer import import_docx
    from app.importers.epub_importer import import_epub
    from app.importers.html_importer import import_html
    from app.importers.latex_importer import import_latex
    from app.importers.md_importer import import_md
    from app.importers.mobi_importer import import_mobi
    from app.importers.odt_importer import import_odt
    from app.importers.pdf_importer import import_pdf
    from app.importers.pptx_importer import import_pptx
    from app.importers.rtf_importer import import_rtf
    from app.importers.txt_importer import import_txt

    register_importer("md", import_md)
    register_importer("txt", import_txt)
    register_importer("html", import_html)
    register_importer("docx", import_docx)
    register_importer("pdf", import_pdf)
    register_importer("pptx", import_pptx)
    register_importer("epub", import_epub)
    register_importer("mobi", import_mobi)
    # AZW3/KF8 share PalmDOC/MOBI container — reuse text extractor path
    register_importer("azw3", import_mobi)
    register_importer("kf8", import_mobi)
    register_importer("latex", import_latex)
    register_importer("odt", import_odt)
    register_importer("rtf", import_rtf)
    register_importer("doc", import_doc)
    register_importer("json", import_json)
    register_importer("xlsx", import_xlsx)
    register_importer("csv", import_csv)

    from app.exporters.azw3_exporter import export_azw3, export_kf8
    from app.exporters.csv_exporter import export_csv
    from app.exporters.docx_exporter import export_docx
    from app.exporters.epub_exporter import export_epub
    from app.exporters.html_exporter import export_html
    from app.exporters.json_exporter import export_json
    from app.exporters.latex_exporter import export_latex
    from app.exporters.md_exporter import export_md
    from app.exporters.mobi_exporter import export_mobi
    from app.exporters.odt_exporter import export_odt
    from app.exporters.pdf_exporter import export_pdf
    from app.exporters.pptx_exporter import export_pptx
    from app.exporters.txt_exporter import export_txt
    from app.exporters.xlsx_exporter import export_xlsx
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

    def _export_latex_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_latex(model).encode("utf-8")

    def _export_csv_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_csv(model).encode("utf-8")

    def _export_odt_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_odt(model)

    def _export_json_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_json(model).encode("utf-8")

    def _export_xlsx_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_xlsx(model)

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
    register_exporter("latex", _export_latex_bytes)
    register_exporter("csv", _export_csv_bytes)
    register_exporter("odt", _export_odt_bytes)
    register_exporter("json", _export_json_bytes)
    register_exporter("xlsx", _export_xlsx_bytes)

    # Layer anydoc over the native importers (no-op when disabled/absent).
    _wire_anydoc()
