"""RAG / chunk-element contract — AST → unstructured-like elements (connector-ready).

Default path emits JSON for external RAG ETL. Optional built-in embedding
(``embed=True``) bumps ``schema_version`` to 1.2 and may persist to the local
vector store (``ADUPORT_VECTOR_DIR``).

v1.1 additive fields (schema id remains ``aduport.rag_elements.v1``):
  - element_id, chunk_index, parent_node_index
  - page_number / bbox / char_start / char_end when AST spans exist

v1.2 additive (when embed enabled):
  - element.embedding, top-level embedding{backend,model,dim,count}
"""

from __future__ import annotations

from typing import Any

from aduport.core.document_model import (
    Blockquote,
    Chart,
    CodeBlock,
    Comment,
    DocumentModel,
    Footnote,
    Heading,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    Revision,
    Table,
    TextBox,
)
from aduport.exporters.export_warnings import warn_export
from aduport.core.node_span import get_node_span, make_element_id


RAG_SCHEMA = "aduport.rag_elements.v1"
RAG_SCHEMA_VERSION = "1.1"
RAG_SCHEMA_VERSION_EMBED = "1.2"


def export_rag_payload(
    model: DocumentModel,
    *,
    max_chars: int = 1200,
    chunk_overlap: int = 0,
    embed: bool = False,
    embed_backend: str | None = None,
    embed_model: str | None = None,
    vector_persist: bool = False,
    vector_collection: str = "default",
) -> dict[str, Any]:
    """Full connector payload (schema + elements); optionally embed + persist."""
    payload: dict[str, Any] = {
        "schema": RAG_SCHEMA,
        "schema_version": RAG_SCHEMA_VERSION,
        "chunking": {
            "max_chars": int(max_chars),
            "overlap": max(0, int(chunk_overlap)),
        },
        "elements": export_rag_elements(
            model, max_chars=max_chars, chunk_overlap=chunk_overlap
        ),
    }
    if embed:
        from aduport.core.embed import attach_embeddings

        attach_embeddings(payload, backend=embed_backend, model=embed_model)
        if vector_persist:
            from aduport.core.vector_store import upsert_rag_payload

            tid = ""
            try:
                from aduport.core.request_context import current_tenant_id

                tid = current_tenant_id() or "default"
            except Exception:
                tid = "default"
            persist = upsert_rag_payload(
                payload,
                collection=vector_collection or "default",
                source_filename=str(
                    (model.metadata or {}).get("filename")
                    or (model.metadata or {}).get("source_filename")
                    or model.title
                    or ""
                ),
                tenant_id=tid,
            )
            payload["vector_store"] = persist
    return payload


