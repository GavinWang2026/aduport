"""Lightweight DOCX extract without docling — python-docx + OOXML enrich.

Mammoth-style style-map (v8 §B): when anydoc is off, map common Word styles to
Heading / ListItem / Blockquote and keep run-level bold/italic/strike/code/link.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

from app.core.document_model import (
    Blockquote,
    DocumentModel,
    Heading,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
    Table,
)

# Mammoth-inspired style name → AST kind (case-insensitive).
_STYLE_EXACT: dict[str, tuple[str, int | None]] = {
    "title": ("heading", 1),
    "subtitle": ("heading", 2),
    "quote": ("blockquote", None),
    "blockquote": ("blockquote", None),
    "intense quote": ("blockquote", None),
    "list paragraph": ("list", None),
    "listparagraph": ("list", None),
    "caption": ("paragraph", None),
}
_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-6])\b", re.I)
_HEADING_CN = re.compile(r"^标题\s*([1-6])$")


def try_extract_docx_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    """Best-effort DOCX → AST using python-docx (always in core deps)."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                return None
    except zipfile.BadZipFile:
        return None

    try:
        from docx import Document  # type: ignore
    except ImportError:
        return _zip_xml_fallback(data, title=title)

    try:
        doc = Document(BytesIO(data))
    except Exception:
        return _zip_xml_fallback(data, title=title)

    nodes: list = []
    body = doc.element.body
    from docx.oxml.ns import qn  # type: ignore
    from docx.table import Table as DocxTable  # type: ignore
    from docx.text.paragraph import Paragraph as DocxParagraph  # type: ignore

    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = DocxParagraph(child, doc)
            rich = _paragraph_to_rich(para)
            if not rich.text.strip():
                continue
            style = ""
            try:
                style = (para.style.name or "") if para.style else ""
            except Exception:
                style = ""
            node = _node_from_style(style, rich, para)
            if node is not None:
                nodes.append(node)
        elif child.tag == qn("w:tbl"):
            table = DocxTable(child, doc)
            rows: list[list[str]] = []
            for row in table.rows:
                rows.append([(c.text or "").strip() for c in row.cells])
            if rows:
                nodes.append(Table(rows=rows, header_rows=1 if rows else 0))

    if not nodes:
        return _zip_xml_fallback(data, title=title)

    model = DocumentModel(
        title=title or "docx",
        nodes=nodes,
        metadata={
            "importer": "docx_native",
            "engine": "python-docx",
            "style_map": "mammoth-lite",
        },
    )
    try:
        from app.importers.docx_ooxml import enrich_docx_model

        model = enrich_docx_model(model, data)
    except Exception:
        pass
    return model


def _node_from_style(style: str, rich: RichText, para) -> object | None:
    """Map Word paragraph style (+ numbering) → AST node (mammoth-lite)."""
    key = (style or "").strip().lower()
    kind, level = _STYLE_EXACT.get(key, (None, None))
    if kind is None:
        m = _HEADING_RE.match(key) or _HEADING_CN.match(key)
        if m:
            kind, level = "heading", int(m.group(1))
        elif key.startswith("heading") or key.startswith("标题"):
            kind, level = "heading", 1
            for ch in key:
                if ch.isdigit():
                    level = max(1, min(6, int(ch)))
                    break

    # Numbering definition → list even without List Paragraph style
    if kind is None and _para_has_numbering(para):
        kind = "list"

    if kind == "heading":
        return Heading(level=max(1, min(6, int(level or 1))), text=rich)
    if kind == "blockquote":
        return Blockquote(paragraphs=[rich.text])
    if kind == "list":
        ordered = _para_list_ordered(para)
        return ListItem(text=rich, ordered=ordered, level=0)
    return Paragraph(text=rich)


def _para_has_numbering(para) -> bool:
    try:
        from docx.oxml.ns import qn

        pPr = para._element.find(qn("w:pPr"))
        if pPr is None:
            return False
        return pPr.find(qn("w:numPr")) is not None
    except Exception:
        return False


