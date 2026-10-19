"""Incremental convert pipeline: parse/export in chunks to a SpillBuffer (边解析边转)."""

from __future__ import annotations

import html as html_lib
import logging
from collections.abc import Iterator

from aduport.config import settings
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import InvalidDocumentError
from aduport.core.spill import SpillBuffer
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.html_exporter import export_html
from aduport.exporters.md_exporter import export_md
from aduport.exporters.pdf_exporter import export_pdf
from aduport.exporters.txt_exporter import export_txt
from aduport.importers.html_importer import import_html
from aduport.importers.md_importer import import_md
from aduport.importers.txt_importer import import_txt

_log = logging.getLogger("aduport.stream_convert")

# Source → target pairs that support chunked AST pipeline
_STREAM_PAIRS = frozenset(
    {
        ("md", "md"),
        ("md", "txt"),
        ("md", "html"),
        ("md", "docx"),
        ("md", "pdf"),
        ("txt", "md"),
        ("txt", "txt"),
        ("txt", "html"),
        ("txt", "docx"),
        ("txt", "pdf"),
        ("html", "md"),
        ("html", "txt"),
        ("html", "html"),
        ("html", "docx"),
        ("html", "pdf"),
    }
)


def can_stream_convert(source_format: str, target_format: str) -> bool:
    if not settings.stream_convert:
        return False
    return (source_format, target_format) in _STREAM_PAIRS


_EXPORT_OPTION_NAMES = (
    "pdf_font_family",
    "pdf_custom_css",
    "pdf_page_size",
    "pdf_orientation",
    "pdf_header",
    "pdf_footer",
    "pdf_page_numbers",
    "pdf_toc",
    "docx_theme",
    "docx_auto_toc",
    "latex_plain_comments",
    "pdf_engine",
)


def convert_streaming(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    *,
    filename: str = "",
    chunk_chars: int | None = None,
    fetch_images: bool | None = None,
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
    pdf_engine: str | None = None,
) -> bytes:
    """
    Convert large text documents by splitting into blocks, parsing each block,
    exporting immediately (or merging into one DOCX/PDF), and spilling when large.

    Report P0-1/P0-2 fixes:
    * Chunks are only ever cut at blank-line (paragraph) boundaries; any source
      whose structure could be torn (fences, raw HTML <pre>/<table>, content
      without blank-line separators, or global heading numbering) falls back to
      a single whole-document parse instead of risking silent corruption.
    * Any chunk import failure aborts the whole conversion (all-or-nothing) —
      partial success is never returned silently.
    * All export options (pdf_*, docx_theme) and remote-image fetching now flow
      through to the export stage so large files honour the same settings as
      the non-streaming path.
    """
    from aduport.core.export_flags import bind_export_flags, reset_export_flags

    flag_token = bind_export_flags(
        docx_auto_toc=docx_auto_toc,
        latex_plain_comments=latex_plain_comments,
    )
    try:
        return _convert_streaming_bound(
            source_bytes,
            source_format,
            target_format,
            filename=filename,
            chunk_chars=chunk_chars,
            fetch_images=fetch_images,
            pdf_font_family=pdf_font_family,
            pdf_custom_css=pdf_custom_css,
            pdf_page_size=pdf_page_size,
            pdf_orientation=pdf_orientation,
            pdf_header=pdf_header,
            pdf_footer=pdf_footer,
            pdf_page_numbers=pdf_page_numbers,
            pdf_toc=pdf_toc,
            docx_theme=docx_theme,
            pdf_engine=pdf_engine,
        )
    finally:
        reset_export_flags(flag_token)


