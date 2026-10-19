"""Image (PNG/JPEG) importer — embed AST image; optional OCR via PDF wrap."""

from __future__ import annotations

import io
from pathlib import Path

from aduport.config import settings
from aduport.core.document_model import DocumentModel, Heading, Image, Paragraph, RichText
from aduport.core.exceptions import InvalidDocumentError
from aduport.importers.import_guard import swallow_import_error

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8\xff"
_MAX_IMAGE_BYTES = 40 * 1024 * 1024
_MAX_IMAGE_PIXELS = 40_000_000

_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}
OCR_DISABLED_NOTICE = "未启用 OCR，仅嵌入图片"
OCR_UNAVAILABLE_NOTICE = "OCR 不可用，仅嵌入图片"
# txt/csv keep a placeholder, not the image bytes. The model notice stays
# honest for targets that really embed the picture.
OCR_DISABLED_PLACEHOLDER = "未启用 OCR，图片以占位符保留"
OCR_UNAVAILABLE_PLACEHOLDER = "OCR 不可用，图片以占位符保留"


def ocr_notice_for_placeholder(text: str) -> str:
    stripped = (text or "").strip()
    if stripped == OCR_UNAVAILABLE_NOTICE:
        return OCR_UNAVAILABLE_PLACEHOLDER
    if stripped == OCR_DISABLED_NOTICE:
        return OCR_DISABLED_PLACEHOLDER
    return text


def _detect_image_kind(data: bytes, filename: str) -> str:
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if data.startswith(_PNG_SIG):
        return "png"
    if data.startswith(_JPEG_SIG):
        return "jpg"
    if ext in ("png", "jpg", "jpeg"):
        return "jpg" if ext == "jpeg" else ext
    raise InvalidDocumentError(f"不是 PNG/JPEG 图像: {filename}")


def _image_to_pdf(data: bytes) -> bytes | None:
    """Wrap a raster image as a one-page PDF for the OCR/PDF pipeline."""
    if len(data) > _MAX_IMAGE_BYTES:
        return swallow_import_error(
            ValueError(f"image too large (>{_MAX_IMAGE_BYTES} bytes)"),
            where="image_importer._image_to_pdf.size",
            default=None,
        )
    try:
        from PIL import Image as PILImage
    except ImportError as exc:
        return swallow_import_error(exc, where="image_importer._image_to_pdf", default=None)
    try:
        # Cap decompression bomb before open (report P3-2).
        PILImage.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        img = PILImage.open(io.BytesIO(data))
        img.load()
        w, h = img.size
        if w * h > _MAX_IMAGE_PIXELS:
            raise PILImage.DecompressionBombError(f"{w}x{h}")
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PDF")
        return buf.getvalue()
    except Exception as exc:
        # Keep DecompressionBombError visible in logs (do not silent-drop without where=).
        return swallow_import_error(exc, where="image_importer._image_to_pdf", default=None)


def import_image(source_bytes: bytes, filename: str = "image.png") -> DocumentModel:
    """Import PNG/JPEG.

    * Always embeds the image bytes on the AST (export can round-trip).
    * When ``ADUPORT_OCR_ENABLED`` and Pillow is available, also runs the
      existing PDF OCR chain on a one-page wrap and prepends recognized text.
    """
    kind = _detect_image_kind(source_bytes, filename)
    mime = _MIME.get(kind, "image/png")
    stem = Path(filename or f"image.{kind}").stem

    nodes: list = [
        Heading(level=1, text=RichText(text=stem)),
        Image(data=source_bytes, alt_text=stem, mime_type=mime),
    ]

    ocr_text = False
    ocr_notice = ""
    if getattr(settings, "ocr_enabled", False):
        pdf_bytes = _image_to_pdf(source_bytes)
        if pdf_bytes:
            try:
                from aduport.importers.ocr_engine import import_pdf_with_ocr

                ocr_model = import_pdf_with_ocr(pdf_bytes, f"{stem}.pdf")
                # Drop duplicate title-ish headings; keep body.
                body = [
                    n
                    for n in ocr_model.nodes
                    if not (isinstance(n, Heading) and n.level == 1)
                ]
                if body:
                    nodes.append(Heading(level=2, text=RichText(text="OCR")))
                    nodes.extend(body)
                    ocr_text = True
            except Exception as exc:
                swallow_import_error(exc, where="image_importer.ocr")
        if not ocr_text:
            ocr_notice = OCR_UNAVAILABLE_NOTICE
    else:
        ocr_notice = OCR_DISABLED_NOTICE

    if ocr_notice:
        nodes.append(Paragraph(text=RichText(text=ocr_notice)))

    model = DocumentModel(title=stem, nodes=nodes)
    model.metadata["importer"] = "image"
    model.metadata["source_format"] = kind
    model.metadata["ocr_applied"] = ocr_text
    if ocr_notice:
        model.metadata["ocr_notice"] = ocr_notice
    return model


def import_png(source_bytes: bytes, filename: str = "image.png") -> DocumentModel:
    return import_image(source_bytes, filename or "image.png")


def import_jpg(source_bytes: bytes, filename: str = "image.jpg") -> DocumentModel:
    return import_image(source_bytes, filename or "image.jpg")
