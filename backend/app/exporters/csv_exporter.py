"""Export DocumentModel tables (and optional flat rows) to CSV."""

from __future__ import annotations

import csv
from io import StringIO

from app.core.document_model import DocumentModel, Heading, Paragraph, Table


def export_csv(model: DocumentModel) -> str:
    """
    Export all Table nodes as CSV blocks separated by blank lines (table-first).

    Non-table content is ignored when at least one table exists. If the document
    has no tables, heading/paragraph text is emitted as a single-column CSV.
    """
    buf = StringIO()
    writer = csv.writer(buf)
    tables = [n for n in model.nodes if isinstance(n, Table)]
    if tables:
        for ti, table in enumerate(tables):
            if ti:
                buf.write("\n")
            for row in table.rows:
                writer.writerow([str(c) if c is not None else "" for c in row])
        return buf.getvalue()

    # Fallback: one column of block text
    for node in model.nodes:
        if isinstance(node, Heading):
            writer.writerow([node.plain_text()])
        elif isinstance(node, Paragraph):
            writer.writerow([node.plain_text()])
    return buf.getvalue()
