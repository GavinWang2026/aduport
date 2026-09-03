"""XML parse helpers — require defusedxml (v6/T3-#19 / D-23)."""

from __future__ import annotations

try:
    from defusedxml.ElementTree import fromstring as _fromstring  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "defusedxml is required for XXE-safe XML parsing. "
        "Install with: pip install 'defusedxml>=0.7.1' "
        "(declared in md-bridge pyproject.toml dependencies)."
    ) from exc


def fromstring(text: str | bytes):
    return _fromstring(text)
