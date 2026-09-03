"""Shared export fidelity helpers: warnings + visible placeholders (no silent drop)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from app.core.document_model import DocumentModel

_warnings_ctx: ContextVar[list[str] | None] = ContextVar("mdbridge_export_warnings", default=None)


def begin_export_warnings() -> Token:
    """Start collecting warnings for the current convert call."""
    return _warnings_ctx.set([])


def take_export_warnings(token: Token | None = None) -> list[str]:
    """Return collected warnings and reset the context."""
    bucket = list(_warnings_ctx.get() or [])
    if token is not None:
        _warnings_ctx.reset(token)
    else:
        _warnings_ctx.set(None)
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for w in bucket:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


_LAST_EXPORT_WARNINGS: list[str] = []


def set_last_export_warnings(warnings: list[str]) -> None:
    global _LAST_EXPORT_WARNINGS
    _LAST_EXPORT_WARNINGS = list(warnings or [])


def get_last_export_warnings() -> list[str]:
    return list(_LAST_EXPORT_WARNINGS)


def warn_export(model: DocumentModel | None, message: str) -> None:
    """Append a fidelity warning onto model.metadata and the convert context."""
    if model is not None:
        bucket = model.metadata.setdefault("export_warnings", [])
        if not isinstance(bucket, list):
            bucket = []
            model.metadata["export_warnings"] = bucket
        if message not in bucket:
            bucket.append(message)
    ctx = _warnings_ctx.get()
    if ctx is not None and message not in ctx:
        ctx.append(message)


def placeholder_line(kind: str, detail: str = "", *, degraded: bool = True) -> str:
    """Visible body placeholder so nodes are never silently discarded."""
    detail = (detail or "").strip()
    tag = "degraded" if degraded else "kept"
    if detail:
        return f"[{kind}|{tag}] {detail}"
    return f"[{kind}|{tag}]"


def node_type_name(node: Any) -> str:
    return getattr(node, "type", None) or type(node).__name__
