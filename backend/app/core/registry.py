"""Importer / exporter plugin registry (replaces hard-coded if/elif chains)."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Any

from app.core.document_model import DocumentModel

ImporterFn = Callable[..., DocumentModel]
ExporterFn = Callable[..., bytes | str]

_IMPORTERS: dict[str, ImporterFn] = {}
_EXPORTERS: dict[str, ExporterFn] = {}
_REGISTRY_LOCK = threading.RLock()
_BUILTINS_READY = False


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
    """Invoke a native importer, matching its own arity."""
    try:
        params = inspect.signature(native).parameters
    except (TypeError, ValueError):
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
    """Wrap an importer so anydoc parses first and the native one is the net."""

    def _wrapped(source_bytes: bytes, *args: Any, **kwargs: Any) -> DocumentModel:
        from app.config import settings
        from app.importers.anydoc_bridge import (
            AnydocFallback,
            AnydocUnavailable,
            import_via_anydoc,
            should_handle,
        )

        filename = args[0] if args and isinstance(args[0], str) else ""
        model: DocumentModel | None = None
        if should_handle(fmt):
            try:
                model = import_via_anydoc(source_bytes, filename, fmt)
            except AnydocUnavailable:
                if native is None:
                    raise
            except AnydocFallback:
                if native is None or not settings.anydoc_fallback:
                    raise
            except Exception:
                # Hard parse failures (malformed / encrypted / limits) used to
                # pierce the "never reduces coverage" net. Prefer native when
                # configured — unsupported formats still raise AnydocFallback.
                if native is None or not settings.anydoc_fallback:
                    raise
        if model is None:
            if native is None:
                from app.core.exceptions import UnsupportedFormatError

                raise UnsupportedFormatError(
                    f"格式 {fmt} 需要内置原生解析扩展：执行 scripts/build_native.sh 编译 "
                    f"mdbridge_native._doc"
                )
            model = _call_native(native, source_bytes, args, kwargs)
            if not model.metadata.get("importer"):
                model.metadata["importer"] = (
                    "native_fallback" if should_handle(fmt) else "native"
                )
        if fmt == "docx":
            from app.importers.docx_ooxml import enrich_docx_model

            model = enrich_docx_model(model, source_bytes)
        return model

    _wrapped.__name__ = f"anydoc_or_{getattr(native, '__name__', fmt)}"
    setattr(_wrapped, "_anydoc_wired", True)
    return _wrapped


def _wire_anydoc() -> None:
    from app.importers.anydoc_bridge import (
        ANYDOC_ONLY_SIBLINGS,
        supports as anydoc_supports,
    )

    for fmt in ("doc", "docx", "odt", "rtf", "pptx", "epub", "csv", "xlsx", "ods", "odp"):
        native = _IMPORTERS.get(fmt)
        if native is not None and anydoc_supports(fmt):
            if getattr(native, "_anydoc_wired", False):
                continue
            register_importer(fmt, _anydoc_first(fmt, native))

    for fmt, sibling in ANYDOC_ONLY_SIBLINGS.items():
        existing = _IMPORTERS.get(fmt)
        if existing is not None and getattr(existing, "_anydoc_wired", False):
            continue
        # Prefer dedicated native for this exact format, else sibling alias
        native = _IMPORTERS.get(fmt) or (
            _IMPORTERS.get(sibling) if sibling else None
        )
        if anydoc_supports(fmt):
            register_importer(fmt, _anydoc_first(fmt, native))
        elif native is not None and existing is None:
            # anydoc absent: still expose builtin native so breadth never collapses
            register_importer(fmt, native)


def ensure_builtin_plugins() -> None:
    """Lazy-load built-in plugins once."""
    global _BUILTINS_READY
    with _REGISTRY_LOCK:
        if _BUILTINS_READY:
            return
        _ensure_builtin_plugins_unlocked()
        _BUILTINS_READY = True


def _ensure_builtin_plugins_unlocked() -> None:
    needed_exporters = {
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
        "rtf",
    }
    needed_importers = {
        "odt",
        "rtf",
        "doc",
        "json",
        "xlsx",
        "csv",
        "latex",
        "azw3",
        "kf8",
        "ods",
        "odp",
        "xls",
        "ppt",
    }
    if needed_exporters.issubset(_EXPORTERS) and needed_importers.issubset(_IMPORTERS):
        _wire_anydoc()
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
    from app.importers.odp_importer import import_odp
    from app.importers.ods_importer import import_ods
    from app.importers.odt_importer import import_odt
    from app.importers.ole_office import (
        import_pot,
        import_pps,
        import_ppt,
        import_xls,
        import_xlsb,
    )
    from app.importers.pdf_importer import import_pdf
    from app.importers.pptx_importer import import_pptx
    from app.importers.rtf_importer import import_rtf
    from app.importers.txt_importer import import_txt

    register_importer("md", import_md)
    register_importer("txt", import_txt)
    register_importer("html", import_html)
    register_importer("docx", import_docx)
    register_importer("docm", import_docx)
    register_importer("pdf", import_pdf)
    register_importer("pptx", import_pptx)
    register_importer("pptm", import_pptx)
    register_importer("ppsx", import_pptx)
    register_importer("ppsm", import_pptx)
    register_importer("epub", import_epub)
    register_importer("mobi", import_mobi)
    register_importer("azw3", import_mobi)
    register_importer("kf8", import_mobi)
    register_importer("latex", import_latex)
    register_importer("odt", import_odt)
    register_importer("ods", import_ods)
    register_importer("odp", import_odp)
    register_importer("rtf", import_rtf)
    register_importer("doc", import_doc)
    register_importer("json", import_json)
    register_importer("xlsx", import_xlsx)
    register_importer("xlsm", import_xlsx)
    register_importer("xls", import_xls)
    register_importer("xlsb", import_xlsb)
    register_importer("ppt", import_ppt)
    register_importer("pps", import_pps)
    register_importer("pot", import_pot)
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
    from app.exporters.ods_exporter import export_ods
    from app.exporters.odp_exporter import export_odp
    from app.exporters.pdf_exporter import export_pdf
    from app.exporters.pptx_exporter import export_pptx
    from app.exporters.rtf_exporter import export_rtf
    from app.exporters.txt_exporter import export_txt
    from app.exporters.xlsx_exporter import export_xlsx

    def _export_md_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_md(model).encode("utf-8")

    def _export_docx_bytes(model: DocumentModel, **kwargs: Any) -> bytes:
        return export_docx(model, theme=kwargs.get("docx_theme"))

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

    def _export_ods_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_ods(model)

    def _export_odp_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_odp(model)

    def _export_json_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_json(model).encode("utf-8")

    def _export_xlsx_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_xlsx(model)

    def _export_rtf_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_rtf(model)

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
    register_exporter("ods", _export_ods_bytes)
    register_exporter("odp", _export_odp_bytes)
    register_exporter("json", _export_json_bytes)
    register_exporter("xlsx", _export_xlsx_bytes)
    register_exporter("rtf", _export_rtf_bytes)

    _wire_anydoc()
