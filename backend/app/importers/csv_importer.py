"""CSV → DocumentModel (single or multi-table blocks)."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from app.core.document_model import DocumentModel, Table, TableCell
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
            cells = [
                [
                    TableCell(text=str(c or ""), origin_row=r, origin_col=j)
                    for j, c in enumerate(row)
                ]
                for r, row in enumerate(rows)
            ]
            tables.append(Table(rows=rows, cells=cells, header_rows=1 if len(rows) > 1 else 0))
    if not tables:
        raise InvalidDocumentError(f"CSV 无数据行: {filename}")
    return DocumentModel(title=Path(filename).stem, nodes=list(tables))
