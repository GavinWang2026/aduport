"""XML parse helpers — require defusedxml (v6/T3-#19 / D-23)."""

from __future__ import annotations

try:
    from defusedxml.ElementTree import fromstring as _fromstring  # type: ignore
except ImportError as exc:  # pragma: no cover
    raise RuntimeError(
        "defusedxml is required for XXE-safe XML parsing. "
        "Install with: pip install 'defusedxml>=0.7.1' "
        "(declared in AduPort pyproject.toml dependencies)."
    ) from exc


def fromstring(text: str | bytes):
    try:
        return _fromstring(text)
    except Exception as exc:
        if _is_unsafe_xml(exc):
            from aduport.core.exceptions import UnsafeDocumentError

            host = _external_host(exc)
            detail = f"（{host}）" if host else ""
            raise UnsafeDocumentError(f"文档包含不安全的外部引用{detail}") from None
        raise


def _is_unsafe_xml(exc: BaseException) -> bool:
    name = type(exc).__name__.casefold()
    return any(token in name for token in ("entit", "dtdforbidden", "externalreference"))


def _external_host(exc: BaseException) -> str:
    from urllib.parse import urlparse

    system = str(getattr(exc, "system_id", "") or "")
    if not system:
        return ""
    parsed = urlparse(system)
    return parsed.netloc or parsed.path

