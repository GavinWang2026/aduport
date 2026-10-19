"""PDF page operations (merge / split / rotate / extract).

Kept outside ``convert()`` so the converter stays a converter.
Uses pypdfium2, which is already a core dependency.
"""

from __future__ import annotations

import io
import re


class PdfPageError(ValueError):
    """Invalid PDF bytes or page specification."""


def _pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover
        raise PdfPageError("需要 pypdfium2 才能编排 PDF 页面") from exc
    return pdfium


def _open(data: bytes):
    if not data or not data.startswith(b"%PDF"):
        raise PdfPageError("不是有效的 PDF")
    pdfium = _pdfium()
    try:
        return pdfium.PdfDocument(data)
    except Exception as exc:
        raise PdfPageError(f"无法打开 PDF: {exc}") from exc


def _save(doc) -> bytes:
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def page_count(data: bytes) -> int:
    doc = _open(data)
    try:
        return len(doc)
    finally:
        doc.close()


def merge_pdfs(parts: list[bytes]) -> bytes:
    if not parts:
        raise PdfPageError("没有可合并的 PDF")
    pdfium = _pdfium()
    out = pdfium.PdfDocument.new()
    opened = []
    try:
        for data in parts:
            src = _open(data)
            opened.append(src)
            out.import_pages(src)
        if len(out) < 1:
            raise PdfPageError("合并结果为空")
        return _save(out)
    finally:
        for src in opened:
            src.close()
        out.close()


def extract_pages(data: bytes, pages: str) -> bytes:
    """``pages`` is a 1-based spec such as ``1,3-5`` (pypdfium2 syntax)."""
    spec = (pages or "").strip()
    if not spec:
        raise PdfPageError("缺少页码，例如 1,3-5")
    if not re.fullmatch(r"[0-9,\-\s]+", spec):
        raise PdfPageError("页码只能包含数字、逗号和连字符")
    pdfium = _pdfium()
    src = _open(data)
    out = pdfium.PdfDocument.new()
    try:
        out.import_pages(src, pages=spec)
        if len(out) < 1:
            raise PdfPageError("页码超出文档范围")
        return _save(out)
    except PdfPageError:
        raise
    except Exception as exc:
        raise PdfPageError(f"抽取页面失败: {exc}") from exc
    finally:
        src.close()
        out.close()


def split_pdf(data: bytes) -> list[bytes]:
    pdfium = _pdfium()
    src = _open(data)
    pieces: list[bytes] = []
    try:
        if len(src) < 1:
            raise PdfPageError("PDF 没有页面")
        for i in range(len(src)):
            piece = pdfium.PdfDocument.new()
            try:
                piece.import_pages(src, pages=[i])
                pieces.append(_save(piece))
            finally:
                piece.close()
        return pieces
    finally:
        src.close()


def rotate_pdf(data: bytes, degrees: int, pages: str | None = None) -> bytes:
    turn = int(degrees) % 360
    if turn not in (0, 90, 180, 270):
        raise PdfPageError("旋转角度只能是 90、180 或 270")
    doc = _open(data)
    try:
        n = len(doc)
        if pages and pages.strip():
            # Materialize the requested pages into indexes via a throwaway import.
            chosen = _indexes_from_spec(n, pages)
        else:
            chosen = list(range(n))
        for i in chosen:
            page = doc[i]
            page.set_rotation((int(page.get_rotation() or 0) + turn) % 360)
        return _save(doc)
    finally:
        doc.close()


def _indexes_from_spec(n: int, spec: str) -> list[int]:
    indexes: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else n
            if start < 1 or end < start or end > n:
                raise PdfPageError(f"页码范围无效: {part}")
            indexes.extend(range(start - 1, end))
        else:
            num = int(part)
            if num < 1 or num > n:
                raise PdfPageError(f"页码超出范围: {num}")
            indexes.append(num - 1)
    if not indexes:
        raise PdfPageError("没有选中任何页面")
    return indexes
