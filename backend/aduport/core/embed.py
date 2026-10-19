"""Built-in embedding helpers for RAG export (optional, default-off).

Backends:
  - ``hash`` (always available): deterministic hashing embedder for local index /
    tests; not a semantic model.
  - ``fastembed``: if ``fastembed`` is installed and selected.
  - ``none``: skip vectors.

Schema bump: when embeddings are attached, ``schema_version`` becomes ``1.2``.
"""

from __future__ import annotations

import hashlib
import logging
import math
import struct
from typing import Any

from aduport.config import settings

_log = logging.getLogger("aduport.embed")

HASH_DIM_DEFAULT = 64


def resolve_embed_backend(requested: str | None = None) -> str:
    raw = (requested or getattr(settings, "embed_backend", "hash") or "hash").strip().lower()
    if raw in ("0", "false", "off", "none", "disabled"):
        return "none"
    if raw in ("hash", "builtin", "local"):
        return "hash"
    if raw in ("fastembed", "fast"):
        return "fastembed"
    return raw


def embed_ready(backend: str | None = None) -> dict[str, Any]:
    b = resolve_embed_backend(backend)
    info: dict[str, Any] = {
        "backend": b,
        "ready": False,
        "model": getattr(settings, "embed_model", "") or "",
        "dim": int(getattr(settings, "embed_dim", HASH_DIM_DEFAULT) or HASH_DIM_DEFAULT),
    }
    if b == "none":
        info["ready"] = False
        return info
    if b == "hash":
        info["ready"] = True
        info["model"] = info["model"] or f"hashing-v1/{info['dim']}"
        return info
    if b == "fastembed":
        try:
            import fastembed  # noqa: F401

            info["ready"] = True
            info["model"] = info["model"] or "BAAI/bge-small-en-v1.5"
        except ImportError:
            info["ready"] = False
            info["detail"] = "pip install fastembed"
        return info
    info["detail"] = f"unknown backend: {b}"
    return info


def hash_embed(text: str, *, dim: int = HASH_DIM_DEFAULT) -> list[float]:
    """Deterministic bag-of-hashes embedding (L2-normalized)."""
    dim = max(8, min(1024, int(dim)))
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        tokens = ["_empty_"]
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        # Two 32-bit lanes → index + signed contribution
        for i in range(0, min(len(digest), 32), 4):
            lane = struct.unpack_from(">I", digest, i)[0]
            idx = lane % dim
            sign = 1.0 if (lane & 1) == 0 else -1.0
            vec[idx] += sign
    return _l2_normalize(vec)


def _tokenize(text: str) -> list[str]:
    raw = (text or "").lower().strip()
    if not raw:
        return []
    out: list[str] = []
    buf: list[str] = []
    for ch in raw:
        if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"):
            buf.append(ch)
        else:
            if buf:
                out.append("".join(buf))
                buf = []
    if buf:
        out.append("".join(buf))
    # CJK unigrams for short strings
    if len(out) <= 2:
        for tok in list(out):
            if any("\u4e00" <= c <= "\u9fff" for c in tok) and len(tok) > 1:
                out.extend(list(tok))
    return out[:512]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed_texts(
    texts: list[str],
    *,
    backend: str | None = None,
    model: str | None = None,
    dim: int | None = None,
) -> tuple[list[list[float]], str, int]:
    """Return (vectors, model_id, dim). Empty input → empty vectors."""
    b = resolve_embed_backend(backend)
    if b == "none":
        return [], "none", 0
    d = int(dim if dim is not None else getattr(settings, "embed_dim", HASH_DIM_DEFAULT) or HASH_DIM_DEFAULT)
    model_id = (model or getattr(settings, "embed_model", "") or "").strip()

    if b == "hash":
        model_id = model_id or f"hashing-v1/{d}"
        return [hash_embed(t, dim=d) for t in texts], model_id, d

    if b == "fastembed":
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RuntimeError("fastembed not installed; pip install fastembed") from exc
        model_id = model_id or "BAAI/bge-small-en-v1.5"
        eng = TextEmbedding(model_name=model_id)
        # Bound memory: chunk large element lists (v14 P3-6).
        batch_cap = max(1, int(getattr(settings, "embed_batch_max", 512) or 512))
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_cap):
            chunk = texts[i : i + batch_cap]
            vectors.extend(list(map(float, v)) for v in eng.embed(chunk))
        dim_out = len(vectors[0]) if vectors else d
        return vectors, model_id, dim_out

    raise RuntimeError(f"unsupported embed backend: {b}")


def attach_embeddings(
    payload: dict[str, Any],
    *,
    backend: str | None = None,
    model: str | None = None,
    dim: int | None = None,
) -> dict[str, Any]:
    """Mutate RAG payload in place: add embedding fields + bump schema_version."""
    elements = list(payload.get("elements") or [])
    texts = [str(e.get("text") or "") for e in elements]
    if not texts:
        payload["embedding"] = {
            "backend": resolve_embed_backend(backend),
            "model": model or "",
            "dim": 0,
            "count": 0,
        }
        payload["schema_version"] = "1.2"
        return payload

    vectors, model_id, dim_out = embed_texts(
        texts, backend=backend, model=model, dim=dim
    )
    for el, vec in zip(elements, vectors):
        el["embedding"] = vec
    payload["elements"] = elements
    payload["embedding"] = {
        "backend": resolve_embed_backend(backend),
        "model": model_id,
        "dim": dim_out,
        "count": len(vectors),
    }
    payload["schema_version"] = "1.2"
    return payload


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))
