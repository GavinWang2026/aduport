"""Lightweight PDF text extraction via pypdfium2 (no Hugging Face models).

PDFium (libpdfium.dylib) is **not thread-safe**. Concurrent opens/extracts on
macOS routinely abort the process with EXC_BREAKPOINT / SIGTRAP. All native
access is serialized on `_PDFIUM_LOCK`, and page text is always extracted
sequentially from a single PdfDocument.
"""

from __future__ import annotations

import logging
import re
import threading
from io import BytesIO

from app.config import settings
from app.core.document_model import DocumentModel, HorizontalRule, Paragraph, RichText
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers.pdf_font_fix import normalize_pdf_text

_log = logging.getLogger(__name__)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PDFIUM_LOCK = threading.RLock()


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


def _strip_running_headers_footers(page_texts: list[str]) -> list[str]:
    """Drop short lines that repeat across many pages (typical headers/footers).

    Conservative: only removes lines that appear on ≥50% of non-empty pages,
    are ≤40 chars, and are not the sole content of a page.
    """
    pages = [t for t in page_texts if t and t.strip()]
    if len(pages) < 2:
        return page_texts

    from collections import Counter

    counts: Counter[str] = Counter()
    for text in pages:
        # Consider first 2 and last 2 non-empty lines as header/footer candidates
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
        if not lines:
            continue
        candidates = set()
        for ln in lines[:2] + lines[-2:]:
            if 1 <= len(ln) <= 40:
                candidates.add(ln)
        for ln in candidates:
            counts[ln] += 1

    threshold = max(2, (len(pages) + 1) // 2)
    noise = {ln for ln, n in counts.items() if n >= threshold}
    if not noise:
        return page_texts

    cleaned: list[str] = []
    for text in page_texts:
        if not text:
            cleaned.append(text)
            continue
        kept = [
            ln
            for ln in text.replace("\r\n", "\n").split("\n")
            if ln.strip() not in noise
        ]
        # Keep at least something if stripping would empty the page
        if any(ln.strip() for ln in kept):
            cleaned.append("\n".join(kept).strip())
        else:
            cleaned.append(text)
    return cleaned


def _require_pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise InvalidDocumentError(
            "PDF 文本回退需要 pypdfium2：pip install 'pypdfium2>=4.30'"
        ) from exc
    return pdfium


def _extract_all_page_texts(source_bytes: bytes) -> list[str]:
    """Return stripped text for every page. Must hold `_PDFIUM_LOCK`."""
    texts, _, _ = _analyze_pdf_once(source_bytes, layout=False, multicolumn=False)
    return texts


def _analyze_pdf_once(
    source_bytes: bytes,
    *,
    layout: bool = True,
    multicolumn: bool = True,
    max_layout_pages: int = 3,
    max_blocks: int = 200,
) -> tuple[list[str], int, list[dict[str, object]]]:
    """Single PdfDocument open for text + multicolumn count + layout blocks (P1 #18)."""
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    texts: list[str] = []
    multi_count = 0
    blocks: list[dict[str, object]] = []
    try:
        n_pages = len(doc)
        want_multi = bool(multicolumn and settings.pdf_text_multicolumn)
        for i in range(n_pages):
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    raw = _page_text_with_columns(page, textpage) or ""
                    texts.append((raw or "").strip())
                    if want_multi:
                        try:
                            full = (textpage.get_text_bounded() or "").strip()
                            width = float(page.get_width())
                            height = float(page.get_height())
                            mid = width / 2.0
                            gap = width * 0.02
                            left = (
                                textpage.get_text_bounded(
                                    left=0, bottom=0, right=mid - gap, top=height
                                )
                                or ""
                            ).strip()
                            right = (
                                textpage.get_text_bounded(
                                    left=mid + gap, bottom=0, right=width, top=height
                                )
                                or ""
                            ).strip()
                            if len(left) >= 80 and len(right) >= 80:
                                merged = left + "\n\n" + right
                                if merged != full and len(merged) > len(full) * 0.85:
                                    multi_count += 1
                        except Exception:
                            pass
                    if layout and i < max_layout_pages and len(blocks) < max_blocks:
                        try:
                            n = textpage.count_chars()
                            if n > 0:
                                chars: list[tuple[float, float, float, float, str]] = []
                                for ci in range(n):
                                    box = textpage.get_charbox(ci)
                                    ch = textpage.get_text_range(ci, 1) or ""
                                    if not ch.strip() and ch != " ":
                                        continue
                                    left, bottom, right, top = box
                                    chars.append((left, bottom, right, top, ch))
                                if chars:
                                    page_h = float(page.get_height())
                                    lines: list[
                                        list[tuple[float, float, float, float, str]]
                                    ] = []
                                    for c in chars:
                                        placed = False
                                        for line in lines:
                                            if abs(line[0][1] - c[1]) < 3.0:
                                                line.append(c)
                                                placed = True
                                                break
                                        if not placed:
                                            lines.append([c])
                                    for line in sorted(lines, key=lambda ln: -ln[0][1]):
                                        left = min(x[0] for x in line)
                                        bottom = min(x[1] for x in line)
                                        right = max(x[2] for x in line)
                                        top = max(x[3] for x in line)
                                        text = "".join(
                                            x[4] for x in sorted(line, key=lambda x: x[0])
                                        )
                                        blocks.append(
                                            {
                                                "page": i + 1,
                                                "left": round(left, 2),
                                                "bottom": round(bottom, 2),
                                                "right": round(right, 2),
                                                "top": round(top, 2),
                                                "y_from_top": round(page_h - top, 2),
                                                "text": text.strip(),
                                            }
                                        )
                                        if len(blocks) >= max_blocks:
                                            break
                        except Exception:
                            pass
                finally:
                    textpage.close()
            finally:
                page.close()
        return texts, multi_count, blocks
    finally:
        doc.close()


def _page_text_with_columns(page, textpage) -> str:
    """Full-page text; optionally merge left/right halves for two-column layouts."""
    full = (textpage.get_text_bounded() or "").strip()
    if not settings.pdf_text_multicolumn:
        return full
    try:
        width = float(page.get_width())
        height = float(page.get_height())
    except Exception:
        return full
    if width < 200 or height < 200:
        return full
    mid = width / 2.0
    gap = width * 0.02
    left = (textpage.get_text_bounded(left=0, bottom=0, right=mid - gap, top=height) or "").strip()
    right = (textpage.get_text_bounded(left=mid + gap, bottom=0, right=width, top=height) or "").strip()
    if len(left) < 80 or len(right) < 80:
        return full
    # Prefer column merge when halves are substantial and not near-duplicates of full
    if abs(len(left) + len(right) - len(full)) > max(40, len(full) * 0.15):
        # Halves don't reconstruct full well → keep reading order from full
        if left.count("\n") + right.count("\n") < full.count("\n") * 0.4:
            return full
    return left + "\n\n" + right


def extract_pdf_text_model(source_bytes: bytes, filename: str = "input.pdf") -> DocumentModel:
    """
    Extract embedded text layer page-by-page (always sequential under a lock).

    Suitable for digital (non-scanned) PDFs when docling models are unavailable.
    ``MDBRIDGE_PDF_TEXT_PARALLEL`` is ignored — parallel PDFium use crashes on macOS.
    """
    if settings.pdf_text_parallel:
        _log.warning(
            "MDBRIDGE_PDF_TEXT_PARALLEL is set but ignored — PDFium is not thread-safe"
        )

    try:
        with _PDFIUM_LOCK:
            page_texts, multicolumn_pages, layout_blocks = _analyze_pdf_once(
                source_bytes
            )
    except InvalidDocumentError:
        raise
    except Exception as exc:
        raise CorruptedFileError(f"无法打开 PDF: {filename} — {exc}") from exc

    page_count = len(page_texts)
    if page_count < 1:
        raise InvalidDocumentError(f"PDF 无页面: {filename}")

    page_texts = _strip_running_headers_footers(page_texts)

    nodes: list = []
    total_chars = 0
    for i, text in enumerate(page_texts):
        if not text:
            continue
        text = normalize_pdf_text(text)
        if not text.strip():
            continue
        total_chars += len(text)
        # Page separators — do not forge H2 "第 N 页" into the outline (P1 #8)
        if page_count > 1 and nodes:
            nodes.append(HorizontalRule())
        for para in _page_to_paragraphs(text):
            nodes.append(Paragraph(text=RichText(text=para)))

    if total_chars < 1:
        raise InvalidDocumentError(
            f"PDF 无可提取文本层（可能为扫描件）: {filename}。"
            "请启用 OCR 并预下载 docling 模型（见 scripts/download_docling_models.sh），"
            "或设置 HF_ENDPOINT 镜像后重试。"
        )

    title = filename.rsplit(".", 1)[0] if filename else ""
    layout_meta = {
        "page_count": page_count,
        "multicolumn_pages": multicolumn_pages,
        "reconstruction": "charbox_v1+text_layer+header_strip",
        "full_layout": bool(layout_blocks),
        "pixel_mode": "charbox_v1",
        "blocks": layout_blocks,
        "page_breaks": page_count - 1 if page_count > 1 else 0,
    }
    return DocumentModel(
        title=title,
        nodes=nodes,
        metadata={
            "pdf_layout": layout_meta,
            "importer": "pdf_text",
            "pdf_text_chars": total_chars,
        },
    )


def _count_multicolumn_pages(source_bytes: bytes) -> int:
    """Pages where left/right column heuristic would merge (layout hint)."""
    if not settings.pdf_text_multicolumn:
        return 0
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    count = 0
    try:
        for i in range(len(doc)):
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    full = (textpage.get_text_bounded() or "").strip()
                    width = float(page.get_width())
                    height = float(page.get_height())
                    mid = width / 2.0
                    gap = width * 0.02
                    left = (
                        textpage.get_text_bounded(
                            left=0, bottom=0, right=mid - gap, top=height
                        )
                        or ""
                    ).strip()
                    right = (
                        textpage.get_text_bounded(
                            left=mid + gap, bottom=0, right=width, top=height
                        )
                        or ""
                    ).strip()
                    if len(left) >= 80 and len(right) >= 80:
                        merged = left + "\n\n" + right
                        if merged != full and len(merged) > len(full) * 0.85:
                            count += 1
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        doc.close()
    return count


def _extract_layout_blocks(source_bytes: bytes, *, max_pages: int = 3, max_blocks: int = 200) -> list[dict[str, object]]:
    """Group PDFium char boxes into positioned line blocks (pixel-level layout hint)."""
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    blocks: list[dict[str, object]] = []
    try:
        for page_idx in range(min(len(doc), max_pages)):
            page = doc[page_idx]
            try:
                textpage = page.get_textpage()
                try:
                    n = textpage.count_chars()
                    if n <= 0:
                        continue
                    chars: list[tuple[float, float, float, float, str]] = []
                    for i in range(n):
                        box = textpage.get_charbox(i)
                        ch = textpage.get_text_range(i, 1) or ""
                        if not ch.strip() and ch != " ":
                            continue
                        left, bottom, right, top = box
                        chars.append((left, bottom, right, top, ch))
                    if not chars:
                        continue
                    page_h = float(page.get_height())
                    # Group into lines by bottom coordinate
                    lines: list[list[tuple[float, float, float, float, str]]] = []
                    for c in chars:
                        placed = False
                        for line in lines:
                            if abs(line[0][1] - c[1]) < 3.0:
                                line.append(c)
                                placed = True
                                break
                        if not placed:
                            lines.append([c])
                    for line in sorted(lines, key=lambda ln: -ln[0][1]):
                        left = min(x[0] for x in line)
                        bottom = min(x[1] for x in line)
                        right = max(x[2] for x in line)
                        top = max(x[3] for x in line)
                        text = "".join(x[4] for x in sorted(line, key=lambda x: x[0]))
                        blocks.append(
                            {
                                "page": page_idx + 1,
                                "left": round(left, 2),
                                "bottom": round(bottom, 2),
                                "right": round(right, 2),
                                "top": round(top, 2),
                                "y_from_top": round(page_h - top, 2),
                                "text": text.strip(),
                            }
                        )
                        if len(blocks) >= max_blocks:
                            break
                finally:
                    textpage.close()
            finally:
                page.close()
            if len(blocks) >= max_blocks:
                break
    finally:
        doc.close()
    return blocks


def pdf_text_char_count(source_bytes: bytes) -> int:
    try:
        _require_pdfium()
    except InvalidDocumentError:
        _log.warning(
            "pypdfium2 not installed — PDF text-layer count returns 0; "
            "install md-bridge core deps (pypdfium2>=4.30)"
        )
        return 0
    try:
        with _PDFIUM_LOCK:
            texts = _extract_all_page_texts(source_bytes)
        return sum(len(t) for t in texts)
    except Exception:
        return 0
