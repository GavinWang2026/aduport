"""DOCX importer using docling (optional heavy dependency)."""

from __future__ import annotations

import os
import tempfile
import zipfile
from io import BytesIO
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
            "DOCX 导入需要安装 docling：pip install 'md-bridge[office]'"
        ) from exc
    return get_office_converter


def import_docx(source_bytes: bytes, filename: str = "input.docx") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = zf.namelist()
            if "word/document.xml" not in names and "[Content_Types].xml" not in names:
                raise InvalidDocumentError(f"文件不是有效的 DOCX: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"DOCX 文件已损坏或不是有效的 Office 文档: {filename}") from exc
    except InvalidDocumentError:
        raise

    get_converter = _require_docling()
    suffix = Path(filename).suffix or ".docx"
    with tempfile.TemporaryDirectory(prefix="mdbridge-docx-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        try:
            converter = get_converter()
            result = converter.convert(str(tmp_path))
            return docling_document_to_model(result.document, title=Path(filename).stem)
        except (InvalidDocumentError, CorruptedFileError):
            raise
        except Exception as exc:
            raise CorruptedFileError(
                f"DOCX 解析失败（文件可能已损坏）: {filename} — {exc}"
            ) from exc
