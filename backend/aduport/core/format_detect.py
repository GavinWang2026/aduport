"""Detect source document format from filename and optional magic bytes."""

from __future__ import annotations

from pathlib import Path

from aduport.core.converter import SUPPORTED_SOURCE_FORMATS

# Delisted: real iWork '13+ is Snappy IWA. Do not sniff these zips as generic zip.
_DELISTED_SOURCE_EXTENSIONS = frozenset({"pages", "numbers", "key", "keynote"})

# Extension → canonical source format (includes aliases)
_EXT_MAP: dict[str, str] = {
    "md": "md",
    "markdown": "md",
    "txt": "txt",
    "text": "txt",
    "html": "html",
    "htm": "html",
    "docx": "docx",
    "docm": "docm",
    "doc": "doc",
    "pdf": "pdf",
    "pptx": "pptx",
    "pptm": "pptm",
    "ppsx": "ppsx",
    "ppsm": "ppsm",
    "ppt": "ppt",
    "pps": "pps",
    "pot": "pot",
    "epub": "epub",
    "mobi": "mobi",
    "azw": "mobi",
    "azw3": "azw3",
    "kf8": "kf8",
    "tex": "latex",
    "latex": "latex",
    "xlsx": "xlsx",
    "xlsm": "xlsm",
    "xls": "xls",
    "xlsb": "xlsb",
    "ods": "ods",
    "odp": "odp",
    "odt": "odt",
    "rtf": "rtf",
    "csv": "csv",
    "json": "json",
    "msg": "msg",
    "ipynb": "ipynb",
    "zip": "zip",
    "png": "png",
    "jpg": "jpg",
    "jpeg": "jpg",
    "rss": "rss",
    "atom": "atom",
}


def detect_source_format(filename: str | None, content: bytes | None = None) -> str | None:
    """
    Resolve source format from filename extension, with light magic-byte fallback.

    Returns a value in SUPPORTED_SOURCE_FORMATS, or None if unknown.
    When ``ADUPORT_ANYDOC_SNIFF`` is on, anydoc's content sniff runs after the
    lightweight path and can resolve OLE/RTF/ODS and mislabeled Office files.
    """
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext in _DELISTED_SOURCE_EXTENSIONS:
        return None
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
        from aduport.config import settings

        if not settings.anydoc_enabled or not settings.anydoc_sniff:
            return None
        from aduport.importers.anydoc_bridge import sniff_format as anydoc_sniff

        return anydoc_sniff(data)
    except Exception:
        return None


def _sniff_bytes(data: bytes) -> str | None:
    head = data[:8]
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"%PDF":
        return "pdf"
    if head[:4] == b"\xd0\xcf\x11\xe0":
        # OLE CFB — prefer anydoc sniff; lightweight guess by stream hints
        low = data[:8192].lower()
        if b"__substg1.0_" in low or b"__properties_version1.0" in low:
            return "msg"
        if b"workbook" in low or b"book\x00" in data[:2048]:
            return "xls"
        if b"powerpoint" in low or b"powerpoint document" in low:
            return "ppt"
        if b"worddocument" in low:
            return "doc"
        return None
    if head[:2] == b"PK":
        # ZIP-based: docx / pptx / xlsx / xlsb / odt / ods / odp / epub / generic
        try:
            import zipfile
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(data)) as zf:
                names = set(zf.namelist())
                if "word/document.xml" in names:
                    return "docx"
                if "ppt/presentation.xml" in names:
                    return "pptx"
                if "xl/workbook.bin" in names:
                    return "xlsb"
                if "xl/workbook.xml" in names:
                    return "xlsx"
                if "content.xml" in names and "mimetype" in names:
                    from aduport.core.safe_zip import read_zip_member

                    mt = read_zip_member(zf, "mimetype", max_bytes=256).decode(
                        "utf-8", errors="ignore"
                    )
                    if "opendocument.text" in mt:
                        return "odt"
                    if "opendocument.spreadsheet" in mt:
                        return "ods"
                    if "opendocument.presentation" in mt:
                        return "odp"
                if "mimetype" in names:
                    from aduport.core.safe_zip import read_zip_member

                    mt = read_zip_member(zf, "mimetype", max_bytes=256).decode(
                        "utf-8", errors="ignore"
                    )
                    if "epub" in mt:
                        return "epub"
                return "zip"
        except Exception:
            return None
    if b"BOOKMOBI" in data[:128] or data[:4] == b"MOBI":
        return "mobi"
    stripped = data.lstrip()[:64].lower()
    if stripped.startswith(b"<!doctype html") or stripped.startswith(b"<html"):
        return "html"
    if stripped.startswith(b"{\\rtf"):
        return "rtf"
    # Jupyter notebook JSON
    if b'"nbformat"' in data[:512] and b'"cells"' in data[:4096]:
        return "ipynb"
    # RSS / Atom
    try:
        from aduport.importers.rss_importer import looks_like_feed

        if looks_like_feed(data):
            # Prefer atom when both markers present and root is feed-like
            sample = data[:8192].lstrip().lower()
            if sample.startswith(b"<feed") or (
                b"<feed" in sample[:2048] and b"<rss" not in sample[:512]
            ):
                return "atom"
            return "rss"
    except Exception:
        pass
    return None
