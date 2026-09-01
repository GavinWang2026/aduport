"""JSON table / document export & import (Table ↔ JSON)."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.document_model import DocumentModel, Heading, Paragraph, Table
from app.core.exceptions import InvalidDocumentError


def export_json(model: DocumentModel) -> str:
    tables = [{"rows": t.rows} for t in model.nodes if isinstance(t, Table)]
    blocks: list[dict] = []
    for node in model.nodes:
        if isinstance(node, Heading):
            blocks.append({"type": "heading", "level": node.level, "text": node.plain_text()})
        elif isinstance(node, Paragraph):
            blocks.append({"type": "paragraph", "text": node.plain_text()})
        elif isinstance(node, Table):
            blocks.append({"type": "table", "rows": node.rows})
    payload = {
        "title": model.title or "",
        "tables": tables,
        "blocks": blocks,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def import_json(source_bytes: bytes, filename: str = "input.json") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidDocumentError(f"无效 JSON: {filename}") from exc

    nodes: list = []
    title = Path(filename).stem

    if isinstance(data, list):
        # bare table rows or list of row-arrays
        if data and isinstance(data[0], list):
            nodes.append(Table(rows=[[str(c) for c in row] for row in data]))
        else:
            for item in data:
                if isinstance(item, dict) and "rows" in item:
                    nodes.append(Table(rows=[[str(c) for c in r] for r in item["rows"]]))
                elif isinstance(item, str):
                    nodes.append(Paragraph(text=item))
    elif isinstance(data, dict):
        title = data.get("title") or title
        if "blocks" in data and isinstance(data["blocks"], list):
            for b in data["blocks"]:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "heading":
                    nodes.append(
                        Heading(level=int(b.get("level") or 1), text=str(b.get("text") or ""))
                    )
                elif t == "paragraph":
                    nodes.append(Paragraph(text=str(b.get("text") or "")))
                elif t == "table":
                    nodes.append(Table(rows=[[str(c) for c in r] for r in b.get("rows") or []]))
        elif "tables" in data:
            for t in data["tables"] or []:
                if isinstance(t, dict) and "rows" in t:
                    nodes.append(Table(rows=[[str(c) for c in r] for r in t["rows"]]))
                elif isinstance(t, list):
                    nodes.append(Table(rows=[[str(c) for c in r] for r in t]))
        elif "rows" in data:
            nodes.append(Table(rows=[[str(c) for c in r] for r in data["rows"]]))
    else:
        raise InvalidDocumentError(f"不支持的 JSON 结构: {filename}")

    if not nodes:
        nodes.append(Paragraph(text="(empty)"))
    return DocumentModel(title=str(title), nodes=nodes)
