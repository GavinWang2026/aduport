"""Read image bytes back out of zip-based packages.

Exporters embed ``Image.data`` under a media prefix. The matching importer
has to restore an ``Image`` node or the structure check reports the picture
as lost.
"""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from aduport.core.document_model import DocumentModel, Image
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import ZipInflateBudget
from aduport.exporters.odf_common import image_ext
from aduport.importers.import_guard import swallow_import_error

_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
}


def images_in_zip(data: bytes, prefixes: tuple[str, ...]) -> list[Image]:
    """Return ``Image`` nodes for members under ``prefixes`` (e.g. ``Pictures/``)."""
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return []
    budget = ZipInflateBudget()
    found: list[Image] = []
    try:
        for name in zf.namelist():
            if name.endswith("/") or not name.startswith(prefixes):
                continue
            ext = Path(name).suffix.lower()
            if ext not in _MIME:
                continue
            try:
                blob = budget.read(zf, name)
            except (FileTooLargeError, KeyError) as exc:
                swallow_import_error(exc, where="zip_images.py")
                continue
            if not blob:
                continue
            stem = Path(name).stem or "image"
            found.append(Image(data=blob, alt_text=stem, mime_type=_MIME[ext], origin_part=name))
            if len(found) >= 64:
                break
    finally:
        zf.close()
    return found


def append_zip_images(model: DocumentModel, data: bytes, prefixes: tuple[str, ...]) -> None:
    """Add packaged pictures that are not already present as ``Image`` nodes."""
    have = {node.data for node in model.nodes if isinstance(node, Image) and node.data}
    for image in images_in_zip(data, prefixes):
        if image.data in have:
            continue
        have.add(image.data)
        model.nodes.append(image)


def package_images(model: DocumentModel, prefix: str) -> list[tuple[str, bytes, str]]:
    """``(member path, bytes, mime)`` for image nodes that carry bytes."""
    out: list[tuple[str, bytes, str]] = []
    for node in model.nodes:
        if not isinstance(node, Image) or not node.data:
            continue
        mime = node.mime_type or "image/png"
        ext = image_ext(mime)
        out.append((f"{prefix}image{len(out) + 1}{ext}", node.data, mime))
        if len(out) >= 64:
            break
    return out
