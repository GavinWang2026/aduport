"""Secret value helpers — SecretStr-safe access without breaking monkeypatch tests."""

from __future__ import annotations

from typing import Any


def secret_plain(value: Any) -> str:
    """Return plaintext from ``SecretStr`` or plain ``str`` (empty → "")."""
    if value is None:
        return ""
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return str(value)
