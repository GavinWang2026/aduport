"""Inject real Word footnotes (word/footnotes.xml + footnoteReference)."""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from aduport.core.document_model import Footnote

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_SEP = (
    '<w:footnote w:type="separator" w:id="-1">'
    "<w:p><w:r><w:separator/></w:r></w:p>"
    "</w:footnote>"
    '<w:footnote w:type="continuationSeparator" w:id="0">'
    "<w:p><w:r><w:continuationSeparator/></w:r></w:p>"
    "</w:footnote>"
)


def inject_word_footnotes(docx_bytes: bytes, footnotes: list[Footnote]) -> bytes:
    """Post-process a DOCX package so Footnote AST nodes become Word footnotes."""
    if not footnotes:
        return docx_bytes

    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
        files = {n: zin.read(n) for n in zin.namelist()}

    used: set[int] = set()
    ids: list[int] = []
    for i, fn in enumerate(footnotes):
        n = _numeric_id(fn.id, i + 1)
        while n in used or n < 1:
            n += 1
        used.add(n)
        ids.append(n)

    doc_xml = files.get("word/document.xml", b"").decode("utf-8")
    ref_paras: list[str] = []
    entries: list[str] = []
    for fid, fn in zip(ids, footnotes):
        text = escape(fn.text or "")
        ref_paras.append(
            f'<w:p xmlns:w="{_NS_W}">'
            f"<w:r><w:t xml:space=\"preserve\"> </w:t></w:r>"
            f"<w:r>"
            f'<w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
            f'<w:footnoteReference w:id="{fid}"/>'
            f"</w:r>"
            f"</w:p>"
        )
        # Hidden marker keeps the original Markdown id (Word w:id is numeric only).
        marker = escape(fn.id or str(fid))
        entries.append(
            f'<w:footnote w:id="{fid}">'
            f"<w:p>"
            f'<w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr>'
            f"<w:r>"
            f'<w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr>'
            f"<w:footnoteRef/>"
            f"</w:r>"
            f"<w:r><w:rPr><w:vanish/></w:rPr>"
            f'<w:t xml:space="preserve">aduport-fnid:{marker} </w:t></w:r>'
            f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
            f"</w:p>"
            f"</w:footnote>"
        )

    insert = "".join(ref_paras)
    if "</w:body>" in doc_xml:
        doc_xml = doc_xml.replace("</w:body>", insert + "</w:body>", 1)
    else:
        doc_xml += insert
    files["word/document.xml"] = doc_xml.encode("utf-8")

    files["word/footnotes.xml"] = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:footnotes xmlns:w="{_NS_W}">'
        f"{_SEP}{''.join(entries)}"
        f"</w:footnotes>"
    ).encode("utf-8")

    ct = files.get("[Content_Types].xml", b"").decode("utf-8")
    if "footnotes.xml" not in ct:
        override = (
            '<Override PartName="/word/footnotes.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        )
        ct = ct.replace("</Types>", override + "</Types>")
        files["[Content_Types].xml"] = ct.encode("utf-8")

    rels_path = "word/_rels/document.xml.rels"
    rels = files.get(rels_path, b"").decode("utf-8")
    if "footnotes.xml" not in rels:
        found = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
        next_id = (max(found) if found else 1) + 1
        rel = (
            f'<Relationship Id="rId{next_id}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" '
            f'Target="footnotes.xml"/>'
        )
        if "</Relationships>" in rels:
            rels = rels.replace("</Relationships>", rel + "</Relationships>")
        else:
            rels = (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{_NS_R}">{rel}</Relationships>'
            )
        files[rels_path] = rels.encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)
    return out.getvalue()


def _numeric_id(raw: str, fallback: int) -> int:
    try:
        n = int(re.sub(r"[^\d]", "", str(raw) or "") or "0")
    except ValueError:
        n = 0
    return n if n >= 1 else fallback
