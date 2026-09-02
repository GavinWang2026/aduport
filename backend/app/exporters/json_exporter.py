"""JSON document export & import — full AST JSON schema."""

from __future__ import annotations

import json
from pathlib import Path

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    RichText,
    Table,
    TableCell,
    TextBox,
    ensure_rich_text,
)
from app.core.exceptions import InvalidDocumentError

SCHEMA_VERSION = "1.0"


def _rich_to_dict(rich: RichText | str) -> dict:
    rt = ensure_rich_text(rich)
    return {
        "text": rt.text,
        "marks": [
            {
                "type": m.type.value if hasattr(m.type, "value") else str(m.type),
                "start": m.start,
                "end": m.end,
                **({"href": m.href} if m.href else {}),
            }
            for m in rt.marks
        ],
    }


def _table_cell_to_dict(cell: TableCell) -> dict:
    return {
        "text": cell.text,
        "row_span": cell.row_span,
        "col_span": cell.col_span,
        "covered": cell.covered,
        "origin_row": cell.origin_row,
        "origin_col": cell.origin_col,
        "formula": cell.formula,
        "cell_kind": cell.cell_kind,
    }


def _node_to_dict(node) -> dict:
    if isinstance(node, Heading):
        return {
            "type": "heading",
            "level": node.level,
            "text": _rich_to_dict(node.text),
            **({"anchor": node.anchor} if node.anchor else {}),
        }
    if isinstance(node, Paragraph):
        return {"type": "paragraph", "text": _rich_to_dict(node.text)}
    if isinstance(node, ListItem):
        return {
            "type": "list_item",
            "text": _rich_to_dict(node.text),
            "ordered": node.ordered,
            "level": node.level,
            **({"checked": node.checked} if node.checked is not None else {}),
        }
    if isinstance(node, Table):
        block: dict = {"type": "table", "rows": node.rows}
        if node.name:
            block["name"] = node.name
        if node.header_rows:
            block["header_rows"] = node.header_rows
        if node.cells:
            block["cells"] = [[_table_cell_to_dict(c) for c in row] for row in node.cells]
        return block
    if isinstance(node, Image):
        return {
            "type": "image",
            "alt_text": node.alt_text,
            "mime_type": node.mime_type,
            "has_data": bool(node.data),
            **({"url": node.url} if node.url else {}),
        }
    if isinstance(node, CodeBlock):
        return {"type": "code_block", "lang": node.lang, "content": node.content}
    if isinstance(node, Blockquote):
        return {
            "type": "blockquote",
            "paragraphs": [_rich_to_dict(p) for p in node.paragraphs],
        }
    if isinstance(node, HorizontalRule):
        return {"type": "horizontal_rule"}
    if isinstance(node, MathBlock):
        return {"type": "math_block", "content": node.content, "display": node.display}
    if isinstance(node, Footnote):
        return {"type": "footnote", "id": node.id, "text": node.text}
    if isinstance(node, Comment):
        return {
            "type": "comment",
            "author": node.author,
            "text": node.text,
            **({"anchor": node.anchor} if node.anchor else {}),
        }
    if isinstance(node, Revision):
        return {
            "type": "revision",
            "change": node.change,
            "author": node.author,
            "text": node.text,
            **({"anchor": node.anchor} if node.anchor else {}),
        }
    if isinstance(node, ContentControl):
        return {
            "type": "content_control",
            "tag": node.tag,
            "alias": node.alias,
            "control_type": node.control_type,
            "text": node.text,
        }
    if isinstance(node, TextBox):
        block = {"type": "text_box", "text": node.text}
        if node.source:
            block["source"] = node.source
        for key in ("x_mm", "y_mm", "width_mm", "height_mm"):
            val = getattr(node, key)
            if val is not None:
                block[key] = val
        return block
    if isinstance(node, PivotTable):
        return {
            "type": "pivot_table",
            "name": node.name,
            "cache_id": node.cache_id,
            "source_range": node.source_range,
            "location": node.location,
            "row_fields": node.row_fields,
            "col_fields": node.col_fields,
            "data_fields": node.data_fields,
        }
    if isinstance(node, HtmlBlock):
        return {"type": "html_block", "content": node.content}
    return {"type": getattr(node, "type", "unknown")}


