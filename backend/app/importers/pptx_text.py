"""Lightweight PPTX extract without docling — slide XML text nodes."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import DocumentModel, Heading, Paragraph, RichText

_A_T = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"


def try_extract_pptx_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = sorted(
        n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
    )
    if not names:
        zf.close()
        return None

    nodes: list = []
    for i, path in enumerate(names[:50]):
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            continue
        texts = [el.text for el in root.iter(_A_T) if el.text and el.text.strip()]
        # also bare local-name fallback
        if not texts:
            texts = [
                el.text
                for el in root.iter()
                if el.tag.endswith("}t") and el.text and el.text.strip()
            ]
        if not texts:
            continue
        if i == 0 and texts:
            nodes.append(Heading(level=1, text=RichText(text=texts[0].strip())))
            for t in texts[1:]:
                nodes.append(Paragraph(text=RichText(text=t.strip())))
        else:
            nodes.append(Heading(level=2, text=RichText(text=f"Slide {i + 1}")))
            for t in texts:
                nodes.append(Paragraph(text=RichText(text=t.strip())))
    zf.close()
    if not nodes:
        return None
    return DocumentModel(title=title or "pptx", nodes=nodes)
