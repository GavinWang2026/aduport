"""Optional image downsampling / re-encode to control export size."""

from __future__ import annotations

from io import BytesIO

from app.config import settings
from app.core.document_model import DocumentModel, Image


def maybe_compress_images(model: DocumentModel) -> DocumentModel:
    """
    Downsample large images when MDBRIDGE_IMAGE_COMPRESS is enabled.
    Soft-depends on Pillow; no-op if unavailable or disabled.
    """
    if not settings.image_compress:
        return model
    try:
        from PIL import Image as PILImage
    except ImportError:
        return model

    max_edge = max(64, settings.image_max_edge)
    quality = max(20, min(95, settings.image_jpeg_quality))
    changed = False
    nodes = list(model.nodes)
    for i, node in enumerate(nodes):
        if not isinstance(node, Image) or not node.data:
            continue
        new_data, new_mime = _compress_one(PILImage, node.data, node.mime_type, max_edge, quality)
        if new_data is not None and new_data != node.data:
            nodes[i] = node.model_copy(update={"data": new_data, "mime_type": new_mime or node.mime_type})
            changed = True
    if not changed:
        return model
    return model.model_copy(update={"nodes": nodes})


def _compress_one(PILImage, data: bytes, mime: str, max_edge: int, quality: int):
    try:
        img = PILImage.open(BytesIO(data))
        img.load()
    except Exception:
        return None, mime
    w, h = img.size
    if max(w, h) <= max_edge and len(data) < 200_000:
        return None, mime
    scale = min(1.0, max_edge / max(w, h))
    if scale < 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((nw, nh), getattr(PILImage, "Resampling", PILImage).LANCZOS)

    out = BytesIO()
    fmt = "JPEG"
    new_mime = "image/jpeg"
    if (mime or "").lower() in ("image/png", "image/gif") and img.mode in ("RGBA", "P", "LA"):
        fmt = "PNG"
        new_mime = "image/png"
        if img.mode == "P":
            img = img.convert("RGBA")
        img.save(out, format=fmt, optimize=True)
    else:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format=fmt, quality=quality, optimize=True)
    compressed = out.getvalue()
    if len(compressed) >= len(data):
        return None, mime
    return compressed, new_mime
