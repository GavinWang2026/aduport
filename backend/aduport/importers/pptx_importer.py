"""PPTX importer: docling when available, else slide XML text extract."""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.pptx_text import try_extract_pptx_model

_log = logging.getLogger("aduport.pptx")


def _require_docling():
    try:
        import docling.document_converter  # noqa: F401

        from aduport.importers._docling_converter import get_office_converter
    except ImportError as exc:
        raise InvalidDocumentError(
            "PPTX 导入需要安装 docling：pip install 'aduport[office]'"
        ) from exc
    return get_office_converter


def import_pptx(source_bytes: bytes, filename: str = "input.pptx") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = zf.namelist()
            if "ppt/presentation.xml" not in names and "[Content_Types].xml" not in names:
                raise InvalidDocumentError(f"文件不是有效的 PPTX: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"PPTX 文件已损坏或不是有效的 Office 文档: {filename}") from exc
    except InvalidDocumentError:
        raise

    title = Path(filename).stem
    light = try_extract_pptx_model(source_bytes, title=title)
    try:
        get_converter = _require_docling()
    except InvalidDocumentError:
        if light is not None and light.nodes:
            return light
        raise

    try:
        return _docling_import(source_bytes, filename, get_converter)
    except Exception as exc:
        if light is not None and light.nodes:
            _log.info("docling PPTX failed, using slide XML fallback: %s", exc)
            return light
        if isinstance(exc, (InvalidDocumentError, CorruptedFileError)):
            raise
        raise CorruptedFileError(
            f"PPTX 解析失败（文件可能已损坏）: {filename} — {exc}"
        ) from exc


def _docling_import(source_bytes: bytes, filename: str, get_converter) -> DocumentModel:
    from aduport.importers._docling_utils import docling_document_to_model

    suffix = Path(filename).suffix or ".pptx"
    with tempfile.TemporaryDirectory(prefix="aduport-pptx-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        converter = get_converter()
        result = converter.convert(str(tmp_path))
        return docling_document_to_model(result.document, title=Path(filename).stem)
