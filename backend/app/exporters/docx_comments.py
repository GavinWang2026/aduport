"""Inject real Word comments (word/comments.xml + comment ranges)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from app.core.document_model import Comment


_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def inject_word_comments(docx_bytes: bytes, comments: list[Comment]) -> bytes:
    """
    Post-process a DOCX package to add genuine Word comments.

    For each Comment, appends a paragraph with commentRange markers and a
    matching entry in word/comments.xml (visible in Word Review pane).
    """
    if not comments:
        return docx_bytes

    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
        names = set(zin.namelist())
        files = {n: zin.read(n) for n in zin.namelist()}

    doc_xml = files.get("word/document.xml", b"").decode("utf-8")
    # Insert comment-anchored paragraphs before </w:body>
    comment_paras = []
    comment_entries = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i, c in enumerate(comments):
        cid = i
        anchor = escape(c.anchor or c.text[:40] or f"comment-{cid}")
        text = escape(c.text)
        author = escape(c.author or "reviewer")
        comment_paras.append(
            f'<w:p xmlns:w="{_NS_W}">'
            f'<w:commentRangeStart w:id="{cid}"/>'
            f"<w:r><w:t>{anchor}</w:t></w:r>"
            f'<w:commentRangeEnd w:id="{cid}"/>'
            f"<w:r>"
            f'<w:rPr><w:rStyle w:val="CommentReference"/></w:rPr>'
            f'<w:commentReference w:id="{cid}"/>'
            f"</w:r>"
            f"</w:p>"
        )
        comment_entries.append(
            f'<w:comment w:id="{cid}" w:author="{author}" w:date="{now}" w:initials="{author[:2]}">'
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
            f"</w:comment>"
        )

    insert = "".join(comment_paras)
    if "</w:body>" in doc_xml:
        doc_xml = doc_xml.replace("</w:body>", insert + "</w:body>", 1)
    else:
        doc_xml += insert
    files["word/document.xml"] = doc_xml.encode("utf-8")

    comments_xml = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{_NS_W}">'
        f"{''.join(comment_entries)}"
        f"</w:comments>"
    )
    files["word/comments.xml"] = comments_xml.encode("utf-8")

    # Content types
    ct = files.get("[Content_Types].xml", b"").decode("utf-8")
    override = (
        '<Override PartName="/word/comments.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
    )
    if "comments.xml" not in ct:
        ct = ct.replace("</Types>", override + "</Types>")
        files["[Content_Types].xml"] = ct.encode("utf-8")

    # Relationships
    rels_path = "word/_rels/document.xml.rels"
    rels = files.get(rels_path, b"").decode("utf-8")
    if "comments.xml" not in rels:
        # find max rId
        import re

        ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
        next_id = (max(ids) if ids else 1) + 1
        rel = (
            f'<Relationship Id="rId{next_id}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" '
            f'Target="comments.xml"/>'
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
        if "word/comments.xml" not in names:
            pass  # already in files
    return out.getvalue()
