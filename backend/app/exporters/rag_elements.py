"""RAG / chunk-element contract (v8 §D) — AST → unstructured-like elements.

Does not embed or call a vector DB. Emits a stable JSON-serializable list that
downstream RAG ETL (unstructured / LangChain / custom) can ingest.
"""

from __future__ import annotations

from typing import Any

from app.core.document_model import (
    Blockquote,
    Chart,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    Image,
    ListItem,
    Paragraph,
    Table,
)


def export_rag_elements(
    model: DocumentModel,
    *,
    max_chars: int = 1200,
) -> list[dict[str, Any]]:
    """Convert DocumentModel nodes into element dicts.

    Schema (stable):
      type: Title | NarrativeText | ListItem | Table | Image | CodeSnippet | …
      text: str
      metadata: {page_number?, category?, source_node, …}
    """
    elements: list[dict[str, Any]] = []
    for idx, node in enumerate(model.nodes):
        base_meta = {
            "source_node": type(node).__name__,
            "node_index": idx,
            "filename": model.title or "",
        }
        if isinstance(node, Heading):
            elements.append(
                {
                    "type": "Title" if node.level <= 1 else "SectionHeader",
                    "text": node.plain_text(),
                    "metadata": {**base_meta, "heading_level": node.level},
                }
            )
        elif isinstance(node, Paragraph):
            text = node.plain_text()
            for chunk in _chunk_text(text, max_chars):
                elements.append(
                    {
                        "type": "NarrativeText",
                        "text": chunk,
                        "metadata": dict(base_meta),
                    }
                )
        elif isinstance(node, ListItem):
            elements.append(
                {
                    "type": "ListItem",
                    "text": node.plain_text(),
                    "metadata": {
                        **base_meta,
                        "ordered": bool(node.ordered),
                        "level": int(node.level or 0),
                    },
                }
            )
        elif isinstance(node, Table):
            # Flatten rows as TSV for embedding; keep structured rows in metadata.
            lines = ["\t".join(str(c) for c in row) for row in (node.rows or [])]
            elements.append(
                {
                    "type": "Table",
                    "text": "\n".join(lines),
                    "metadata": {
                        **base_meta,
                        "rows": node.rows,
                        "name": node.name or "",
                        "header_rows": int(node.header_rows or 0),
                    },
                }
            )
        elif isinstance(node, CodeBlock):
            elements.append(
                {
                    "type": "CodeSnippet",
                    "text": node.content or "",
                    "metadata": {**base_meta, "language": node.lang or ""},
                }
            )
        elif isinstance(node, Blockquote):
            joined = "\n".join(
                p if isinstance(p, str) else getattr(p, "text", str(p))
                for p in (node.paragraphs or [])
            )
            elements.append(
                {
                    "type": "NarrativeText",
                    "text": joined,
                    "metadata": {**base_meta, "category": "blockquote"},
                }
            )
        elif isinstance(node, Image):
            elements.append(
                {
                    "type": "Image",
                    "text": node.alt_text or "[image]",
                    "metadata": {
                        **base_meta,
                        "mime_type": node.mime_type or "",
                        "has_bytes": bool(node.data),
                    },
                }
            )
        elif isinstance(node, Chart):
            elements.append(
                {
                    "type": "FigureCaption",
                    "text": node.plain_text(),
                    "metadata": {
                        **base_meta,
                        "chart_type": node.chart_type,
                        "x_axis_title": node.x_axis_title,
                        "y_axis_title": node.y_axis_title,
                    },
                }
            )
        elif isinstance(node, Footnote):
            elements.append(
                {
                    "type": "NarrativeText",
                    "text": f"[^{node.id}]: {node.text}",
                    "metadata": {**base_meta, "category": "footnote", "id": node.id},
                }
            )
        else:
            plain = getattr(node, "plain_text", None)
            text = plain() if callable(plain) else ""
            if text:
                elements.append(
                    {
                        "type": "UncategorizedText",
                        "text": text,
                        "metadata": dict(base_meta),
                    }
                )
    return elements


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            # Prefer paragraph / sentence breaks
            cut = text.rfind("\n\n", start, end)
            if cut <= start:
                cut = text.rfind(". ", start, end)
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        start = end
    return [c for c in out if c]
