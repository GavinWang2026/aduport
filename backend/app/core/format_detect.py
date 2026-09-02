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
    "azw3": "azw3",
    "kf8": "kf8",
    "tex": "latex",
    "latex": "latex",
}


def detect_source_format(filename: str | None, content: bytes | None = None) -> str | None:
    """
    Resolve source format from filename extension, with light magic-byte fallback.

    Returns a value in SUPPORTED_SOURCE_FORMATS, or None if unknown.
    When ``MDBRIDGE_ANYDOC_SNIFF`` is on, anydoc's content sniff runs after the
    lightweight path and can resolve OLE/RTF/ODS and mislabeled Office files.
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
        sniffed = _sniff_via_anydoc(content)
        if sniffed and sniffed in SUPPORTED_SOURCE_FORMATS:
            return sniffed
    return None


def _sniff_via_anydoc(data: bytes) -> str | None:
    try:
        from app.config import settings

        if not settings.anydoc_enabled or not settings.anydoc_sniff:
            return None
        from app.importers.anydoc_bridge import sniff_format as anydoc_sniff

        return anydoc_sniff(data)
    except Exception:
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
