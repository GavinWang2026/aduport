"""Optional AST node provenance / layout spans for RAG & OCR connectors.

Spans live on ``DocumentModel.metadata["node_spans"]`` keyed by node index
(string) so existing importers stay unchanged. Layout sidecars use
``metadata["pdf_layout"]`` / ``metadata["ocr_layout"]``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from aduport.core.document_model import DocumentModel

_WS = re.compile(r"\s+")


def normalize_span_text(text: str) -> str:
    return _WS.sub("", (text or "").strip().lower())


def get_node_spans(model: DocumentModel) -> dict[str, dict[str, Any]]:
    raw = model.metadata.get("node_spans")
    return dict(raw) if isinstance(raw, dict) else {}


def set_node_span(model: DocumentModel, node_index: int, span: dict[str, Any]) -> None:
    spans = get_node_spans(model)
    spans[str(int(node_index))] = dict(span)
    model.metadata["node_spans"] = spans


def get_node_span(model: DocumentModel, node_index: int) -> dict[str, Any] | None:
    spans = get_node_spans(model)
    hit = spans.get(str(int(node_index)))
    return dict(hit) if isinstance(hit, dict) else None


def make_element_id(
    *,
    filename: str,
    node_index: int,
    chunk_index: int,
    text: str,
) -> str:
    digest = hashlib.sha1(
        f"{filename}|{node_index}|{chunk_index}|{text}".encode("utf-8", errors="replace")
    ).hexdigest()[:12]
    return f"rag_{node_index}_{chunk_index}_{digest}"


def attach_spans_from_layout_blocks(
    model: DocumentModel,
    blocks: list[dict[str, Any]],
    *,
    source: str = "layout",
) -> int:
    """Best-effort: map layout blocks onto AST nodes by normalized text containment.

    Returns number of nodes that received a span.
    """
    if not blocks or not model.nodes:
        return 0
    prepared: list[tuple[str, dict[str, Any]]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = normalize_span_text(str(b.get("text") or ""))
        if len(t) < 4:
            continue
        prepared.append((t, b))
    if not prepared:
        return 0

    attached = 0
    used: set[int] = set()
    for idx, node in enumerate(model.nodes):
        plain = getattr(node, "plain_text", None)
        text = plain() if callable(plain) else ""
        needle = normalize_span_text(text)
        if len(needle) < 4:
            continue
        best_i = -1
        best_score = 0
        for i, (bt, _) in enumerate(prepared):
            if i in used:
                continue
            if needle in bt or bt in needle:
                score = min(len(needle), len(bt))
                if score > best_score:
                    best_score = score
                    best_i = i
        if best_i < 0:
            continue
        used.add(best_i)
        block = prepared[best_i][1]
        bbox = _bbox_from_block(block)
        span: dict[str, Any] = {
            "source": source,
            "page": int(block.get("page") or block.get("page_number") or 0) or None,
        }
        if bbox is not None:
            span["bbox"] = bbox
        set_node_span(model, idx, span)
        attached += 1
    return attached


def _bbox_from_block(block: dict[str, Any]) -> list[float] | None:
    if isinstance(block.get("bbox"), (list, tuple)) and len(block["bbox"]) >= 4:
        try:
            return [float(x) for x in block["bbox"][:4]]
        except (TypeError, ValueError):
            return None
    keys = ("left", "bottom", "right", "top")
    if all(k in block for k in keys):
        try:
            return [float(block[k]) for k in keys]
        except (TypeError, ValueError):
            return None
    return None


def stamp_layout_sidecar(
    model: DocumentModel,
    *,
    key: str,
    engine: str,
    blocks: list[dict[str, Any]],
    pixel_mode: str,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "engine": engine,
        "pixel_mode": pixel_mode,
        "blocks": list(blocks),
        "block_count": len(blocks),
    }
    if extra:
        payload.update(extra)
    model.metadata[key] = payload
    attach_spans_from_layout_blocks(model, blocks, source=f"{key}:{engine}")
