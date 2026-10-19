"""Read Markdown image paths from disk when they stay inside an allowed root."""

from __future__ import annotations

from pathlib import Path

from aduport.core.document_model import DocumentModel, Image
from aduport.exporters.export_warnings import warn_export

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def _mime_for(data: bytes, suffix: str) -> str:
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix.lower(), "image/png")


def _roots(filename: str) -> list[Path]:
    from aduport.config import settings

    roots: list[Path] = []
    if filename:
        parent = Path(filename).expanduser()
        if parent.is_file() or parent.suffix:
            parent = parent.parent
        roots.append(parent.resolve())
    raw = (getattr(settings, "media_roots", "") or "").strip()
    for part in raw.split(","):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser().resolve())
    return roots


def _src_of(image: Image) -> str:
    if image.url:
        return image.url.strip()
    alt = image.alt_text or ""
    if "|" in alt:
        return alt.split("|", 1)[1].strip()
    return ""


def _allowed(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def resolve_local_images(model: DocumentModel, *, filename: str = "") -> DocumentModel:
    """Fill ``Image.data`` from a local file. Missing files become a warning."""
    roots = _roots(filename)
    if not roots:
        return model
    from aduport.config import settings

    limit = int(getattr(settings, "media_max_bytes", 8_000_000) or 8_000_000)
    for node in model.nodes:
        if not isinstance(node, Image) or node.data:
            continue
        src = _src_of(node)
        if not src or src.lower().startswith(("http://", "https://", "data:")):
            continue
        path = Path(src).expanduser()
        if not path.is_absolute():
            path = (roots[0] / path).resolve()
        else:
            path = path.resolve()
        if not _allowed(path, roots):
            warn_export(model, f"image_unresolved: {src}")
            continue
        if not path.is_file():
            warn_export(model, f"image_unresolved: {src}")
            continue
        size = path.stat().st_size
        if size <= 0 or size > limit:
            warn_export(model, f"image_unresolved: {src}")
            continue
        data = path.read_bytes()
        node.data = data
        node.mime_type = _mime_for(data, path.suffix)
    return model
