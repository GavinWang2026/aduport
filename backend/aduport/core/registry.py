"""Importer / exporter plugin registry (replaces hard-coded if/elif chains)."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from typing import Any

from aduport.core.document_model import DocumentModel

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
        from aduport.config import settings
        from aduport.importers.anydoc_bridge import (
            AnydocFallback,
            AnydocUnavailable,
            import_via_anydoc,
            prefer_native_importer,
            should_handle,
        )

        filename = args[0] if args and isinstance(args[0], str) else ""
        model: DocumentModel | None = None
        # ODT styles and RTF control words we write survive on the native
        # importer. anydoc flattens them to plain paragraphs.
        authored = prefer_native_importer(fmt, source_bytes)
        if should_handle(fmt) and not authored:
            try:
                model = import_via_anydoc(source_bytes, filename, fmt)
            except AnydocUnavailable:
                if native is None:
                    raise
            except AnydocFallback:
                if native is None or not settings.anydoc_fallback:
                    raise
            except Exception as exc:
                # Hard parse failures (malformed / encrypted / limits) used to
                # pierce the "never reduces coverage" net. Prefer native when
                # configured — unsupported formats still raise AnydocFallback.
                import logging

                logging.getLogger("aduport.registry").warning(
                    "anydoc failed for %s, trying native: %s", fmt, exc
                )
                if native is None or not settings.anydoc_fallback:
                    raise
        if model is None:
            if native is None:
                from aduport.core.exceptions import UnsupportedFormatError

                raise UnsupportedFormatError(
                    f"格式 {fmt} 需要内置原生解析扩展：执行 scripts/build_native.sh 编译 "
                    f"aduport_native._doc"
                )
            model = _call_native(native, source_bytes, args, kwargs)
            if not model.metadata.get("importer"):
                model.metadata["importer"] = (
                    "native_fallback" if should_handle(fmt) else "native"
                )
        if fmt == "docx":
            from aduport.importers.docx_ooxml import enrich_docx_model

            model = enrich_docx_model(model, source_bytes)
        elif fmt == "pptx":
            from aduport.importers.pptx_text import enrich_pptx_model

            model = enrich_pptx_model(model, source_bytes)
        elif fmt == "odt":
            from aduport.importers.odt_text import lift_dollar_math

            model = lift_dollar_math(model)
        return model

    _wrapped.__name__ = f"anydoc_or_{getattr(native, '__name__', fmt)}"
    setattr(_wrapped, "_anydoc_wired", True)
    return _wrapped


def _wire_anydoc() -> None:
    from aduport.importers.anydoc_bridge import (
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

    from aduport.exporters.json_exporter import import_json
    from aduport.exporters.xlsx_exporter import import_xlsx
    from aduport.importers.csv_importer import import_csv
    from aduport.importers.doc_importer import import_doc
    from aduport.importers.docx_importer import import_docx
    from aduport.importers.epub_importer import import_epub
    from aduport.importers.html_importer import import_html
    from aduport.importers.iwork_importer import import_keynote, import_numbers, import_pages
    from aduport.importers.latex_importer import import_latex
    from aduport.importers.md_importer import import_md
    from aduport.importers.mobi_importer import import_mobi
    from aduport.importers.odp_importer import import_odp
    from aduport.importers.ods_importer import import_ods
    from aduport.importers.odt_importer import import_odt
    from aduport.importers.ole_office import (
        import_pot,
        import_pps,
        import_ppt,
        import_xls,
        import_xlsb,
    )
    from aduport.importers.pdf_importer import import_pdf
    from aduport.importers.pptx_importer import import_pptx
    from aduport.importers.rtf_importer import import_rtf
    from aduport.importers.txt_importer import import_txt

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
    register_importer("pages", import_pages)
    register_importer("numbers", import_numbers)
    register_importer("keynote", import_keynote)
    from aduport.importers.image_importer import import_jpg, import_png
    from aduport.importers.ipynb_importer import import_ipynb
    from aduport.importers.msg_importer import import_msg
    from aduport.importers.rss_importer import import_atom, import_rss
    from aduport.importers.zip_importer import import_zip

    register_importer("msg", import_msg)
    register_importer("ipynb", import_ipynb)
    register_importer("zip", import_zip)
    register_importer("png", import_png)
    register_importer("jpg", import_jpg)
    register_importer("rss", import_rss)
    register_importer("atom", import_atom)
    from aduport.exporters.azw3_exporter import export_azw3, export_kf8
    from aduport.exporters.csv_exporter import export_csv
    from aduport.exporters.docx_exporter import export_docx
    from aduport.exporters.epub_exporter import export_epub
    from aduport.exporters.html_exporter import export_html
    from aduport.exporters.json_exporter import export_json
    from aduport.exporters.latex_exporter import export_latex
    from aduport.exporters.md_exporter import export_md
    from aduport.exporters.mobi_exporter import export_mobi
    from aduport.exporters.odt_exporter import export_odt
    from aduport.exporters.ods_exporter import export_ods
    from aduport.exporters.odp_exporter import export_odp
    from aduport.exporters.pdf_exporter import export_pdf
    from aduport.exporters.pptx_exporter import export_pptx
    from aduport.exporters.rag_elements import export_rag_payload  # noqa: F401 — used below
    from aduport.exporters.rtf_exporter import export_rtf
    from aduport.exporters.txt_exporter import export_txt
    from aduport.exporters.xlsx_exporter import export_xlsx

    def _export_md_bytes(model: DocumentModel, **_: Any) -> bytes:
        return export_md(model).encode("utf-8")

    def _export_docx_bytes(model: DocumentModel, **kwargs: Any) -> bytes:
        return export_docx(
            model,
            theme=kwargs.get("docx_theme"),
            auto_toc=kwargs.get("docx_auto_toc"),
        )

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
        pdf_engine: str | None = None,
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
            engine=pdf_engine,
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

    def _export_rag_bytes(model: DocumentModel, **kwargs: Any) -> bytes:
        import json

        from aduport.config import settings
        from aduport.exporters.rag_elements import export_rag_payload

        max_chars = kwargs.get("rag_max_chars")
        if max_chars is None:
            max_chars = getattr(settings, "rag_max_chars", 1200)
        overlap = kwargs.get("rag_chunk_overlap")
        if overlap is None:
            overlap = getattr(settings, "rag_chunk_overlap", 0)
        embed = kwargs.get("embed")
        if embed is None:
            embed = bool(getattr(settings, "embed_default", False))
        vector_persist = kwargs.get("vector_persist")
        if vector_persist is None:
            vector_persist = bool(getattr(settings, "vector_dir", "") or "")
        payload = export_rag_payload(
            model,
            max_chars=int(max_chars or 1200),
            chunk_overlap=int(overlap or 0),
            embed=bool(embed),
            embed_backend=kwargs.get("embed_backend"),
            embed_model=kwargs.get("embed_model"),
            vector_persist=bool(vector_persist) and bool(embed),
            vector_collection=str(
                kwargs.get("vector_collection")
                or getattr(settings, "vector_collection", "default")
                or "default"
            ),
        )
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

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
    register_exporter("rag", _export_rag_bytes)
    register_exporter("xlsx", _export_xlsx_bytes)
    register_exporter("rtf", _export_rtf_bytes)

    _wire_anydoc()
