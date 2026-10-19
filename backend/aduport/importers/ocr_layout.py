"""OCR layout sidecar helpers — preserve engine geometry for RAG / preview.

MinerU / Paddle outputs are primarily Markdown; when middle JSON / bbox lists
exist we normalize them into ``ocr_layout.blocks`` and attach AST spans.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aduport.core.document_model import DocumentModel
from aduport.core.node_span import stamp_layout_sidecar
from aduport.importers.import_guard import swallow_import_error


def collect_mineru_layout_blocks(out_dir: Path) -> list[dict[str, Any]]:
    """Best-effort parse MinerU middle/content_list JSON under an output dir."""
    blocks: list[dict[str, Any]] = []
    candidates = sorted(out_dir.rglob("*.json"))
    for path in candidates:
        name = path.name.lower()
        interesting = any(k in name for k in ("middle", "content_list", "model", "layout"))
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if not interesting and size > 2_000_000:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            swallow_import_error(exc, where="ocr_layout.py:mineru_json")
            continue
        blocks.extend(_normalize_layout_payload(data, default_page=1))
        if len(blocks) >= 400:
            break
    return blocks[:400]


def extract_paddle_layout_blocks(result: object) -> list[dict[str, Any]]:
    """Pull bbox-bearing fragments from heterogeneous PaddleOCR-VL results."""
    blocks: list[dict[str, Any]] = []
    _walk_paddle(result, blocks, page=1)
    return blocks[:400]


def apply_ocr_layout_to_model(
    model: DocumentModel,
    *,
    engine: str,
    blocks: list[dict[str, Any]],
) -> DocumentModel:
    if not blocks:
        return model
    stamp_layout_sidecar(
        model,
        key="ocr_layout",
        engine=engine,
        blocks=blocks,
        pixel_mode="ocr_engine_v1",
        extra={"reconstruction": "ocr_markdown+layout_sidecar"},
    )
    if not isinstance(model.metadata.get("pdf_layout"), dict):
        model.metadata["pdf_layout"] = {
            "page_count": max((int(b.get("page") or 1) for b in blocks), default=1),
            "reconstruction": "ocr_layout_mirror",
            "full_layout": True,
            "pixel_mode": "ocr_engine_v1",
            "blocks": blocks,
        }
    return model


def _normalize_layout_payload(data: Any, *, default_page: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            out.extend(_normalize_layout_item(item, page=default_page))
        return out
    if isinstance(data, dict):
        for key in ("pdf_info", "para_blocks", "content_list", "layout_dets", "blocks"):
            if key in data and isinstance(data[key], list):
                for page_i, page_obj in enumerate(data[key]):
                    page_no = page_i + 1
                    if isinstance(page_obj, dict) and "page_idx" in page_obj:
                        try:
                            page_no = int(page_obj["page_idx"]) + 1
                        except (TypeError, ValueError):
                            pass
                    nested = None
                    if isinstance(page_obj, dict):
                        nested = page_obj.get("para_blocks") or page_obj.get("blocks")
                    if isinstance(nested, list):
                        for item in nested:
                            out.extend(_normalize_layout_item(item, page=page_no))
                    else:
                        out.extend(_normalize_layout_item(page_obj, page=page_no))
                if out:
                    return out
        out.extend(_normalize_layout_item(data, page=default_page))
    return out


def _normalize_layout_item(item: Any, *, page: int) -> list[dict[str, Any]]:
    if not isinstance(item, dict):
        return []
    text = (
        item.get("text")
        or item.get("content")
        or item.get("md")
        or _join_lines(item.get("lines"))
        or ""
    )
    text = str(text).strip()
    bbox = item.get("bbox") or item.get("box") or item.get("poly")
    norm_bbox = _coerce_bbox(bbox)
    page_no = item.get("page") or item.get("page_number") or item.get("page_idx")
    page_out = page
    if page_no is not None:
        try:
            raw = int(page_no)
            page_out = raw + 1 if "page_idx" in item and "page" not in item else raw
        except (TypeError, ValueError):
            page_out = page
    if not text and norm_bbox is None:
        nested = item.get("blocks") or item.get("lines")
        if isinstance(nested, list):
            acc: list[dict[str, Any]] = []
            for child in nested:
                acc.extend(_normalize_layout_item(child, page=page_out))
            return acc
        return []
    block: dict[str, Any] = {"page": page_out, "text": text}
    if norm_bbox is not None:
        block["bbox"] = norm_bbox
        block["left"], block["bottom"], block["right"], block["top"] = (
            norm_bbox[0],
            norm_bbox[1],
            norm_bbox[2],
            norm_bbox[3],
        )
    return [block]


def _join_lines(lines: Any) -> str:
    if not isinstance(lines, list):
        return ""
    parts: list[str] = []
    for ln in lines:
        if isinstance(ln, str):
            parts.append(ln)
        elif isinstance(ln, dict):
            parts.append(str(ln.get("text") or ln.get("content") or ""))
    return "\n".join(p for p in parts if p)


def _coerce_bbox(raw: Any) -> list[float] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        if len(raw) >= 4 and all(isinstance(x, (int, float)) for x in raw[:4]):
            return [float(x) for x in raw[:4]]
        if len(raw) >= 4 and all(
            isinstance(p, (list, tuple)) and len(p) >= 2 for p in raw[:4]
        ):
            xs = [float(p[0]) for p in raw]
            ys = [float(p[1]) for p in raw]
            return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _walk_paddle(obj: Any, out: list[dict[str, Any]], *, page: int) -> None:
    if obj is None or len(out) >= 400:
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _walk_paddle(item, out, page=page)
        return
    if isinstance(obj, dict) or hasattr(obj, "get"):
        try:
            mapping = dict(obj) if not isinstance(obj, dict) else obj  # type: ignore[arg-type]
        except Exception:
            mapping = {}
            for attr in ("text", "bbox", "box", "page", "res", "parsing_res_list", "layout"):
                if hasattr(obj, attr):
                    try:
                        mapping[attr] = getattr(obj, attr)
                    except Exception as exc:
                        swallow_import_error(exc, where="ocr_layout.py:paddle_attr")
        out.extend(_normalize_layout_item(mapping, page=page))
        for key in ("res", "parsing_res_list", "layout", "blocks", "pages"):
            if key in mapping:
                _walk_paddle(mapping[key], out, page=page)
        return
    if not isinstance(obj, (str, bytes, int, float, bool)):
        mapping: dict[str, Any] = {}
        for attr in ("text", "bbox", "box", "page", "res", "markdown"):
            if hasattr(obj, attr):
                try:
                    mapping[attr] = getattr(obj, attr)
                except Exception as exc:
                    swallow_import_error(exc, where="ocr_layout.py:paddle_obj")
        if mapping:
            out.extend(_normalize_layout_item(mapping, page=page))
            for v in mapping.values():
                if isinstance(v, (list, tuple, dict)) or hasattr(v, "get"):
                    _walk_paddle(v, out, page=page)
