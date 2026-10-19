"""ODT importer — light content.xml parse (no docling required)."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.odt_text import try_extract_odt_model


def import_odt(source_bytes: bytes, filename: str = "input.odt") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = zf.namelist()
            if "content.xml" not in names:
                raise InvalidDocumentError(f"文件不是有效的 ODT: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"ODT 文件已损坏: {filename}") from exc

    model = try_extract_odt_model(source_bytes, title=Path(filename).stem)
    if model is None:
        raise CorruptedFileError(f"ODT 解析失败（无正文）: {filename}")
    from aduport.importers.zip_images import append_zip_images

    append_zip_images(model, source_bytes, ("Pictures/",))
    return model
