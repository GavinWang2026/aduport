"""DOCX importer: native python-docx first; docling optional enrichment path.

v9 P0: 内嵌图片经 ``docx_text`` 抽取（``w:drawing`` / ``a:blip`` / ``r:embed`` /
``v:imagedata`` / ``w:pict`` → ``word/media/*`` relationship → ``Image`` node）。
本模块在 docling 路径下会把 native 抽出的图片合并回去，避免静默丢图。
"""

from __future__ import annotations

import logging
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path

from aduport.core.document_model import DocumentModel, Image
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.core.title_source import resolve_title
from aduport.importers.docx_text import try_extract_docx_model

_log = logging.getLogger("aduport.docx")


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
    # Native path always runs first — owns drawing/blip/image/pict extraction.
    native = try_extract_docx_model(source_bytes, title=title)

    # Prefer docling when installed (layout-richer); always keep native fallback.
    try:
        import docling.document_converter  # noqa: F401
        from aduport.importers._docling_converter import get_office_converter
        from aduport.importers._docling_utils import docling_document_to_model
    except ImportError:
        if native is not None and native.nodes:
            return _finish_docx(native, source_bytes, filename)
        raise InvalidDocumentError(
            f"DOCX 解析失败（原生路径无正文）: {filename}"
        )

    try:
        suffix = Path(filename).suffix or ".docx"
        with tempfile.TemporaryDirectory(prefix="aduport-docx-") as tmpdir:
            tmp_path = Path(tmpdir) / f"input{suffix}"
            tmp_path.write_bytes(source_bytes)
            os.chmod(tmp_path, 0o600)
            converter = get_office_converter()
            result = converter.convert(str(tmp_path))
            model = docling_document_to_model(result.document, title=title)
            model = _merge_native_images(model, native)
            meta = dict(model.metadata or {})
            meta["importer"] = "docx_docling"
            if any(isinstance(n, Image) for n in model.nodes):
                meta["docx_images"] = "native_merged"
            model.metadata = meta
            return _finish_docx(model, source_bytes, filename)
    except Exception as exc:
        if native is not None and native.nodes:
            _log.info("docling DOCX failed, using native python-docx: %s", exc)
            return _finish_docx(native, source_bytes, filename)
        if isinstance(exc, (InvalidDocumentError, CorruptedFileError)):
            raise
        raise CorruptedFileError(
            f"DOCX 解析失败（文件可能已损坏）: {filename} — {exc}"
        ) from exc


def _finish_docx(model: DocumentModel, source_bytes: bytes, filename: str) -> DocumentModel:
    """Attach OOXML chrome once, then drop filename-stem titles."""
    from aduport.importers.docx_ooxml import enrich_docx_model

    model = enrich_docx_model(model, source_bytes)
    model.title = resolve_title(model.title, model.nodes, filename=filename)
    return model


def _merge_native_images(
    model: DocumentModel, native: DocumentModel | None
) -> DocumentModel:
    """If docling dropped embedded pictures, reinject Image nodes from native extract.

    Matches v9 P0: drawing/blip → media must survive whichever importer wins.
    """
    if native is None:
        return model
    native_imgs = [n for n in native.nodes if isinstance(n, Image) and n.data]
    if not native_imgs:
        return model
    have = {
        (len(n.data), n.mime_type, (n.alt_text or "")[:40])
        for n in model.nodes
        if isinstance(n, Image) and n.data
    }
    missing = [
        img
        for img in native_imgs
        if (len(img.data), img.mime_type, (img.alt_text or "")[:40]) not in have
    ]
    if not missing:
        return model
    # Append after body content so captions that follow images stay readable.
    model.nodes.extend(missing)
    return model
