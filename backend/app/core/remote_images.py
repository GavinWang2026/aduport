"""Resolve remote Image placeholders into embedded bytes (SSRF-safe)."""

from __future__ import annotations

import logging

from app.config import settings
from app.core.document_model import DocumentModel, Image
from app.core.safe_fetch import fetch_bytes

_log = logging.getLogger("mdbridge.remote_images")

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def resolve_remote_images(model: DocumentModel) -> DocumentModel:
    """Fetch http(s) images referenced in alt_text as 'alt|url' when data is empty."""
    if not settings.fetch_remote_images:
        return model
    for i, node in enumerate(model.nodes):
        if not isinstance(node, Image) or node.data:
            continue
        url = _url_from_alt(node.alt_text)
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        try:
            data, ctype = fetch_bytes(
                url,
                timeout=settings.fetch_remote_timeout,
                max_bytes=settings.fetch_remote_max_bytes,
            )
            mime = ctype if ctype.startswith("image/") else _guess_mime(url)
            alt = node.alt_text.split("|", 1)[0] if "|" in node.alt_text else node.alt_text
            model.nodes[i] = Image(data=data, alt_text=alt or url, mime_type=mime)
        except Exception as exc:
            _log.warning("skip remote image %s: %s", url[:80], exc)
    return model


def _url_from_alt(alt: str) -> str:
    if "|" in alt:
        return alt.split("|", 1)[1].strip()
    if alt.startswith(("http://", "https://")):
        return alt.strip()
    return ""


def _guess_mime(url: str) -> str:
    lower = url.lower().split("?", 1)[0]
    for ext, mime in _MIME_BY_EXT.items():
        if lower.endswith(ext):
            return mime
    return "image/png"
