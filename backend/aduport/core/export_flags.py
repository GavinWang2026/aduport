"""Per-conversion export switches that must not mutate global settings."""

from __future__ import annotations

from contextvars import ContextVar, Token

_flags: ContextVar[dict[str, object]] = ContextVar("aduport_export_flags", default={})


def export_flag(name: str, default=None):
    return _flags.get().get(name, default)


def bind_export_flags(**kwargs: object) -> Token:
    current = dict(_flags.get())
    for key, value in kwargs.items():
        if value is not None:
            current[key] = value
    return _flags.set(current)


def reset_export_flags(token: Token) -> None:
    _flags.reset(token)
