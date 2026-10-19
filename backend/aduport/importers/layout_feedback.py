"""Layout feedback helpers — close the OCR/RAG span loop without re-training."""

from __future__ import annotations

from typing import Any

from aduport.core.document_model import DocumentModel
from aduport.core.node_span import set_node_span


def apply_layout_feedback_to_model(
    model: DocumentModel,
    feedback: list[dict[str, Any]],
) -> DocumentModel:
    """Override ``metadata.node_spans`` from client corrections.

    Each feedback item may include:
      - node_index (int, required)
      - page / page_number
      - bbox (list[float])
      - source (str, default ``layout_feedback``)
    """
    for item in feedback or []:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("node_index"))
        except (TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(model.nodes):
            continue
        page = item.get("page", item.get("page_number"))
        bbox = item.get("bbox")
        span: dict[str, Any] = {"source": str(item.get("source") or "layout_feedback")}
        if page is not None:
            try:
                span["page"] = int(page)
            except (TypeError, ValueError):
                pass
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                span["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            except (TypeError, ValueError):
                pass
        set_node_span(model, idx, span)
    if model.metadata is None:
        model.metadata = {}
    model.metadata["layout_feedback_applied"] = len(feedback or [])
    return model


def apply_layout_feedback_to_rag(
    payload: dict[str, Any],
    feedback: list[dict[str, Any]],
) -> dict[str, Any]:
    """Patch RAG element metadata by ``element_id`` or ``node_index``+``chunk_index``."""
    by_id: dict[str, dict[str, Any]] = {}
    by_node: dict[tuple[int, int], dict[str, Any]] = {}
    for item in feedback or []:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("element_id") or "").strip()
        if eid:
            by_id[eid] = item
            continue
        try:
            ni = int(item.get("node_index"))
            ci = int(item.get("chunk_index", 0))
        except (TypeError, ValueError):
            continue
        by_node[(ni, ci)] = item

    patched = 0
    for el in payload.get("elements") or []:
        meta = el.setdefault("metadata", {})
        item = None
        eid = str(meta.get("element_id") or "")
        if eid and eid in by_id:
            item = by_id[eid]
        else:
            try:
                key = (int(meta.get("node_index")), int(meta.get("chunk_index", 0)))
            except (TypeError, ValueError):
                key = None
            if key is not None and key in by_node:
                item = by_node[key]
        if not item:
            continue
        if item.get("page", item.get("page_number")) is not None:
            try:
                meta["page_number"] = int(item.get("page", item.get("page_number")))
            except (TypeError, ValueError):
                pass
        bbox = item.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            try:
                meta["bbox"] = [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])]
            except (TypeError, ValueError):
                pass
        meta["span_source"] = str(item.get("source") or "layout_feedback")
        patched += 1

    payload["layout_feedback_patched"] = patched
    return payload
