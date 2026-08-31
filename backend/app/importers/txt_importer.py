"""Plain-text importer."""

from __future__ import annotations

from app.core.document_model import DocumentModel, Paragraph, RichText
from app.core.exceptions import InvalidDocumentError


def import_txt(source_bytes: bytes, filename: str = "input.txt") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("文本文件必须是有效的 UTF-8 编码") from exc

    nodes = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        line = block.strip()
        if line:
            nodes.append(Paragraph(text=RichText(text=line)))
    if not nodes and text.strip():
        nodes.append(Paragraph(text=RichText(text=text.strip())))
    return DocumentModel(title=filename.rsplit(".", 1)[0] if filename else "", nodes=nodes)
