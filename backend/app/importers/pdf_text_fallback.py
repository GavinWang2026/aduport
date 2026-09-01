"""Lightweight PDF text extraction via pypdfium2 (no Hugging Face models)."""

from __future__ import annotations

import re
from io import BytesIO

from app.core.document_model import DocumentModel, Heading, Paragraph, RichText
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers.pdf_font_fix import normalize_pdf_text
from app.config import settings

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if _CJK_RE.match(c)) / max(len(text), 1)


def _join_wrapped_lines(lines: list[str]) -> str:
    """Join soft-wrapped lines; avoid inserting spaces between CJK characters."""
    if not lines:
        return ""
    sample = "".join(lines)
    if _cjk_ratio(sample) >= 0.25:
        out: list[str] = []
        for i, ln in enumerate(lines):
            if i == 0:
                out.append(ln)
                continue
            prev = out[-1]
            # Keep blank-ish separation for list-like / numbered lines
            if re.match(r"^[\d一二三四五六七八九十]+[、.．、]\s*", ln) or ln.startswith(
                ("•", "·", "-", "（", "(")
            ):
                out.append("\n" + ln)
            elif prev.endswith(("。", "！", "？", "；", ".", "!", "?", ":")):
                out.append("\n" + ln)
            else:
                out.append(ln)
        return "".join(out)
    return " ".join(lines)


def _page_to_paragraphs(text: str) -> list[str]:
    """Split a page into paragraph strings."""
    raw_lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    paragraphs: list[str] = []
    buf: list[str] = []
    for ln in raw_lines:
        if not ln:
            if buf:
                paragraphs.append(_join_wrapped_lines(buf))
                buf = []
            continue
        buf.append(ln)
    if buf:
        paragraphs.append(_join_wrapped_lines(buf))
    return [p for p in paragraphs if p.strip()]


def extract_pdf_text_model(source_bytes: bytes, filename: str = "input.pdf") -> DocumentModel:
    """
    Extract embedded text layer page-by-page (optionally in parallel).

    Suitable for digital (non-scanned) PDFs when docling models are unavailable.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise InvalidDocumentError(
            "PDF 文本回退需要 pypdfium2（通常随 docling 安装）"
        ) from exc

    try:
        doc = pdfium.PdfDocument(BytesIO(source_bytes))
        page_count = len(doc)
        doc.close()
    except Exception as exc:
        raise CorruptedFileError(f"无法打开 PDF: {filename} — {exc}") from exc

    if page_count < 1:
        raise InvalidDocumentError(f"PDF 无页面: {filename}")

    workers = max(1, int(settings.pdf_text_workers))
    use_parallel = settings.pdf_text_parallel and page_count > 1 and workers > 1

    if use_parallel:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(workers, page_count)) as pool:
            page_texts = list(
                pool.map(
                    lambda i: _extract_one_page(source_bytes, i),
                    range(page_count),
                )
            )
    else:
        page_texts = [_extract_one_page(source_bytes, i) for i in range(page_count)]

    nodes: list = []
    total_chars = 0
    for i, text in enumerate(page_texts):
        if not text:
            continue
        text = normalize_pdf_text(text)
        if not text.strip():
            continue
        total_chars += len(text)
        if page_count > 1:
            nodes.append(Heading(level=2, text=RichText(text=f"第 {i + 1} 页")))
        for para in _page_to_paragraphs(text):
            nodes.append(Paragraph(text=RichText(text=para)))

    if total_chars < 1:
        raise InvalidDocumentError(
            f"PDF 无可提取文本层（可能为扫描件）: {filename}。"
            "请启用 OCR 并预下载 docling 模型（见 scripts/download_docling_models.sh），"
            "或设置 HF_ENDPOINT 镜像后重试。"
        )

    title = filename.rsplit(".", 1)[0] if filename else ""
    return DocumentModel(title=title, nodes=nodes)


def _extract_one_page(source_bytes: bytes, index: int) -> str:
    """Open a private PdfDocument for thread-safety and extract one page's text."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    try:
        page = doc[index]
        try:
            textpage = page.get_textpage()
            try:
                raw = textpage.get_text_bounded() or ""
            finally:
                textpage.close()
        finally:
            page.close()
        return (raw or "").strip()
    finally:
        doc.close()


def pdf_text_char_count(source_bytes: bytes) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    try:
        doc = pdfium.PdfDocument(BytesIO(source_bytes))
    except Exception:
        return 0
    n = 0
    try:
        for i in range(len(doc)):
            page = doc[i]
            try:
                tp = page.get_textpage()
                n += len((tp.get_text_bounded() or "").strip())
                tp.close()
            finally:
                page.close()
    finally:
        doc.close()
    return n
