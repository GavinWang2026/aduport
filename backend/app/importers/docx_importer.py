"""DOCX importer: native python-docx first; docling optional enrichment path."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers.docx_text import try_extract_docx_model

_log = logging.getLogger("mdbridge.docx")


def import_docx(source_bytes: bytes, filename: str = "input.docx") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = zf.namelist()
            if "word/document.xml" not in names and "[Content_Types].xml" not in names:
                raise InvalidDocumentError(f"文件不是有效的 DOCX: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(
            f"DOCX 文件已损坏或不是有效的 Office 文档: {filename}"
        ) from exc
    except InvalidDocumentError:
        raise

    title = Path(filename).stem
    native = try_extract_docx_model(source_bytes, title=title)

    # Prefer docling when installed (layout-richer); always keep native fallback.
    try:
        import docling.document_converter  # noqa: F401
        from app.importers._docling_converter import get_office_converter
        from app.importers._docling_utils import docling_document_to_model
        from app.importers.docx_ooxml import enrich_docx_model
    except ImportError:
        if native is not None and native.nodes:
            return native
        raise InvalidDocumentError(
            f"DOCX 解析失败（原生路径无正文）: {filename}"
        )

    try:
        suffix = Path(filename).suffix or ".docx"
        with tempfile.TemporaryDirectory(prefix="mdbridge-docx-") as tmpdir:
            tmp_path = Path(tmpdir) / f"input{suffix}"
            tmp_path.write_bytes(source_bytes)
            os.chmod(tmp_path, 0o600)
            converter = get_office_converter()
            result = converter.convert(str(tmp_path))
            model = docling_document_to_model(result.document, title=title)
            model = enrich_docx_model(model, source_bytes)
            meta = dict(model.metadata or {})
            meta["importer"] = "docx_docling"
            model.metadata = meta
            return model
    except Exception as exc:
        if native is not None and native.nodes:
            _log.info("docling DOCX failed, using native python-docx: %s", exc)
            return native
        if isinstance(exc, (InvalidDocumentError, CorruptedFileError)):
            raise
        raise CorruptedFileError(
            f"DOCX 解析失败（文件可能已损坏）: {filename} — {exc}"
        ) from exc
