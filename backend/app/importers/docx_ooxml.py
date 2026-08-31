"""Post-pass OOXML enrichment for DOCX import (comments / headers / theme).

Runs on the raw ZIP bytes after (or without) docling body extraction so
Word Review comments, section headers, and theme accent survive into AST.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import Comment, DocumentModel, Paragraph

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def enrich_docx_model(model: DocumentModel, source_bytes: bytes) -> DocumentModel:
    """Mutate/return model with OOXML-derived comments, header note, theme meta."""
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = set(zf.namelist())
            comments = _parse_comments(zf) if "word/comments.xml" in names else []
            header_text = _parse_headers(zf, names)
            theme = _parse_theme_name(zf) if "word/theme/theme1.xml" in names else ""
    except zipfile.BadZipFile:
        return model

    if theme:
        meta = dict(getattr(model, "metadata", None) or {})
        meta["docx_theme"] = theme
        model.metadata = meta

    if header_text:
        meta = dict(model.metadata or {})
        meta["docx_header"] = header_text[:500]
        model.metadata = meta
        # Surface as leading paragraph so MD/PDF exporters show it
        note = f"[页眉] {header_text[:200]}"
        if not any(isinstance(n, Paragraph) and n.plain_text() == note for n in model.nodes):
            model.nodes.insert(0, Paragraph(text=note))

    existing = {
        (c.author, c.text)
        for c in model.nodes
        if isinstance(c, Comment)
    }
    for c in comments:
        if (c.author, c.text) not in existing:
            model.nodes.append(c)
    return model


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text_of(el: ET.Element) -> str:
    parts: list[str] = []
    for t in el.iter(f"{_W}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts).strip()


def _parse_comments(zf: zipfile.ZipFile) -> list[Comment]:
    raw = zf.read("word/comments.xml")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[Comment] = []
    for c_el in root.findall(f"{_W}comment"):
        author = c_el.get(f"{_W}author") or c_el.get("author") or "reviewer"
        text = _text_of(c_el)
        if text:
            out.append(Comment(author=author, text=text))
    return out


def _parse_headers(zf: zipfile.ZipFile, names: set[str]) -> str:
    header_files = sorted(n for n in names if re.match(r"word/header\d*\.xml$", n))
    chunks: list[str] = []
    for path in header_files:
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            continue
        text = _text_of(root)
        if text:
            chunks.append(text)
    # Unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return " | ".join(uniq)


def _parse_theme_name(zf: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(zf.read("word/theme/theme1.xml"))
    except ET.ParseError:
        return ""
    # Prefer theme name attribute if present
    for el in root.iter():
        if _local(el.tag) == "theme":
            name = el.get("name") or ""
            if name:
                return name
    # Fall back to accent1 scheme color hint
    for el in root.iter():
        if _local(el.tag) == "srgbClr":
            val = el.get("val")
            if val:
                return f"accent:{val}"
    return ""