def _convert_streaming_bound(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    *,
    filename: str = "",
    chunk_chars: int | None = None,
    fetch_images: bool | None = None,
    pdf_font_family: str | None = None,
    pdf_custom_css: str | None = None,
    pdf_page_size: str | None = None,
    pdf_orientation: str | None = None,
    pdf_header: str | None = None,
    pdf_footer: str | None = None,
    pdf_page_numbers: bool | None = None,
    pdf_toc: bool | None = None,
    docx_theme: str | None = None,
    pdf_engine: str | None = None,
) -> bytes:
    do_fetch = settings.fetch_remote_images if fetch_images is None else fetch_images
    size = chunk_chars or max(8_000, settings.stream_chunk_chars)
    chunks = list(_iter_text_chunks(source_bytes, source_format, size))
    if len(chunks) <= 1:
        model = _import_chunk(source_bytes, source_format, filename)
        model = _resolve_chunk_images(model, do_fetch)
        model = _apply_global_post(model)
        return _export_model(model, target_format, docx_theme=docx_theme, pdf_opts=_pdf_kwargs(locals()))

    # Binary Office targets: merge ASTs then single export (still lowers peak parse size)
    if target_format in ("docx", "pdf"):
        return _stream_merge_binary(
            chunks,
            source_format,
            target_format,
            filename,
            fetch_images=do_fetch,
            docx_theme=docx_theme,
            pdf_opts=_pdf_kwargs(locals()),
        )

    writer = _SpillWriter()
    first = True
    for idx, chunk in enumerate(chunks):
        try:
            model = _import_chunk(chunk, source_format, f"{filename}#{idx}")
        except Exception as exc:
            _log.error("stream chunk %s import failed; aborting whole convert: %s", idx, exc)
            raise InvalidDocumentError(
                f"流式转换块 #{idx} 解析失败，已中止整个转换（防止静默内容损坏）: {exc}"
            ) from exc
        model = _resolve_chunk_images(model, do_fetch)
        model = _apply_global_post(model)
        piece = _export_model(model, target_format, docx_theme=docx_theme, pdf_opts=_pdf_kwargs(locals()))
        if not first and target_format == "html":
            piece = _html_body_fragment(piece).encode("utf-8")
            writer.write(b"\n")
        elif not first:
            writer.write(b"\n")
        if first and target_format == "html" and len(chunks) > 1:
            writer.write(_html_head(model).encode("utf-8"))
            piece = _html_body_fragment(piece).encode("utf-8")
        writer.write(piece if isinstance(piece, bytes) else piece.encode("utf-8"))
        first = False
    if target_format == "html" and len(chunks) > 1:
        writer.write(b"\n</body></html>\n")
    return writer.getvalue()


def _pdf_kwargs(namespace: dict) -> dict:
    """Pick the pdf_* option values actually present (from a locals() dict)."""
    return {k: namespace[k] for k in _EXPORT_OPTION_NAMES if k.startswith("pdf_") and namespace.get(k) is not None}


def _resolve_chunk_images(model: DocumentModel, do_fetch: bool) -> DocumentModel:
    """Remote-image resolution parity with the main convert() flow (P0-2)."""
    if not do_fetch:
        return model
    try:
        from aduport.core.remote_images import resolve_remote_images

        return resolve_remote_images(model)
    except Exception as exc:  # noqa: BLE001 — remote fetch must never kill streaming
        _log.warning("stream remote-image resolution failed: %s", exc)
        return model


def _apply_global_post(model: DocumentModel) -> DocumentModel:
    """heading_numbering / image_compress parity with converter._export (P0-2)."""
    if settings.heading_numbering:
        from aduport.core.heading_numbers import apply_heading_numbers

        model = apply_heading_numbers(model)
    if settings.image_compress:
        from aduport.core.image_ops import maybe_compress_images

        model = maybe_compress_images(model)
    return model


def _stream_merge_binary(
    chunks: list[bytes],
    source_format: str,
    target_format: str,
    filename: str,
    *,
    fetch_images: bool,
    docx_theme: str | None,
    pdf_opts: dict,
) -> bytes:
    """Parse chunks sequentially, concatenate nodes, one DOCX/PDF export."""
    merged = DocumentModel(title="", nodes=[])
    for idx, chunk in enumerate(chunks):
        try:
            model = _import_chunk(chunk, source_format, f"{filename}#{idx}")
        except Exception as exc:
            _log.error("stream binary chunk %s import failed; aborting: %s", idx, exc)
            raise InvalidDocumentError(
                f"流式转换块 #{idx} 解析失败，已中止整个转换（防止静默内容损坏）: {exc}"
            ) from exc
        if not merged.title and model.title:
            merged.title = model.title
        merged.nodes.extend(model.nodes)
        if model.metadata:
            merged.metadata.update(model.metadata)
    merged = _resolve_chunk_images(merged, fetch_images)
    merged = _apply_global_post(merged)
    return _export_model(merged, target_format, docx_theme=docx_theme, pdf_opts=pdf_opts)


