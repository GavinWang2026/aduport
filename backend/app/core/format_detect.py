"""Detect source document format from filename and optional magic bytes."""

from __future__ import annotations

from pathlib import Path

from app.core.converter import SUPPORTED_SOURCE_FORMATS

# Extension → canonical source format (includes aliases)
_EXT_MAP: dict[str, str] = {
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
    "html": "html",
    "htm": "html",
    "docx": "docx",
    "pdf": "pdf",
    "pptx": "pptx",
    "ppt": "pptx",
    "epub": "epub",
    "mobi": "mobi",
    "azw": "mobi",
    "azw3": "mobi",
}


def detect_source_format(filename: str | None, content: bytes | None = None) -> str | None:
    """
    Resolve source format from filename extension, with light magic-byte fallback.

    Returns a value in SUPPORTED_SOURCE_FORMATS, or None if unknown.
    """
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in _EXT_MAP:
        fmt = _EXT_MAP[ext]
        if fmt in SUPPORTED_SOURCE_FORMATS:
            return fmt

    if content:
        sniffed = _sniff_bytes(content)
        if sniffed and sniffed in SUPPORTED_SOURCE_FORMATS:
            return sniffed
    return None


def _sniff_bytes(data: bytes) -> str | None:
    head = data[:8]
    if data[:4] == b"%PDF":
        return "pdf"
    if head[:2] == b"PK":
        # ZIP-based: docx / pptx / epub — prefer Content_Types / mimetype when possible
        try:
            import zipfile
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(data)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" in names:
                    return "docx"
                if "ppt/presentation.xml" in names:
                    return "pptx"
                if "mimetype" in names:
                    mt = zf.read("mimetype").decode("utf-8", errors="ignore")
                    if "epub" in mt:
                        return "epub"
        except Exception:
            return None
    if b"BOOKMOBI" in data[:128] or data[:4] == b"MOBI":
        return "mobi"
    stripped = data.lstrip()[:64].lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "html"
    return None
