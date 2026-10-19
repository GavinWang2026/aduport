"""Request-scoped import options (force OCR / engine / sidecar) via ContextVar."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

_import_opts: ContextVar[dict[str, Any] | None] = ContextVar(
    "aduport_import_opts", default=None
)


def get_import_opts() -> dict[str, Any]:
    return dict(_import_opts.get() or {})


def set_import_opts(opts: dict[str, Any] | None) -> Token:
    return _import_opts.set(dict(opts) if opts else None)


def reset_import_opts(token: Token) -> None:
    _import_opts.reset(token)


@contextmanager
def import_opts_scope(**opts: Any) -> Iterator[dict[str, Any]]:
    """Activate non-empty import overrides for the current context."""
    cleaned = {k: v for k, v in opts.items() if v is not None and v != ""}
    if not cleaned:
        yield {}
        return
    token = set_import_opts(cleaned)
    try:
        yield cleaned
    finally:
        reset_import_opts(token)
