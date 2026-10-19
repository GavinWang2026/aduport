"""Unified conversion entry point (single + batch) with registry + cache."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aduport.config import settings
from aduport.core.audit import audit_event
from aduport.core.cache import result_cache
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    ExportError,
    FileTooLargeError,
    InvalidDocumentError,
    AduPortError,
    UnsupportedFormatError,
)
from aduport.core.license import license_seat
from aduport.core.metrics import metrics
from aduport.core.registry import (
    ensure_builtin_plugins,
    get_exporter,
    get_importer,
    registered_export_formats,
    registered_import_formats,
)
from aduport.core.security import validate_upload
from aduport.core.virus_scan import scan_bytes

# Import formats include txt/html; export: md/docx/pdf/html/txt/epub
SUPPORTED_SOURCE_FORMATS = frozenset(
    {
        "docx",
        "pdf",
        "md",
        "txt",
        "html",
        "pptx",
        "epub",
        "mobi",
        "azw3",
        "kf8",
        "latex",
        "odt",
        "rtf",
        "doc",
        "json",
        "xlsx",
        "csv",
        # --- Office / ODF builtins (native; anydoc preferred when compiled) ---
        "ppt",
        "pps",
        "pot",
        "xls",
        "xlsb",
        "xlsm",
        "ods",
        "odp",
        "docm",
        "pptm",
        "ppsx",
        "ppsm",
        # MarkItDown-gap sources (local AST; not Office/PDF replacements)
        "msg",
        "ipynb",
        "zip",
        "png",
        "jpg",
        "rss",
        "atom",
    }
)
SUPPORTED_TARGET_FORMATS = frozenset(
    {
        "docx",
        "pdf",
        "md",
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
        "ods",
        "odp",
        "json",
        "xlsx",
        "rtf",
    }
)
# rag is an export mode (aduport.rag_elements JSON), not a file format.
# convert() still accepts rag / elements / rag_elements / rag-json.
EXPORT_MODES = frozenset({"rag"})
# Own-export round-trip of pdf/csv/txt/rtf now keeps headings, tables, and
# images. The 23:10 matrix still recorded those losses; this map stays empty
# so the public label matches current behavior. Third-party files that are
# not our exports can still flatten.
TARGET_STRUCTURE_LIMITS: dict[str, tuple[str, ...]] = {}
SUPPORTED_FORMATS = SUPPORTED_SOURCE_FORMATS | SUPPORTED_TARGET_FORMATS

MIME_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "epub": "application/epub+zip",
    "mobi": "application/x-mobipocket-ebook",
    "azw3": "application/vnd.amazon.ebook",
    "kf8": "application/vnd.amazon.ebook",
    "latex": "application/x-latex",
    "csv": "text/csv; charset=utf-8",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "rtf": "application/rtf",
    "doc": "application/msword",
    "json": "application/json; charset=utf-8",
    "rag": "application/json; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "msg": "application/vnd.ms-outlook",
    "ipynb": "application/x-ipynb+json",
    "zip": "application/zip",
    "png": "image/png",
    "jpg": "image/jpeg",
    "rss": "application/rss+xml",
    "atom": "application/atom+xml",
}


# Importers that take (bytes, filename); the rest take (bytes) only.
# anydoc-backed formats are included so parse errors can name the file.
_FILENAME_AWARE_FORMATS = frozenset(
    {
        "docx",
        "pdf",
        "html",
        "txt",
        "pptx",
        "epub",
        "mobi",
        "ppt",
        "pps",
        "pot",
        "xls",
        "xlsb",
        "xlsm",
        "ods",
        "odp",
        "docm",
        "pptm",
        "ppsx",
        "ppsm",
        "msg",
        "md",
        "ipynb",
        "zip",
        "png",
        "jpg",
        "rss",
        "atom",
        "rtf",
        "latex",
    }
)


@dataclass
class BatchItemResult:
    source: str
    target: str | None = None
    success: bool = False
    error: str | None = None
    code: str | None = None
    degraded: bool = False


@dataclass
class BatchResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    degraded: int = 0
    items: list[BatchItemResult] = field(default_factory=list)


def _normalize_format(fmt: str) -> str:
    key = fmt.lower().lstrip(".")
    if key in ("elements", "rag_elements", "rag-json"):
        return "rag"
    return key


def is_accepted_target(fmt: str) -> bool:
    """True for a declared file target or an export mode such as rag."""
    key = _normalize_format(fmt)
    return key in SUPPORTED_TARGET_FORMATS or key in EXPORT_MODES


def _validate_formats(source_format: str, target_format: str) -> tuple[str, str]:
    ensure_builtin_plugins()
    src = _normalize_format(source_format)
    tgt = _normalize_format(target_format)
    if src not in registered_import_formats() and src not in SUPPORTED_SOURCE_FORMATS:
        raise UnsupportedFormatError(
            f"不支持的源格式: {source_format}。支持: {sorted(SUPPORTED_SOURCE_FORMATS)}"
        )
    if (
        tgt not in registered_export_formats()
        and tgt not in SUPPORTED_TARGET_FORMATS
        and tgt not in EXPORT_MODES
    ):
        raise UnsupportedFormatError(
            f"不支持的目标格式: {target_format}。"
            f"支持: {sorted(SUPPORTED_TARGET_FORMATS)}；导出模式: {sorted(EXPORT_MODES)}"
        )
    if get_importer(src) is None:
        # anydoc owns several formats (xls/xlsb/ods/odp/ppt/rtf/odt/…) that have
        # no native importer; don't reject them before the anydoc route runs.
        try:
            from aduport.importers.anydoc_bridge import should_handle as _anydoc_owns

            if not _anydoc_owns(src):
                raise UnsupportedFormatError(f"未注册源格式导入器: {source_format}")
        except ImportError:
            raise UnsupportedFormatError(f"未注册源格式导入器: {source_format}")
    if get_exporter(tgt) is None:
        raise UnsupportedFormatError(f"未注册目标格式导出器: {target_format}")
    return src, tgt


def _markdown_needs_normalize(source_bytes: bytes) -> bool:
    """True when Markdown must be decoded and written back as clean UTF-8."""
    if any(b < 32 and b not in (9, 10, 13) for b in source_bytes):
        return True
    try:
        source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _check_size(source_bytes: bytes, filename: str = "") -> None:
    if not source_bytes:
        raise EmptyFileError(f"文件为空: {filename or '(unnamed)'}")
    if len(source_bytes) > settings.max_upload_bytes:
        raise FileTooLargeError(
            f"文件过大: {filename or '(unnamed)'} "
            f"({len(source_bytes)} bytes)，限制 {settings.max_upload_mb} MB"
        )


def _import(source_bytes: bytes, source_format: str, filename: str = "") -> DocumentModel:
    ensure_builtin_plugins()
    from aduport.importers.import_guard import import_failure_budget

    fmt = _normalize_format(source_format)
    importer = get_importer(fmt)
    if importer is None:
        # Route anydoc-owned formats (anydoc-only + allow-listed shared) to the
        # vendored Rust parser via the model channel.
        try:
            from aduport.importers.anydoc_bridge import (
                AnydocUnavailable,
                import_via_anydoc,
            )
            from aduport.importers.anydoc_bridge import (
                should_handle as _anydoc_owns,
            )
        except ImportError:
            importer = None
        else:
            if _anydoc_owns(fmt):
                try:
                    return import_via_anydoc(source_bytes, filename, fmt)
                except AnydocUnavailable as exc:
                    raise CorruptedFileError(
                        f"anydoc 原生解析扩展未安装，无法处理 {source_format}: {exc}"
                    ) from exc
        raise UnsupportedFormatError(f"不支持的源格式: {source_format}")
    try:
        with import_failure_budget():
            if fmt in _FILENAME_AWARE_FORMATS:
                return importer(source_bytes, filename or f"input.{fmt}")
            return importer(source_bytes)
    except AduPortError:
        raise
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError(f"文本编码无效，请使用 UTF-8: {filename}") from exc
    except Exception as exc:
        raise CorruptedFileError(
            f"无法解析文件（可能已损坏或格式不正确）: {filename or source_format} — {exc}"
        ) from exc


def _export(
    model: DocumentModel,
    target_format: str,
    *,
    pdf_font_family: str | None = None,
    pdf_custom_css: str | None = None,
    pdf_page_size: str | None = None,
    pdf_orientation: str | None = None,
    pdf_header: str | None = None,
    pdf_footer: str | None = None,
    pdf_page_numbers: bool | None = None,
    pdf_toc: bool | None = None,
    docx_theme: str | None = None,
    docx_auto_toc: bool | None = None,
    latex_plain_comments: bool | None = None,
    csv_table_only: bool | None = None,
    rag_max_chars: int | None = None,
    rag_chunk_overlap: int | None = None,
    embed: bool | None = None,
    embed_backend: str | None = None,
    embed_model: str | None = None,
    vector_persist: bool | None = None,
    vector_collection: str | None = None,
    pdf_engine: str | None = None,
) -> bytes:
    ensure_builtin_plugins()
    fmt = _normalize_format(target_format)
    exporter = get_exporter(fmt)
    if exporter is None:
        raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")
    from aduport.exporters.export_warnings import (
        begin_export_warnings,
        set_last_export_warnings,
        take_export_warnings,
        warn_export,
    )

    from aduport.core.export_flags import bind_export_flags, reset_export_flags

    flag_token = bind_export_flags(
        docx_auto_toc=docx_auto_toc,
        latex_plain_comments=latex_plain_comments,
        csv_table_only=csv_table_only,
    )
    token = begin_export_warnings()
    enc = str((model.metadata or {}).get("source_encoding") or "")
    if enc and enc != "utf-8":
        warn_export(model, f"source-encoding: {enc} (detected)")
    if (model.metadata or {}).get("nul_stripped"):
        warn_export(model, "stripped NUL/control characters before export")
    if (model.metadata or {}).get("structure_fidelity") == "below_text":
        warn_export(
            model,
            "degraded: PDF text-layer structure fidelity is below text fidelity",
        )
    ocr_notice = str((model.metadata or {}).get("ocr_notice") or "").strip()
    if ocr_notice:
        warn_export(model, ocr_notice)
    try:
        try:
            if settings.heading_numbering:
                from aduport.core.heading_numbers import apply_heading_numbers

                model = apply_heading_numbers(model)
            if settings.image_compress:
                from aduport.core.image_ops import maybe_compress_images

                model = maybe_compress_images(model)
            result = exporter(
                model,
                pdf_font_family=pdf_font_family,
                pdf_custom_css=pdf_custom_css,
                pdf_page_size=pdf_page_size,
                pdf_orientation=pdf_orientation,
                pdf_header=pdf_header,
                pdf_footer=pdf_footer,
                pdf_page_numbers=pdf_page_numbers,
                pdf_toc=pdf_toc,
                docx_theme=docx_theme,
                rag_max_chars=rag_max_chars,
                rag_chunk_overlap=rag_chunk_overlap,
                embed=embed,
                embed_backend=embed_backend,
                embed_model=embed_model,
                vector_persist=vector_persist,
                vector_collection=vector_collection,
                pdf_engine=pdf_engine,
            )
            return result if isinstance(result, bytes) else str(result).encode("utf-8")
        except AduPortError:
            raise
        except Exception as exc:
            raise ExportError(f"导出失败 ({target_format}): {exc}") from exc
    finally:
        reset_export_flags(flag_token)
        collected = take_export_warnings(token)
        existing = model.metadata.get("export_warnings") or []
        if not isinstance(existing, list):
            existing = []
        merged = list(dict.fromkeys([*existing, *collected]))
        if merged:
            model.metadata["export_warnings"] = merged
        set_last_export_warnings(merged)


_LO_PDF_SOURCES = frozenset(
    {
        "docx",
        "doc",
        "docm",
        "odt",
        "rtf",
        "xlsx",
        "xls",
        "xlsm",
        "xlsb",
        "ods",
        "pptx",
        "ppt",
        "pptm",
        "odp",
        "html",
        "csv",
    }
)


def _try_libreoffice_pdf(
    source_bytes: bytes, src: str, tgt: str, filename: str
) -> bytes | None:
    """Optional layout-faithful office→PDF via system soffice (never bundled)."""
    if tgt != "pdf" or src not in _LO_PDF_SOURCES:
        return None
    try:
        from aduport.exporters.libreoffice_bridge import (
            LibreOfficeUnavailable,
            convert_via_libreoffice,
            libreoffice_enabled,
        )
    except ImportError:
        return None
    if not libreoffice_enabled():
        return None
    try:
        return convert_via_libreoffice(
            source_bytes,
            filename=filename or f"input.{src}",
            target_ext="pdf",
        )
    except LibreOfficeUnavailable:
        return None
    except Exception:
        # Fall through to AST→Weasy so enabling LO never hard-breaks conversions.
        return None


def _try_anydoc_fast_markdown(
    source_bytes: bytes, src: str, tgt: str, filename: str
) -> bytes | None:
    """Serialize Markdown with anydoc directly, or None to use the model path.

    Target Markdown is anydoc's native output, so routing it through the
    intermediate model would only discard fidelity (cell spans, list markers,
    inline math). Post-processing that rewrites the model — heading numbering,
    image compression — forces the model path instead.
    """
    if tgt != "md" or not settings.anydoc_fast_markdown:
        return None
    # anydoc's pdf-inspector writes a third-party table as one line
    # ("Wide head End Tall B2 C2 B3 C3") and glues list items to the next
    # paragraph. The text-layer model keeps the table and the lists; cues
    # for files we wrote live in an attachment, not in that fast channel.
    if src == "pdf":
        return None
    if settings.heading_numbering or settings.image_compress:
        return None
    # v9 P2: span-aware HTML tables live on the model→export_md path; skip fast
    # for office sources when html_table_for_span is on.
    if getattr(settings, "html_table_for_span", True) and src in {
        "docx",
        "doc",
        "docm",
        "xlsx",
        "xlsm",
    }:
        return None
    try:
        from aduport.importers.anydoc_bridge import (
            AnydocFallback,
            AnydocUnavailable,
            markdown_via_anydoc,
            should_handle_fast_md,
            supports,
        )

        if not should_handle_fast_md(src):
            return None
        if not supports(src):
            return None
        from aduport.importers.anydoc_bridge import prefer_native_importer

        if prefer_native_importer(src, source_bytes):
            return None
        text = markdown_via_anydoc(source_bytes, src, filename)
        from aduport.exporters.html_render import PDF_CUE_MARK

        if PDF_CUE_MARK in text:
            # Visual page plus cue page. The text importer folds them.
            return None
        return text.encode("utf-8")
    except (AnydocUnavailable, AnydocFallback, CorruptedFileError):
        # OLE sources (ppt/pps/pot/xls) often fail the fast Markdown channel
        # with a structural error the model importer can still read.
        return None
    except AduPortError:
        raise
    except Exception:
        # Never let the accelerator break a conversion the model path can do.
        return None


def convert(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    filename: str = "",
    *,
    pdf_font_family: str | None = None,
    pdf_custom_css: str | None = None,
    pdf_page_size: str | None = None,
    pdf_orientation: str | None = None,
    pdf_header: str | None = None,
    pdf_footer: str | None = None,
    pdf_page_numbers: bool | None = None,
    pdf_toc: bool | None = None,
    docx_theme: str | None = None,
    docx_auto_toc: bool | None = None,
    latex_plain_comments: bool | None = None,
    csv_table_only: bool | None = None,
    rag_max_chars: int | None = None,
    rag_chunk_overlap: int | None = None,
    embed: bool | None = None,
    embed_backend: str | None = None,
    embed_model: str | None = None,
    vector_persist: bool | None = None,
    vector_collection: str | None = None,
    pdf_engine: str | None = None,
    ocr_engine: str | None = None,
    force_ocr: bool | None = None,
    ocr_max_pages: int | None = None,
    skip_pdf_sidecar: bool | None = None,
    cache_bypass: bool | None = None,
    fetch_images: bool | None = None,
    client: str = "",
    skip_magic: bool = False,
) -> bytes:
    """Convert document bytes from source_format to target_format."""
    if not isinstance(source_bytes, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"source_bytes must be bytes-like, got {type(source_bytes).__name__}"
        )
    source_bytes = bytes(source_bytes)
    if not isinstance(source_format, str) or not source_format.strip():
        raise TypeError("source_format must be a non-empty str")
    if not isinstance(target_format, str) or not target_format.strip():
        raise TypeError("target_format must be a non-empty str")
    src, tgt = _validate_formats(source_format, target_format)
    _check_size(source_bytes, filename)

    if src == tgt and not (src == "md" and _markdown_needs_normalize(source_bytes)):
        # Still validate identity passthrough unless skipped.
        # Markdown that is not clean UTF-8 (GBK, UTF-16, NUL) is re-encoded below.
        # Identity is not a real conversion — exclude from conversions_total.
        if settings.magic_bytes_check and not skip_magic:
            validate_upload(source_bytes, src, filename)
        scan_bytes(source_bytes, filename or f"input.{src}")
        metrics.record_identity_passthrough(src)
        audit_event(
            action="convert_identity",
            source_format=src,
            target_format=tgt,
            filename=filename,
            success=True,
            client=client,
            cached=False,
            bytes_in=len(source_bytes),
            bytes_out=len(source_bytes),
            detail="identity_passthrough",
        )
        return source_bytes

    # §6.4: HTML → EPUB 直出（保留正文结构，跳过 AST 往返）
    if src == "html" and tgt == "epub" and not settings.heading_numbering:
        if settings.magic_bytes_check and not skip_magic:
            validate_upload(source_bytes, src, filename)
        scan_bytes(source_bytes, filename or f"input.{src}")
        from aduport.exporters.epub_exporter import export_epub_from_html

        stem = (filename or "document").rsplit("/", 1)[-1]
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        out = export_epub_from_html(source_bytes, title=stem)
        metrics.record_success(src, tgt, 0.0, importer="html_epub_direct")
        audit_event(
            action="convert",
            source_format=src,
            target_format=tgt,
            filename=filename,
            success=True,
            client=client,
            cached=False,
            bytes_in=len(source_bytes),
            bytes_out=len(out),
            detail="html_epub_direct",
        )
        return out

    extra = "|".join(
        [
            pdf_font_family or "",
            pdf_custom_css or "",
            pdf_page_size or "",
            pdf_orientation or "",
            pdf_header or "",
            pdf_footer or "",
            str(pdf_page_numbers),
            str(pdf_toc),
            docx_theme or settings.docx_theme or "",
            str(rag_max_chars if rag_max_chars is not None else getattr(settings, "rag_max_chars", 1200)),
            str(rag_chunk_overlap if rag_chunk_overlap is not None else getattr(settings, "rag_chunk_overlap", 0)),
            f"emb={int(bool(embed if embed is not None else getattr(settings, 'embed_default', False)))}",
            f"ebb={embed_backend or getattr(settings, 'embed_backend', 'hash')}",
            f"emm={embed_model or getattr(settings, 'embed_model', '')}",
            f"vp={int(bool(vector_persist))}",
            f"vc={vector_collection or getattr(settings, 'vector_collection', 'default')}",
            f"pe={pdf_engine or ''}",
            f"oe={ocr_engine or ''}",
            f"fo={int(bool(force_ocr))}",
            f"omp={ocr_max_pages if ocr_max_pages is not None else ''}",
            f"ss={int(bool(skip_pdf_sidecar))}",
            str(fetch_images),
            f"hn={settings.heading_numbering}",
            f"dc={settings.docx_export_comments}",
            f"ic={settings.image_compress}:{settings.image_max_edge}:{settings.image_jpeg_quality}",
            f"sv={int(bool(settings.stream_convert))}",
            f"smb={settings.stream_min_bytes}",
            # v6/T1-#6: these change the rendered output but were absent from
            # the cache key, so flipping them served the previous result for
            # the whole TTL window (verified: KaTeX on/off returned identical
            # HTML; docx_auto_toc on/off returned identical docx).
            f"katex={int(bool(getattr(settings, 'html_katex', False)))}",
            f"toc={int(bool(settings.docx_auto_toc if docx_auto_toc is None else docx_auto_toc))}",
            f"lpc={int(bool(settings.latex_plain_comments if latex_plain_comments is None else latex_plain_comments))}",
            f"cto={int(bool(csv_table_only))}",
            f"scc={getattr(settings, 'stream_chunk_chars', 0)}",
            f"tfrh={int(bool(getattr(settings, 'table_first_row_header', False)))}",
            f"lo={int(bool(getattr(settings, 'libreoffice_enabled', False)))}",
            f"hts={int(bool(getattr(settings, 'html_table_for_span', True)))}",
            f"llm={int(bool(getattr(settings, 'llm_mode', False)))}",
        ]
    )
    cache_key = result_cache.make_key(source_bytes, src, tgt, extra)
    bypass = bool(cache_bypass)
    cached = None if bypass else result_cache.get(cache_key)
    if cached is not None:
        if not settings.cache_skip_revalidate:
            if settings.magic_bytes_check and not skip_magic:
                validate_upload(source_bytes, src, filename)
            scan_bytes(source_bytes, filename or f"input.{src}")
        audit_event(
            action="convert",
            source_format=src,
            target_format=tgt,
            filename=filename,
            success=True,
            client=client,
            cached=True,
            bytes_in=len(source_bytes),
            bytes_out=len(cached),
        )
        metrics.record_success(src, tgt, 0.0)
        from aduport.core.billing import emit_usage_event

        emit_usage_event(
            source_format=src,
            target_format=tgt,
            bytes_in=len(source_bytes),
            bytes_out=len(cached),
            cached=True,
        )
        return cached

    if settings.magic_bytes_check and not skip_magic:
        validate_upload(source_bytes, src, filename)
    scan_bytes(source_bytes, filename or f"input.{src}")

    with license_seat():
        started = time.perf_counter()
        importer = ""
        try:
            out = _try_libreoffice_pdf(source_bytes, src, tgt, filename)
            if out is not None:
                importer = "libreoffice"
                elapsed = time.perf_counter() - started
                result_cache.put(cache_key, out)
                metrics.record_success(src, tgt, elapsed, importer=importer)
                audit_event(
                    action="convert",
                    source_format=src,
                    target_format=tgt,
                    filename=filename,
                    success=True,
                    client=client,
                    cached=False,
                    bytes_in=len(source_bytes),
                    bytes_out=len(out),
                    importer=importer,
                )
                from aduport.core.billing import emit_usage_event

                emit_usage_event(
                    source_format=src,
                    target_format=tgt,
                    bytes_in=len(source_bytes),
                    bytes_out=len(out),
                    importer=importer,
                )
                return out

            out = _try_anydoc_fast_markdown(source_bytes, src, tgt, filename)
            if out is not None:
                importer = "anydoc_fast_md"
                metrics.record_anydoc_hit(channel="fast_md")
                elapsed = time.perf_counter() - started
                result_cache.put(cache_key, out)
                metrics.record_success(src, tgt, elapsed, importer=importer)
                audit_event(
                    action="convert",
                    source_format=src,
                    target_format=tgt,
                    filename=filename,
                    success=True,
                    client=client,
                    cached=False,
                    bytes_in=len(source_bytes),
                    bytes_out=len(out),
                    importer=importer,
                )
                from aduport.core.billing import emit_usage_event

                emit_usage_event(
                    source_format=src,
                    target_format=tgt,
                    bytes_in=len(source_bytes),
                    bytes_out=len(out),
                    importer=importer,
                )
                return out

            from aduport.core.convert_context import import_opts_scope
            from aduport.core.stream_convert import can_stream_convert, convert_streaming

            # 边解析边转：大文本格式分块导入/导出，降低峰值 AST 内存。
            # P0-2: 导出选项必须透传，否则大文件静默退回默认排版且与缓存 key 矛盾。
            if can_stream_convert(src, tgt) and len(source_bytes) >= settings.stream_min_bytes:
                out = convert_streaming(
                    source_bytes,
                    src,
                    tgt,
                    filename=filename,
                    fetch_images=(settings.fetch_remote_images if fetch_images is None else fetch_images),
                    pdf_font_family=pdf_font_family,
                    pdf_custom_css=pdf_custom_css,
                    pdf_page_size=pdf_page_size,
                    pdf_orientation=pdf_orientation,
                    pdf_header=pdf_header,
                    pdf_footer=pdf_footer,
                    pdf_page_numbers=pdf_page_numbers,
                    pdf_toc=pdf_toc,
                    docx_theme=docx_theme,
                    docx_auto_toc=docx_auto_toc,
                    latex_plain_comments=latex_plain_comments,
                    pdf_engine=pdf_engine,
                )
                importer = "stream"
            else:
                with import_opts_scope(
                    pdf_engine=pdf_engine,
                    ocr_engine=ocr_engine,
                    force_ocr=force_ocr,
                    ocr_max_pages=ocr_max_pages,
                    skip_pdf_sidecar=skip_pdf_sidecar,
                ):
                    model = _import(source_bytes, src, filename)
                importer = str(model.metadata.get("importer") or "native")
                # anydoc hit/degrade telemetry: when anydoc was the designated engine,
                # record whether it actually performed the conversion.
                try:
                    from aduport.importers.anydoc_bridge import should_handle as _anydoc_designated

                    if _anydoc_designated(src):
                        if importer == "anydoc":
                            metrics.record_anydoc_hit(channel="model")
                        else:
                            metrics.record_anydoc_degrade()
                except Exception:
                    pass
                name = Path(filename).name if filename else ""
                if name and not str((model.metadata or {}).get("filename") or "").strip():
                    model.metadata["filename"] = name
                from aduport.core.local_images import resolve_local_images

                model = resolve_local_images(model, filename=filename)
                do_fetch = settings.fetch_remote_images if fetch_images is None else fetch_images
                if do_fetch:
                    from aduport.core.remote_images import resolve_remote_images

                    model = resolve_remote_images(model)
                out = _export(
                    model,
                    tgt,
                    pdf_font_family=pdf_font_family,
                    pdf_custom_css=pdf_custom_css,
                    pdf_page_size=pdf_page_size,
                    pdf_orientation=pdf_orientation,
                    pdf_header=pdf_header,
                    pdf_footer=pdf_footer,
                    pdf_page_numbers=pdf_page_numbers,
                    pdf_toc=pdf_toc,
                    docx_theme=docx_theme,
                    docx_auto_toc=docx_auto_toc,
                    latex_plain_comments=latex_plain_comments,
                    csv_table_only=csv_table_only,
                    rag_max_chars=rag_max_chars,
                    rag_chunk_overlap=rag_chunk_overlap,
                    embed=embed,
                    embed_backend=embed_backend,
                    embed_model=embed_model,
                    vector_persist=vector_persist,
                    vector_collection=vector_collection,
                    pdf_engine=pdf_engine,
                )
            elapsed = time.perf_counter() - started
            if not bypass:
                result_cache.put(cache_key, out)
            metrics.record_success(src, tgt, elapsed, importer=importer)
            audit_event(
                action="convert",
                source_format=src,
                target_format=tgt,
                filename=filename,
                success=True,
                client=client,
                cached=False,
                bytes_in=len(source_bytes),
                bytes_out=len(out),
                importer=importer,
            )
            from aduport.core.billing import emit_usage_event

            emit_usage_event(
                source_format=src,
                target_format=tgt,
                bytes_in=len(source_bytes),
                bytes_out=len(out),
                importer=importer,
            )
            return out
        except Exception as exc:
            fail_code = exc.code if isinstance(exc, AduPortError) else "conversion_error"
            metrics.record_failure(src, tgt, code=fail_code)
            audit_event(
                action="convert",
                source_format=src,
                target_format=tgt,
                filename=filename,
                success=False,
                detail=str(exc),
                client=client,
                bytes_in=len(source_bytes),
                importer=importer,
            )
            raise



def convert_directory(
    input_dir: Path | str,
    output_dir: Path | str,
    source_format: str,
    target_format: str,
    *,
    recursive: bool = False,
    pdf_font_family: str | None = None,
    pdf_custom_css: str | None = None,
    pdf_page_size: str | None = None,
    pdf_orientation: str | None = None,
    pdf_header: str | None = None,
    pdf_footer: str | None = None,
    pdf_page_numbers: bool | None = None,
    pdf_toc: bool | None = None,
    docx_theme: str | None = None,
    docx_auto_toc: bool | None = None,
    latex_plain_comments: bool | None = None,
    rag_max_chars: int | None = None,
    rag_chunk_overlap: int | None = None,
    embed: bool | None = None,
    force_ocr: bool | None = None,
    pdf_engine: str | None = None,
    cache_bypass: bool | None = None,
    workers: int | None = None,
    cancel_event: Any | None = None,
    progress_cb: Any | None = None,
    force: bool = False,
) -> BatchResult:
    """Batch-convert a directory (thread-pool). Set cancel_event to interrupt."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    src, tgt = _validate_formats(source_format, target_format)
    in_path = Path(input_dir)
    out_path = Path(output_dir)

    if not in_path.is_dir():
        raise InvalidDocumentError(f"输入目录不存在: {in_path}")

    out_path.mkdir(parents=True, exist_ok=True)
    ext_map = {
        "md": "md",
        "txt": "txt",
        "html": "html",
        "docx": "docx",
        "pdf": "pdf",
        "pptx": "pptx",
        "epub": "epub",
        "mobi": "mobi",
        "odt": "odt",
        "rtf": "rtf",
        "doc": "doc",
        "json": "json",
        "xlsx": "xlsx",
        "csv": "csv",
        "rag": "json",
    }
    ext = ext_map.get(src, src)
    pattern = f"**/*.{ext}" if recursive else f"*.{ext}"
    files = sorted(p for p in in_path.glob(pattern) if p.is_file())

    result = BatchResult(total=len(files))
    n_workers = max(1, workers if workers is not None else settings.batch_workers)

    def _one(file_path: Path) -> BatchItemResult:
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return BatchItemResult(source=str(file_path), error="cancelled", code="cancelled")
        rel = file_path.relative_to(in_path)
        out_ext = ext_map.get(tgt, tgt)
        target_file = out_path / rel.with_suffix(f".{out_ext}")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        item = BatchItemResult(source=str(file_path), target=str(target_file))
        try:
            if target_file.exists() and not force:
                raise InvalidDocumentError(
                    f"输出已存在（使用 force=True / --force 覆盖）: {target_file}"
                )
            size = file_path.stat().st_size
            if size > settings.max_upload_bytes:
                raise FileTooLargeError(
                    f"文件过大: {file_path.name} ({size} bytes)，限制 {settings.max_upload_mb} MB"
                )
            converted = convert(
                file_path.read_bytes(),
                src,
                tgt,
                filename=file_path.name,
                pdf_font_family=pdf_font_family,
                pdf_custom_css=pdf_custom_css,
                pdf_page_size=pdf_page_size,
                pdf_orientation=pdf_orientation,
                pdf_header=pdf_header,
                pdf_footer=pdf_footer,
                pdf_page_numbers=pdf_page_numbers,
                pdf_toc=pdf_toc,
                docx_theme=docx_theme,
                docx_auto_toc=docx_auto_toc,
                latex_plain_comments=latex_plain_comments,
                rag_max_chars=rag_max_chars,
                rag_chunk_overlap=rag_chunk_overlap,
                embed=embed,
                force_ocr=force_ocr,
                pdf_engine=pdf_engine,
                cache_bypass=cache_bypass,
            )
            target_file.write_bytes(converted)
            item.success = True
            if tgt == "pdf" and b"aduport-pdf-degraded: true" in converted[:1024]:
                item.degraded = True
        except AduPortError as exc:
            item.error = exc.message
            item.code = exc.code
        except Exception as exc:
            item.error = str(exc)
            item.code = "conversion_error"
        return item

    done = 0
    pool = ThreadPoolExecutor(max_workers=n_workers)
    try:
        futs = {pool.submit(_one, fp): fp for fp in files}
        for fut in as_completed(futs):
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                for pending in futs:
                    pending.cancel()
                break
            try:
                item = fut.result()
            except Exception as exc:
                item = BatchItemResult(
                    source=str(futs.get(fut, "")),
                    error=str(exc),
                    code="conversion_error",
                )
            result.items.append(item)
            if item.success:
                result.succeeded += 1
                if item.degraded:
                    result.degraded += 1
            else:
                result.failed += 1
            done += 1
            if progress_cb:
                try:
                    progress_cb(done, result.total, item)
                except Exception:
                    pass
    finally:
        # cancel_futures so cancel_event actually stops work (P1 #16)
        pool.shutdown(wait=False, cancel_futures=True)
    return result


def convert_directory_async(
    input_dir: Path | str,
    source_format: str,
    target_format: str,
    *,
    recursive: bool = False,
    client: str = "",
    rag_max_chars: int | None = None,
    rag_chunk_overlap: int | None = None,
    docx_theme: str | None = None,
) -> list[str]:
    """Enqueue each file as a job_store job; returns job ids."""
    from aduport.core.jobs import job_store

    src, tgt = _validate_formats(source_format, target_format)
    in_path = Path(input_dir)
    if not in_path.is_dir():
        raise InvalidDocumentError(f"输入目录不存在: {in_path}")
    pattern = f"**/*.{src}" if recursive else f"*.{src}"
    files = sorted(p for p in in_path.glob(pattern) if p.is_file())
    job_ids: list[str] = []
    for file_path in files:
        job = job_store.submit(
            content=file_path.read_bytes(),
            filename=file_path.name,
            source_format=src,
            target_format=tgt,
            client=client,
            result_filename=file_path.with_suffix(f".{tgt if tgt != 'rag' else 'json'}").name,
            docx_theme=docx_theme,
            rag_max_chars=rag_max_chars,
            rag_chunk_overlap=rag_chunk_overlap,
        )
        job_ids.append(job.id)
    return job_ids
