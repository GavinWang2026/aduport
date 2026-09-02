"""Unified conversion entry point (single + batch) with registry + cache."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.audit import audit_event
from app.core.cache import result_cache
from app.core.document_model import DocumentModel
from app.core.exceptions import (
    CorruptedFileError,
    EmptyFileError,
    ExportError,
    FileTooLargeError,
    InvalidDocumentError,
    MdBridgeError,
    UnsupportedFormatError,
)
from app.core.metrics import metrics
from app.core.registry import (
    ensure_builtin_plugins,
    get_exporter,
    get_importer,
    registered_export_formats,
    registered_import_formats,
)
from app.core.security import validate_upload
from app.core.virus_scan import scan_bytes

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
        # --- Formats read through the anydoc bridge ---
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
        "json",
        "xlsx",
        "rtf",
    }
)
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
    "rtf": "application/rtf",
    "doc": "application/msword",
    "json": "application/json; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    }
)


@dataclass
class BatchItemResult:
    source: str
    target: str | None = None
    success: bool = False
    error: str | None = None
    code: str | None = None


@dataclass
class BatchResult:
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    items: list[BatchItemResult] = field(default_factory=list)


def _normalize_format(fmt: str) -> str:
    return fmt.lower().lstrip(".")


def _validate_formats(source_format: str, target_format: str) -> tuple[str, str]:
    ensure_builtin_plugins()
    src = _normalize_format(source_format)
    tgt = _normalize_format(target_format)
    if src not in registered_import_formats() and src not in SUPPORTED_SOURCE_FORMATS:
        raise UnsupportedFormatError(
            f"不支持的源格式: {source_format}。支持: {sorted(SUPPORTED_SOURCE_FORMATS)}"
        )
    if tgt not in registered_export_formats() and tgt not in SUPPORTED_TARGET_FORMATS:
        raise UnsupportedFormatError(
            f"不支持的目标格式: {target_format}。支持: {sorted(SUPPORTED_TARGET_FORMATS)}"
        )
    if get_importer(src) is None:
        # anydoc owns several formats (xls/xlsb/ods/odp/ppt/rtf/odt/…) that have
        # no native importer; don't reject them before the anydoc route runs.
        try:
            from app.importers.anydoc_bridge import should_handle as _anydoc_owns

            if not _anydoc_owns(src):
                raise UnsupportedFormatError(f"未注册源格式导入器: {source_format}")
        except ImportError:
            raise UnsupportedFormatError(f"未注册源格式导入器: {source_format}")
    if get_exporter(tgt) is None:
        raise UnsupportedFormatError(f"未注册目标格式导出器: {target_format}")
    return src, tgt


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
    fmt = _normalize_format(source_format)
    importer = get_importer(fmt)
    if importer is None:
        # Route anydoc-owned formats (anydoc-only + allow-listed shared) to the
        # vendored Rust parser via the model channel.
        try:
            from app.importers.anydoc_bridge import (
                AnydocUnavailable,
                import_via_anydoc,
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
        if fmt in _FILENAME_AWARE_FORMATS:
            return importer(source_bytes, filename or f"input.{fmt}")
        return importer(source_bytes)
    except MdBridgeError:
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
) -> bytes:
    ensure_builtin_plugins()
    fmt = _normalize_format(target_format)
    exporter = get_exporter(fmt)
    if exporter is None:
        raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")
    try:
        if settings.heading_numbering:
            from app.core.heading_numbers import apply_heading_numbers

            model = apply_heading_numbers(model)
        if settings.image_compress:
            from app.core.image_ops import maybe_compress_images

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
        )
        return result if isinstance(result, bytes) else str(result).encode("utf-8")
    except MdBridgeError:
        raise
    except Exception as exc:
        raise ExportError(f"导出失败 ({target_format}): {exc}") from exc


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
    if settings.heading_numbering or settings.image_compress:
        return None
    try:
        from app.importers.anydoc_bridge import (
            AnydocFallback,
            AnydocUnavailable,
            markdown_via_anydoc,
            should_handle,
            supports,
        )

        if not should_handle(src):
            return None
        if not supports(src):
            return None
        return markdown_via_anydoc(source_bytes, src, filename).encode("utf-8")
    except (AnydocUnavailable, AnydocFallback):
        return None
    except MdBridgeError:
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
    fetch_images: bool | None = None,
    client: str = "",
    skip_magic: bool = False,
) -> bytes:
    """Convert document bytes from source_format to target_format."""
    src, tgt = _validate_formats(source_format, target_format)
    _check_size(source_bytes, filename)

    if src == tgt:
        # Still validate identity passthrough unless skipped
        if settings.magic_bytes_check and not skip_magic:
            validate_upload(source_bytes, src, filename)
        scan_bytes(source_bytes, filename or f"input.{src}")
        return source_bytes

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
            str(fetch_images),
            f"hn={settings.heading_numbering}",
            f"dc={settings.docx_export_comments}",
            f"ic={settings.image_compress}:{settings.image_max_edge}:{settings.image_jpeg_quality}",
        ]
    )
    cache_key = result_cache.make_key(source_bytes, src, tgt, extra)
    cached = result_cache.get(cache_key)
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
        return cached

    if settings.magic_bytes_check and not skip_magic:
        validate_upload(source_bytes, src, filename)
    scan_bytes(source_bytes, filename or f"input.{src}")

    started = time.perf_counter()
    importer = ""
    try:
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
            return out

        from app.core.stream_convert import can_stream_convert, convert_streaming

        # 边解析边转：大文本格式分块导入/导出，降低峰值 AST 内存
        if can_stream_convert(src, tgt) and len(source_bytes) >= settings.stream_min_bytes:
            out = convert_streaming(source_bytes, src, tgt, filename=filename)
            importer = "stream"
        else:
            model = _import(source_bytes, src, filename)
            importer = str(model.metadata.get("importer") or "native")
            # anydoc hit/degrade telemetry: when anydoc was the designated engine,
            # record whether it actually performed the conversion.
            try:
                from app.importers.anydoc_bridge import should_handle as _anydoc_designated

                if _anydoc_designated(src):
                    if importer == "anydoc":
                        metrics.record_anydoc_hit(channel="model")
                    else:
                        metrics.record_anydoc_degrade()
            except Exception:
                pass
            do_fetch = settings.fetch_remote_images if fetch_images is None else fetch_images
            if do_fetch:
                from app.core.remote_images import resolve_remote_images

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
            )
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
        return out
    except Exception as exc:
        fail_code = exc.code if isinstance(exc, MdBridgeError) else "conversion_error"
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
    workers: int | None = None,
    cancel_event: Any | None = None,
    progress_cb: Any | None = None,
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
        target_file = out_path / rel.with_suffix(f".{tgt}")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        item = BatchItemResult(source=str(file_path), target=str(target_file))
        try:
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
            )
            target_file.write_bytes(converted)
            item.success = True
        except MdBridgeError as exc:
            item.error = exc.message
            item.code = exc.code
        except Exception as exc:
            item.error = str(exc)
            item.code = "conversion_error"
        return item

    done = 0
    with ThreadPoolExecutor(max_workers=n_workers) as pool:
        futs = {pool.submit(_one, fp): fp for fp in files}
        for fut in as_completed(futs):
            if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
                break
            item = fut.result()
            result.items.append(item)
            if item.success:
                result.succeeded += 1
            else:
                result.failed += 1
            done += 1
            if progress_cb:
                try:
                    progress_cb(done, result.total, item)
                except Exception:
                    pass
    return result


def convert_directory_async(
    input_dir: Path | str,
    source_format: str,
    target_format: str,
    *,
    recursive: bool = False,
    client: str = "",
) -> list[str]:
    """Enqueue each file as a job_store job; returns job ids."""
    from app.core.jobs import job_store

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
            result_filename=file_path.with_suffix(f".{tgt}").name,
        )
        job_ids.append(job.id)
    return job_ids
