"""Legacy .doc importer — prefer docling[office]; OLE text fallback."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from aduport.core.document_model import DocumentModel, Paragraph
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError

_log = logging.getLogger("aduport.doc")


def import_doc(source_bytes: bytes, filename: str = "input.doc") -> DocumentModel:
    if len(source_bytes) < 8:
        raise InvalidDocumentError(f"DOC 文件过小: {filename}")
    # OLE Compound Document magic
    if not source_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        raise InvalidDocumentError(f"不是 OLE/DOC 复合文档: {filename}")

    from aduport.importers.ole_cfb import cfb_is_truncated

    if cfb_is_truncated(source_bytes):
        raise CorruptedFileError(f"OLE 文件被截断或不完整: {filename}")

    # Prefer docling when available
    try:
        from aduport.importers._docling_converter import get_office_converter
        from aduport.importers._docling_utils import docling_document_to_model

        with tempfile.TemporaryDirectory(prefix="aduport-doc-") as tmpdir:
            tmp_path = Path(tmpdir) / (Path(filename).name or "input.doc")
            if not tmp_path.suffix:
                tmp_path = tmp_path.with_suffix(".doc")
            tmp_path.write_bytes(source_bytes)
            os.chmod(tmp_path, 0o600)
            result = get_office_converter().convert(str(tmp_path))
            return docling_document_to_model(result.document, title=Path(filename).stem)
    except ImportError:
        _log.info("docling office converter unavailable for %s; using OLE text scrape", filename)
    except Exception as exc:
        _log.warning(
            "docling DOC import failed for %s (%s: %s); falling back to OLE text",
            filename,
            type(exc).__name__,
            exc,
        )

    text = _ole_extract_text(source_bytes)
    if not text.strip():
        raise CorruptedFileError(
            f"DOC 解析失败（需 pip install 'aduport[office]' 或文件无文本）: {filename}"
        )
    nodes = [Paragraph(text=p.strip()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not nodes:
        nodes = [Paragraph(text=text.strip())]
    model = DocumentModel(title=Path(filename).stem, nodes=nodes)
    model.metadata["importer"] = "doc_ole_text"
    model.metadata["degraded"] = True
    return model


def _ole_extract_text(data: bytes) -> str:
    """Best-effort: pull printable UTF-16LE / ASCII runs from OLE payload."""
    chunks: list[str] = []
    # UTF-16LE runs of letters/spaces
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data):
        try:
            chunks.append(m.group().decode("utf-16-le"))
        except UnicodeDecodeError:
            continue
    if not chunks:
        for m in re.finditer(rb"[\x20-\x7e]{8,}", data):
            chunks.append(m.group().decode("ascii", errors="ignore"))
    # Deduplicate consecutive similar noise
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        if len(c) < 4:
            continue
        if out and c in out[-1]:
            continue
        from aduport.importers.ole_cfb import is_cfb_directory_name

        if is_cfb_directory_name(c):
            continue
        out.append(c)
    return "\n\n".join(out[:200])
