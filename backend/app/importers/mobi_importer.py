"""MOBI importer using docling (optional [office] extra)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers._docling_utils import docling_document_to_model


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
    # PalmDB / MOBI: "BOOKMOBI" often at offset 60
    if b"BOOKMOBI" in data[:128]:
        return True
    if data[:4] in (b"TPZ0", b"\x00\x00\x00\x00") and b"MOBI" in data[:256]:
        return True
    return False


def import_mobi(source_bytes: bytes, filename: str = "input.mobi") -> DocumentModel:
    if not _looks_like_mobi(source_bytes):
        raise InvalidDocumentError(f"文件不是有效的 MOBI: {filename}")

    from app.importers.mobi_text import try_extract_mobi_model

    title = Path(filename).stem
    native = try_extract_mobi_model(source_bytes, title=title)
    if native is not None and native.nodes:
        native.metadata = dict(native.metadata or {})
        native.metadata["importer"] = "mobi_text"
        return native

    get_converter = _require_docling()
    suffix = Path(filename).suffix or ".mobi"
    with tempfile.TemporaryDirectory(prefix="mdbridge-mobi-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        try:
            converter = get_converter()
            result = converter.convert(str(tmp_path))
            return docling_document_to_model(result.document, title=title)
        except (InvalidDocumentError, CorruptedFileError):
            raise
        except Exception as exc:
            raise CorruptedFileError(
                f"MOBI 解析失败（文件可能已损坏或当前 docling 不支持）: {filename} — {exc}"
            ) from exc
