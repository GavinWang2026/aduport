"""MOBI importer: docling when available, else lightweight PalmDB text extract."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers.mobi_text import try_extract_mobi_model

_log = logging.getLogger("mdbridge.mobi")


def _require_docling():
    try:
        import docling.document_converter  # noqa: F401

        from app.importers._docling_converter import get_office_converter
    except ImportError as exc:
        raise InvalidDocumentError(
            "MOBI 导入需要安装 docling：pip install 'md-bridge[office]'"
        ) from exc
    return get_office_converter


def _looks_like_mobi(data: bytes) -> bool:
    if b"BOOKMOBI" in data[:128]:
        return True
    if data[:4] in (b"TPZ0", b"\x00\x00\x00\x00") and b"MOBI" in data[:256]:
        return True
    return False


def import_mobi(source_bytes: bytes, filename: str = "input.mobi") -> DocumentModel:
    if not _looks_like_mobi(source_bytes):
        raise InvalidDocumentError(f"文件不是有效的 MOBI: {filename}")

    title = Path(filename).stem
    # Prefer lightweight extract for our own / simple MOBI (uncompressed HTML)
    light = try_extract_mobi_model(source_bytes, title=title)
    if light is not None and light.nodes:
        try:
            get_converter = _require_docling()
        except InvalidDocumentError:
            return light
        # docling available: try it for richer structure; fall back to light on failure
        try:
            return _docling_import(source_bytes, filename, get_converter)
        except Exception as exc:
            _log.info("docling MOBI failed, using text fallback: %s", exc)
            return light

    get_converter = _require_docling()
    try:
        return _docling_import(source_bytes, filename, get_converter)
    except Exception as exc:
        if light is not None and light.nodes:
            return light
        if isinstance(exc, (InvalidDocumentError, CorruptedFileError)):
            raise
        raise CorruptedFileError(
            f"MOBI 解析失败（文件可能已损坏或当前引擎不支持）: {filename} — {exc}"
        ) from exc


def _docling_import(source_bytes: bytes, filename: str, get_converter) -> DocumentModel:
    from app.importers._docling_utils import docling_document_to_model

    suffix = Path(filename).suffix or ".mobi"
    with tempfile.TemporaryDirectory(prefix="mdbridge-mobi-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        converter = get_converter()
        result = converter.convert(str(tmp_path))
        return docling_document_to_model(result.document, title=Path(filename).stem)