def _para_list_ordered(para) -> bool:
    """Best-effort: style name hints ordered; otherwise unordered."""
    try:
        style = ""
        try:
            style = (para.style.name or "").lower() if para.style else ""
        except Exception:
            pass
        if "number" in style or "enum" in style or style.startswith("list number"):
            return True
        return False
    except Exception:
        return False


def _paragraph_to_rich(para) -> RichText:
    """§3.4: emit run-level bold/italic/strike/code/link without anydoc."""
    parts: list[str] = []
    marks: list[InlineMark] = []
    offset = 0
    try:
        runs = list(para.runs)
    except Exception:
        text = (para.text or "").strip()
        return RichText(text=text)

    for run in runs:
        chunk = run.text or ""
        if not chunk:
            continue
        start = offset
        end = offset + len(chunk)
        parts.append(chunk)
        try:
            if run.bold:
                marks.append(InlineMark(type=InlineMarkType.BOLD, start=start, end=end))
            if run.italic:
                marks.append(InlineMark(type=InlineMarkType.ITALIC, start=start, end=end))
            if run.font and run.font.strike:
                marks.append(
                    InlineMark(type=InlineMarkType.STRIKETHROUGH, start=start, end=end)
                )
            name = (run.font.name or "") if run.font else ""
            if name and ("courier" in name.lower() or "mono" in name.lower()):
                marks.append(InlineMark(type=InlineMarkType.CODE, start=start, end=end))
            try:
                rstyle = (run.style.name or "").lower() if run.style else ""
            except Exception:
                rstyle = ""
            if rstyle in ("strong", "emphasis", "intense emphasis"):
                if "strong" in rstyle:
                    marks.append(
                        InlineMark(type=InlineMarkType.BOLD, start=start, end=end)
                    )
                if "emphasis" in rstyle:
                    marks.append(
                        InlineMark(type=InlineMarkType.ITALIC, start=start, end=end)
                    )
        except Exception:
            pass
        offset = end

    try:
        from docx.oxml.ns import qn

        for link in para._element.findall(".//" + qn("w:hyperlink")):
            href = ""
            r_id = link.get(qn("r:id")) or link.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            if r_id:
                try:
                    rel = para.part.rels[r_id]
                    href = rel.target_ref or ""
                except Exception:
                    href = ""
            texts = [(t.text or "") for t in link.findall(".//" + qn("w:t"))]
            joined = "".join(texts)
            if not joined:
                continue
            full = "".join(parts)
            pos = full.find(joined)
            if pos >= 0:
                marks.append(
                    InlineMark(
                        type=InlineMarkType.LINK,
                        start=pos,
                        end=pos + len(joined),
                        href=href or None,
                    )
                )
    except Exception:
        pass

    text = "".join(parts).strip()
    if not text:
        return RichText(text="")
    lead = len("".join(parts)) - len("".join(parts).lstrip())
    if lead:
        adjusted = []
        for m in marks:
            s = max(0, m.start - lead)
            e = max(0, m.end - lead)
            if e > s and e <= len(text):
                adjusted.append(InlineMark(type=m.type, start=s, end=e, href=m.href))
        marks = adjusted
    marks.sort(key=lambda m: (m.start, m.end))
    return RichText(text=text, marks=marks)


def _zip_xml_fallback(data: bytes, *, title: str = "") -> DocumentModel | None:
    """Minimal w:t scrape when python-docx cannot open the package."""
    from xml.etree import ElementTree as ET

    from app.core import xml_safe
    from app.core.safe_zip import read_zip_member, zip_inflate_session

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            with zip_inflate_session() as budget:
                budget.bind(zf)
                raw = read_zip_member(zf, "word/document.xml")
    except Exception:
        return None
    try:
        root = xml_safe.fromstring(raw)
    except ET.ParseError:
        return None
    texts: list[str] = []
    buf: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "t" and el.text:
            buf.append(el.text)
        elif tag == "p" and buf:
            texts.append("".join(buf).strip())
            buf = []
    if buf:
        texts.append("".join(buf).strip())
    nodes = [Paragraph(text=RichText(text=t)) for t in texts if t]
    if not nodes:
        return None
    return DocumentModel(
        title=title or "docx",
        nodes=nodes,
        metadata={"importer": "docx_zip_fallback", "engine": "xml"},
    )
