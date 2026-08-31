"""Incremental convert pipeline: parse/export in chunks to a SpillBuffer (边解析边转)."""

from __future__ import annotations

import logging
from collections.abc import Iterator

from app.config import settings
from app.core.document_model import DocumentModel
from app.core.spill import SpillBuffer
from app.exporters.html_exporter import export_html
from app.exporters.md_exporter import export_md
from app.exporters.txt_exporter import export_txt
from app.importers.html_importer import import_html
from app.importers.md_importer import import_md
from app.importers.txt_importer import import_txt

_log = logging.getLogger("mdbridge.stream_convert")

# Source → target pairs that support chunked AST pipeline
_STREAM_PAIRS = frozenset(
    {
        ("md", "md"),
        ("md", "txt"),
        ("md", "html"),
        ("txt", "md"),
        ("txt", "txt"),
        ("txt", "html"),
        ("html", "md"),
        ("html", "txt"),
        ("html", "html"),
    }
)


def can_stream_convert(source_format: str, target_format: str) -> bool:
    if not settings.stream_convert:
        return False
    return (source_format, target_format) in _STREAM_PAIRS


def convert_streaming(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    *,
    filename: str = "",
    chunk_chars: int | None = None,
) -> bytes:
    """
    Convert large text documents by splitting into blocks, parsing each block,
    exporting immediately, and spilling to disk when output grows past threshold.

    Reduces peak AST memory vs full-document import+export for huge MD/TXT/HTML.
    """
    size = chunk_chars or max(8_000, settings.stream_chunk_chars)
    chunks = list(_iter_text_chunks(source_bytes, source_format, size))
    if len(chunks) <= 1:
        # Small: fall back to normal single-shot via callers
        model = _import_chunk(source_bytes, source_format, filename)
        return _export_model(model, target_format)

    writer = _SpillWriter()
    first = True
    for idx, chunk in enumerate(chunks):
        try:
            model = _import_chunk(chunk, source_format, f"{filename}#{idx}")
        except Exception as exc:
            _log.warning("stream chunk %s import failed: %s", idx, exc)
            continue
        piece = _export_model(model, target_format)
        if not first and target_format == "html":
            # Strip duplicate html wrapper — keep body fragment only
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


def _iter_text_chunks(data: bytes, fmt: str, size: int) -> Iterator[bytes]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        yield data
        return
    if len(text) <= size:
        yield data
        return
    # Prefer splitting on blank lines near the size boundary
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            # look for paragraph break
            br = text.rfind("\n\n", start + size // 2, end)
            if br != -1:
                end = br + 2
        chunk = text[start:end]
        yield chunk.encode("utf-8")
        start = end


def _import_chunk(data: bytes, fmt: str, filename: str) -> DocumentModel:
    if fmt == "md":
        return import_md(data)
    if fmt == "txt":
        return import_txt(data, filename or "chunk.txt")
    if fmt == "html":
        return import_html(data, filename or "chunk.html")
    raise ValueError(fmt)


def _export_model(model: DocumentModel, fmt: str) -> bytes:
    if fmt == "md":
        return export_md(model).encode("utf-8")
    if fmt == "txt":
        return export_txt(model).encode("utf-8")
    if fmt == "html":
        return export_html(model).encode("utf-8")
    raise ValueError(fmt)


def _html_head(model: DocumentModel) -> str:
    title = model.title or "document"
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
            # Append by rewrite (simple); for large streams re-open path
            existing = self._spill.read()
            self._spill.release()
            self._spill = SpillBuffer(existing + data, prefix="mdbridge-stream-")
            return
        self._buf.extend(data)
        threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
        if threshold > 0 and len(self._buf) >= threshold:
            self._spill = SpillBuffer(bytes(self._buf), prefix="mdbridge-stream-")
            self._buf.clear()

    def getvalue(self) -> bytes:
        if self._spill is not None:
            data = self._spill.read()
            self._spill.release()
            self._spill = None
            return data
        return bytes(self._buf)