def _iter_text_chunks(data: bytes, fmt: str, size: int) -> Iterator[bytes]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        yield data
        return
    if len(text) <= size:
        yield data
        return
    # Global heading numbering is order-dependent across the whole document —
    # per-chunk numbering would restart at 1 on every chunk (P0-2). Fall back to
    # a single parse when it is enabled.
    if settings.heading_numbering:
        yield data
        return
    # Hard stop: fenced code spanning chunks silently destroys fences — fall back
    # to a single parse when any fence marker is present (report CORE-01).
    if fmt in ("md", "txt") and ("```" in text or "~~~" in text):
        yield data
        return
    # Raw HTML <pre>/<table> regions must never be torn across chunks either
    # (report P0-1): a missing </pre> collapses the whole block to paragraphs.
    if "<pre" in text.lower() or "<table" in text.lower():
        yield data
        return
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            br = _paragraph_break(text, start, end)
            if br is None:
                # No blank-line boundary in this window (e.g. one giant table /
                # single-paragraph region): parse the whole document instead of
                # risking a mid-block tear. Correctness beats streaming here.
                yield data
                return
            end = br
        chunk = text[start:end]
        yield chunk.encode("utf-8")
        start = end


def _paragraph_break(text: str, start: int, end: int) -> int | None:
    """Return the index just past the last ``\\n\\n`` in the window, or None.

    Single newlines are deliberately never used as cut points: they are the
    interior line separator of GFM tables, HTML blocks and tight lists, so
    cutting there silently tears those structures (report P0-1).
    """
    window_lo = start + (end - start) // 2
    if window_lo >= end:
        return None
    br = text.rfind("\n\n", window_lo, end)
    if br == -1:
        return None
    return br + 2


def _import_chunk(data: bytes, fmt: str, filename: str) -> DocumentModel:
    from aduport.importers.import_guard import import_failure_budget

    with import_failure_budget(limit=16):
        if fmt == "md":
            return import_md(data)
        if fmt == "txt":
            return import_txt(data, filename or "chunk.txt")
        if fmt == "html":
            return import_html(data, filename or "chunk.html")
        raise ValueError(fmt)


# Long option names (converter API) → exporter short parameter names, matching
# the mapping the registry adapter (_export_*_bytes) applies for the same kwargs.
_PDF_LONG_TO_SHORT = {
    "pdf_font_family": "font_family",
    "pdf_custom_css": "custom_css",
    "pdf_page_size": "page_size",
    "pdf_orientation": "orientation",
    "pdf_header": "header",
    "pdf_footer": "footer",
    "pdf_page_numbers": "page_numbers",
    "pdf_toc": "toc",
    "pdf_engine": "engine",
}


def _export_model(
    model: DocumentModel,
    fmt: str,
    *,
    docx_theme: str | None = None,
    pdf_opts: dict | None = None,
) -> bytes:
    if fmt == "md":
        return export_md(model).encode("utf-8")
    if fmt == "txt":
        return export_txt(model).encode("utf-8")
    if fmt == "html":
        return export_html(model).encode("utf-8")
    if fmt == "docx":
        # Export-option parity with converter._export (P0-2): a requested theme
        # must not silently fall back to the default for large files.
        return export_docx(model, theme=docx_theme) if docx_theme else export_docx(model)
    if fmt == "pdf":
        opts = {
            _PDF_LONG_TO_SHORT[k]: v
            for k, v in (pdf_opts or {}).items()
            if k in _PDF_LONG_TO_SHORT and v is not None
        }
        return export_pdf(model, **opts)
    raise ValueError(fmt)


def _html_head(model: DocumentModel) -> str:
    title = html_lib.escape(model.title or "document", quote=True)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{title}</title></head><body>\n"
    )


def _html_body_fragment(html_doc: bytes | str) -> str:
    text = html_doc.decode("utf-8") if isinstance(html_doc, bytes) else html_doc
    lower = text.lower()
    start = lower.find("<body")
    if start != -1:
        start = lower.find(">", start) + 1
        end = lower.rfind("</body>")
        if end != -1:
            return text[start:end].strip()
    return text


class _SpillWriter:
    """Accumulate bytes; spill to SpillBuffer when large."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._spill: SpillBuffer | None = None

    def write(self, data: bytes) -> None:
        if self._spill is not None:
            self._spill.append(data)
            return
        self._buf.extend(data)
        threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
        if threshold > 0 and len(self._buf) >= threshold:
            self._spill = SpillBuffer(bytes(self._buf), prefix="aduport-stream-")
            self._buf.clear()

    def getvalue(self) -> bytes:
        if self._spill is not None:
            data = self._spill.read()
            self._spill.release()
            self._spill = None
            return data
        return bytes(self._buf)
