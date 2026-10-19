"""Local JSONL vector store for built-in RAG embeddings (no FAISS required)."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from aduport.config import settings
from aduport.core.embed import cosine_similarity, embed_texts, resolve_embed_backend

_log = logging.getLogger("aduport.vector_store")
_lock = threading.Lock()


def _safe_tenant(tenant_id: str | None) -> str:
    tid = (tenant_id or "").strip()
    if not tid or tid in (".", ".."):
        return "_default"
    safe = "".join(c for c in tid if c.isalnum() or c in "-_")[:64]
    return safe or "_default"


def _safe_collection(collection: str | None) -> str:
    safe = "".join(
        c if c.isalnum() or c in "-_" else "_" for c in (collection or "default")
    )
    return safe or "default"


def vector_dir() -> Path | None:
    raw = (getattr(settings, "vector_dir", "") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def collection_path(
    collection: str = "default", *, tenant_id: str | None = None
) -> Path | None:
    root = vector_dir()
    if root is None:
        return None
    # Tenant subdirectory — isolates collections across tenants (v14 P1-2).
    return root / _safe_tenant(tenant_id) / f"{_safe_collection(collection)}.jsonl"


def _file_lock(path: Path):
    """Cross-process exclusive lock (fcntl on POSIX; no-op fallback elsewhere)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except ImportError:
        pass
    except OSError as exc:
        _log.warning("vector flock failed for %s: %s", path, exc)
    return fh


def _file_unlock(fh) -> None:
    try:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (ImportError, OSError):
        pass
    try:
        fh.close()
    except OSError:
        pass


def upsert_rag_payload(
    payload: dict[str, Any],
    *,
    collection: str = "default",
    source_filename: str = "",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Append / replace records from an embedded RAG payload into the local store."""
    path = collection_path(collection, tenant_id=tenant_id)
    if path is None:
        return {"persisted": False, "reason": "ADUPORT_VECTOR_DIR unset"}
    emb_meta = payload.get("embedding") or {}
    rows: list[dict[str, Any]] = []
    for el in payload.get("elements") or []:
        vec = el.get("embedding")
        if not isinstance(vec, list) or not vec:
            continue
        meta = dict(el.get("metadata") or {})
        eid = str(meta.get("element_id") or "")
        rows.append(
            {
                "element_id": eid,
                "text": el.get("text") or "",
                "type": el.get("type") or "",
                "embedding": vec,
                "metadata": meta,
                "source_filename": source_filename or meta.get("filename") or "",
                "embedding_model": emb_meta.get("model") or "",
                "embedding_dim": emb_meta.get("dim") or len(vec),
                "tenant_id": _safe_tenant(tenant_id),
            }
        )
    if not rows:
        return {"persisted": False, "reason": "no embeddings", "path": str(path)}

    with _lock:
        fh = _file_lock(path)
        try:
            existing: dict[str, dict[str, Any]] = {}
            if path.is_file():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = str(rec.get("element_id") or "")
                    if key:
                        existing[key] = rec
            for row in rows:
                key = row["element_id"] or f"anon-{len(existing)}"
                existing[key] = row
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as out:
                for rec in existing.values():
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        finally:
            _file_unlock(fh)
    return {
        "persisted": True,
        "path": str(path),
        "count": len(existing),
        "upserted": len(rows),
        "collection": collection,
        "tenant_id": _safe_tenant(tenant_id),
    }


def load_collection(
    collection: str = "default", *, tenant_id: str | None = None
) -> list[dict[str, Any]]:
    path = collection_path(collection, tenant_id=tenant_id)
    if path is None or not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with _lock:
        fh = _file_lock(path)
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        finally:
            _file_unlock(fh)
    return out


def search(
    query: str,
    *,
    collection: str = "default",
    top_k: int = 5,
    backend: str | None = None,
    model: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Brute-force cosine search over the local JSONL collection (tenant-scoped)."""
    tid = _safe_tenant(tenant_id)
    records = load_collection(collection, tenant_id=tid)
    if not records:
        return {
            "hits": [],
            "collection": collection,
            "tenant_id": tid,
            "backend": resolve_embed_backend(backend),
            "detail": "empty collection or VECTOR_DIR unset",
        }
    dim = int(records[0].get("embedding_dim") or len(records[0].get("embedding") or []) or 64)
    vectors, model_id, _ = embed_texts(
        [query],
        backend=backend or resolve_embed_backend(),
        model=model or records[0].get("embedding_model"),
        dim=dim,
    )
    if not vectors:
        return {
            "hits": [],
            "collection": collection,
            "tenant_id": tid,
            "detail": "embed backend disabled",
        }
    q = vectors[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for rec in records:
        vec = rec.get("embedding")
        if not isinstance(vec, list):
            continue
        scored.append((cosine_similarity(q, vec), rec))
    scored.sort(key=lambda x: x[0], reverse=True)
    k = max(1, min(50, int(top_k)))
    hits = []
    for score, rec in scored[:k]:
        hits.append(
            {
                "score": round(float(score), 6),
                "element_id": rec.get("element_id"),
                "text": rec.get("text"),
                "type": rec.get("type"),
                "metadata": rec.get("metadata") or {},
                "source_filename": rec.get("source_filename") or "",
                "node_index": (rec.get("metadata") or {}).get("node_index"),
            }
        )
    return {
        "hits": hits,
        "collection": collection,
        "tenant_id": tid,
        "backend": resolve_embed_backend(backend),
        "model": model_id,
        "top_k": k,
    }


def store_info() -> dict[str, Any]:
    root = vector_dir()
    return {
        "enabled": root is not None,
        "dir": str(root) if root else "",
        "backend": resolve_embed_backend(),
    }