def export_rag_elements(
    model: DocumentModel,
    *,
    max_chars: int = 1200,
    chunk_overlap: int = 0,
) -> list[dict[str, Any]]:
    """Convert DocumentModel nodes into element dicts."""
    elements: list[dict[str, Any]] = []
    model_meta = model.metadata or {}
    filename = str(
        model_meta.get("filename") or model_meta.get("source_filename") or model.title or ""
    )
    overlap = max(0, min(int(chunk_overlap), max(0, int(max_chars) - 1)))

    for idx, node in enumerate(model.nodes):
        span = get_node_span(model, idx) or {}
        base_meta: dict[str, Any] = {
            "source_node": type(node).__name__,
            "node_index": idx,
            "parent_node_index": idx,
            "filename": filename,
            "confidence": _element_confidence(node, model_meta),
        }
        if span.get("page") is not None:
            base_meta["page_number"] = span["page"]
        if span.get("bbox") is not None:
            base_meta["bbox"] = span["bbox"]
        if span.get("source"):
            base_meta["span_source"] = span["source"]

        if isinstance(node, Heading):
            text = node.plain_text()
            elements.append(
                _element(
                    "Title" if node.level <= 1 else "SectionHeader",
                    text,
                    {**base_meta, "heading_level": node.level},
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Paragraph):
            text = node.plain_text()
            chunks = _chunk_text(text, max_chars, overlap=overlap)
            offset = 0
            for ci, chunk in enumerate(chunks):
                # Approximate char offsets in original paragraph text
                start = text.find(chunk, offset) if chunk else offset
                if start < 0:
                    start = offset
                end = start + len(chunk)
                offset = max(offset, end - overlap) if overlap else end
                meta = {
                    **base_meta,
                    "chunk_index": ci,
                    "char_start": start,
                    "char_end": end,
                }
                elements.append(
                    _element(
                        "NarrativeText",
                        chunk,
                        meta,
                        filename=filename,
                        node_index=idx,
                        chunk_index=ci,
                    )
                )
        elif isinstance(node, ListItem):
            elements.append(
                _element(
                    "ListItem",
                    node.plain_text(),
                    {
                        **base_meta,
                        "ordered": bool(node.ordered),
                        "level": int(node.level or 0),
                    },
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Table):
            lines = ["\t".join(str(c) for c in row) for row in (node.rows or [])]
            elements.append(
                _element(
                    "Table",
                    "\n".join(lines),
                    {
                        **base_meta,
                        "rows": node.rows,
                        "name": node.name or "",
                        "header_rows": int(node.header_rows or 0),
                    },
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, CodeBlock):
            elements.append(
                _element(
                    "CodeSnippet",
                    node.content or "",
                    {**base_meta, "language": node.lang or ""},
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Blockquote):
            joined = "\n".join(
                p if isinstance(p, str) else getattr(p, "text", str(p))
                for p in (node.paragraphs or [])
            )
            elements.append(
                _element(
                    "NarrativeText",
                    joined,
                    {**base_meta, "category": "blockquote"},
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Image):
            elements.append(
                _element(
                    "Image",
                    node.alt_text or "[image]",
                    {
                        **base_meta,
                        "mime_type": node.mime_type or "",
                        "has_bytes": bool(node.data),
                    },
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Chart):
            elements.append(
                _element(
                    "FigureCaption",
                    node.plain_text(),
                    {
                        **base_meta,
                        "chart_type": node.chart_type,
                        "x_axis_title": node.x_axis_title,
                        "y_axis_title": node.y_axis_title,
                    },
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, Footnote):
            elements.append(
                _element(
                    "NarrativeText",
                    f"[^{node.id}]: {node.text}",
                    {**base_meta, "category": "footnote", "id": node.id},
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        elif isinstance(node, (Comment, Revision, TextBox, MathBlock)):
            # These used to vanish: they had no plain_text(), so the fallback
            # emitted nothing. Keep the payload and say so.
            kind = type(node).__name__
            warn_export(model, f"rag: {kind} kept as UncategorizedText")
            elements.append(
                _element(
                    "UncategorizedText",
                    node.plain_text(),
                    {**base_meta, "category": kind},
                    filename=filename,
                    node_index=idx,
                    chunk_index=0,
                )
            )
        else:
            plain = getattr(node, "plain_text", None)
            text = plain() if callable(plain) else ""
            if text:
                elements.append(
                    _element(
                        "UncategorizedText",
                        text,
                        dict(base_meta),
                        filename=filename,
                        node_index=idx,
                        chunk_index=0,
                    )
                )
    return elements


def _element_confidence(node: object, meta: dict[str, Any]) -> float:
    """Higher when the importer did not flag a flattened text layer."""
    low = str(meta.get("structure_fidelity") or "") == "below_text"
    kind = type(node).__name__
    if kind == "Heading":
        return 0.72 if low else 0.92
    if kind == "Table":
        return 0.60 if low else 0.90
    if kind in {"ListItem", "Blockquote", "CodeBlock"}:
        return 0.58 if low else 0.84
    if kind == "Footnote":
        return 0.55 if low else 0.80
    if kind == "Paragraph":
        return 0.55 if low else 0.80
    return 0.50 if low else 0.75


def _element(
    etype: str,
    text: str,
    metadata: dict[str, Any],
    *,
    filename: str,
    node_index: int,
    chunk_index: int,
) -> dict[str, Any]:
    meta = dict(metadata)
    meta.setdefault("chunk_index", chunk_index)
    meta["element_id"] = make_element_id(
        filename=filename,
        node_index=node_index,
        chunk_index=chunk_index,
        text=text or "",
    )
    return {"type": etype, "text": text, "metadata": meta}


def _chunk_text(text: str, max_chars: int, *, overlap: int = 0) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        if end < len(text):
            cut = text.rfind("\n\n", start, end)
            if cut <= start:
                cut = text.rfind(". ", start, end)
            if cut > start:
                end = cut + 1
        piece = text[start:end].strip()
        if piece:
            out.append(piece)
        if end >= len(text):
            break
        nxt = end - overlap if overlap else end
        start = max(nxt, start + 1)
    return out
