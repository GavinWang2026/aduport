"""CSV → DocumentModel (single or multi-table blocks)."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from app.core.document_model import DocumentModel, Table
from app.core.exceptions import InvalidDocumentError


def import_csv(source_bytes: bytes, filename: str = "input.csv") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError(f"CSV 解码失败: {filename}") from exc

    blocks = text.split("\n\n")
    tables: list[Table] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        reader = csv.reader(StringIO(block))
        rows = [[c for c in row] for row in reader]
        if rows:
            tables.append(Table(rows=rows))
    if not tables:
        raise InvalidDocumentError(f"CSV 无数据行: {filename}")
    return DocumentModel(title=Path(filename).stem, nodes=list(tables))