def export_json(model: DocumentModel) -> str:
    blocks = [_node_to_dict(n) for n in model.nodes]
    tables = [b for b in blocks if b.get("type") == "table"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "title": model.title or "",
        "metadata": dict(model.metadata or {}),
        "blocks": blocks,
        "tables": tables,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _dict_to_rich(data) -> RichText | str:
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        from app.core.document_model import InlineMark, InlineMarkType

        text = str(data.get("text") or "")
        marks = []
        for m in data.get("marks") or []:
            if not isinstance(m, dict):
                continue
            try:
                mtype = InlineMarkType(str(m.get("type")))
            except ValueError:
                continue
            marks.append(
                InlineMark(
                    type=mtype,
                    start=int(m.get("start") or 0),
                    end=int(m.get("end") or 0),
                    href=m.get("href"),
                )
            )
        return RichText(text=text, marks=marks)
    return str(data)


def _block_to_node(b: dict):
    t = b.get("type")
    if t == "heading":
        return Heading(
            level=int(b.get("level") or 1),
            text=_dict_to_rich(b.get("text")),
            anchor=b.get("anchor"),
        )
    if t == "paragraph":
        return Paragraph(text=_dict_to_rich(b.get("text")))
    if t == "list_item":
        return ListItem(
            text=_dict_to_rich(b.get("text")),
            ordered=bool(b.get("ordered")),
            level=int(b.get("level") or 0),
            checked=b.get("checked"),
        )
    if t == "table":
        cells_raw = b.get("cells")
        cells = None
        if isinstance(cells_raw, list):
            cells = [
                [
                    TableCell(
                        text=str(c.get("text") or ""),
                        row_span=int(c.get("row_span") or 1),
                        col_span=int(c.get("col_span") or 1),
                        covered=bool(c.get("covered")),
                        origin_row=int(c.get("origin_row") or 0),
                        origin_col=int(c.get("origin_col") or 0),
                        formula=str(c.get("formula") or ""),
                        cell_kind=c.get("cell_kind") or "value",
                    )
                    for c in row
                    if isinstance(c, dict)
                ]
                for row in cells_raw
                if isinstance(row, list)
            ]
        return Table(
            rows=[[str(c) for c in r] for r in b.get("rows") or []],
            cells=cells or [],
            name=str(b.get("name") or ""),
            header_rows=int(b.get("header_rows") or 0),
        )
    if t == "code_block":
        return CodeBlock(lang=str(b.get("lang") or ""), content=str(b.get("content") or ""))
    if t == "blockquote":
        return Blockquote(paragraphs=[_dict_to_rich(p) for p in b.get("paragraphs") or []])
    if t == "horizontal_rule":
        return HorizontalRule()
    if t == "math_block":
        return MathBlock(content=str(b.get("content") or ""), display=bool(b.get("display", True)))
    if t == "footnote":
        return Footnote(id=str(b.get("id") or "1"), text=str(b.get("text") or ""))
    if t == "comment":
        return Comment(
            author=str(b.get("author") or "reviewer"),
            text=str(b.get("text") or ""),
            anchor=str(b.get("anchor") or ""),
        )
    if t == "revision":
        return Revision(
            change=b.get("change") or "insert",
            author=str(b.get("author") or ""),
            text=str(b.get("text") or ""),
            anchor=str(b.get("anchor") or ""),
        )
    if t == "content_control":
        return ContentControl(
            tag=str(b.get("tag") or ""),
            alias=str(b.get("alias") or ""),
            control_type=str(b.get("control_type") or ""),
            text=str(b.get("text") or ""),
        )
    if t == "text_box":
        return TextBox(
            text=str(b.get("text") or ""),
            source=str(b.get("source") or ""),
            x_mm=b.get("x_mm"),
            y_mm=b.get("y_mm"),
            width_mm=b.get("width_mm"),
            height_mm=b.get("height_mm"),
        )
    if t == "pivot_table":
        return PivotTable(
            name=str(b.get("name") or ""),
            cache_id=str(b.get("cache_id") or ""),
            source_range=str(b.get("source_range") or ""),
            location=str(b.get("location") or ""),
            row_fields=list(b.get("row_fields") or []),
            col_fields=list(b.get("col_fields") or []),
            data_fields=list(b.get("data_fields") or []),
        )
    if t == "html_block":
        return HtmlBlock(content=str(b.get("content") or ""))
    if t == "image":
        return Paragraph(text=f"[Image: {b.get('alt_text') or 'image'}]")
    return None


def import_json(source_bytes: bytes, filename: str = "input.json") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidDocumentError(f"无效 JSON: {filename}") from exc

    nodes: list = []
    title = Path(filename).stem
    metadata: dict = {}

    if isinstance(data, list):
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
        if isinstance(data.get("metadata"), dict):
            metadata = data["metadata"]
        if "blocks" in data and isinstance(data["blocks"], list):
            for b in data["blocks"]:
                if isinstance(b, dict):
                    node = _block_to_node(b)
                    if node is not None:
                        nodes.append(node)
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
    return DocumentModel(title=str(title), nodes=nodes, metadata=metadata)
