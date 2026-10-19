"""Minimal EPUB 3 exporter (ZIP + XHTML, no external deps)."""

from __future__ import annotations

import base64
import hashlib
import html
import re
import uuid
import zipfile
from io import BytesIO

from aduport.core.document_model import DocumentModel, Footnote, Heading, Image
from aduport.core.title_source import (
    _is_fallback,
    _stem,
    first_heading_text,
    is_placeholder_title,
    title_repeats_leading_heading,
)
from aduport.exporters.html_render import render_body_html


def _pack_epub(
    *,
    title: str,
    body_html: str,
    nav_ol: str | None = None,
    include_body_title: bool = True,
    images: list[tuple[str, bytes, str]] | None = None,
) -> bytes:
    # A stock label must not become an <h1> the importer reads back as a heading.
    if _is_fallback(title, ""):
        title = ""
        include_body_title = False
    ident = hashlib.sha256(f"{title}\n{body_html}".encode("utf-8")).hexdigest()
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, ident)}"
    safe_title = html.escape(title) if title else ""
    nav_inner = nav_ol or f'<li><a href="chapter.xhtml">{safe_title}</a></li>'

    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">
<head><meta charset="utf-8"/><meta name="generator" content="AduPort"/><title>{safe_title}</title>
<style>
body{{font-family:serif;line-height:1.6;margin:1em}}
img{{max-width:100%}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 8px}}
blockquote{{border-left:3px solid #ccc;padding-left:1em;color:#444}}
.comment{{border-left:3px solid #9ab;font-size:0.92em;margin:1em 0;padding:0.5em 1em;background:#f8f9fc;color:#333}}
.comment-author{{font-weight:600}}
.comment-anchor{{color:#666;font-weight:normal}}
section.footnotes{{margin-top:2em;border-top:1px solid #ccc;padding-top:1em;font-size:0.92em}}
</style></head>
<body>
{("<h1>" + safe_title + "</h1>") if include_body_title else ""}
{body_html}
</body></html>
"""

    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">
<head><meta charset="utf-8"/><title>Nav</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{nav_inner}</ol></nav>
</body></html>
"""

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{book_id}</dc:identifier>
    <dc:title>{safe_title}</dc:title>
    <dc:language>zh</dc:language>
    <meta property="dcterms:modified">2026-08-31T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    {"".join(
        f'<item id="img{i}" href="{html.escape(href)}" media-type="{html.escape(mime)}"/>'
        for i, (href, _data, mime) in enumerate(images or [], start=1)
    )}
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("EPUB/content.opf", opf)
        zf.writestr("EPUB/nav.xhtml", nav)
        zf.writestr("EPUB/chapter.xhtml", chapter)
        for href, data, _mime in images or []:
            zf.writestr(f"EPUB/{href}", data)
    from aduport.core.reproducible_zip import stabilize_zip_timestamps

    return stabilize_zip_timestamps(buf.getvalue())


def _extract_html_body(raw: str) -> tuple[str, str]:
    """Return (title, body_inner) from an HTML document string."""
    title = "Untitled"
    tm = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    if tm:
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() or title
    bm = re.search(r"<body[^>]*>(.*?)</body>", raw, flags=re.I | re.S)
    body = bm.group(1).strip() if bm else raw.strip()
    body = re.sub(r"<script\b[^>]*>.*?</script>", "", body, flags=re.I | re.S)
    body = re.sub(r"<style\b[^>]*>.*?</style>", "", body, flags=re.I | re.S)
    return title, body


def export_epub_from_html(html_bytes: bytes, *, title: str = "") -> bytes:
    """§6.4 HTML→EPUB 直出：尽量保留原 HTML 正文结构，跳过 AST 往返损失。"""
    raw = html_bytes.decode("utf-8", "replace")
    extracted_title, body = _extract_html_body(raw)
    book_title = (title or extracted_title or "").strip()
    if _is_fallback(book_title, _stem(title)):
        book_title = ""
    nav_items: list[str] = []
    for i, m in enumerate(
        re.finditer(r"<h([1-3])\b[^>]*>(.*?)</h\1>", body, flags=re.I | re.S)
    ):
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not label:
            continue
        anchor = f"h-direct-{i}"
        old = m.group(0)
        if "id=" not in old.lower():
            tagged = re.sub(
                rf"<h{m.group(1)}\b",
                f'<h{m.group(1)} id="{anchor}"',
                old,
                count=1,
                flags=re.I,
            )
            body = body.replace(old, tagged, 1)
        else:
            idm = re.search(r'\bid=["\']([^"\']+)["\']', old, flags=re.I)
            anchor = idm.group(1) if idm else anchor
        nav_items.append(
            f'<li><a href="chapter.xhtml#{html.escape(anchor)}">{html.escape(label)}</a></li>'
        )
    return _pack_epub(title=book_title, body_html=body, nav_ol="".join(nav_items) or None)


def export_epub(model: DocumentModel) -> bytes:
    heading = first_heading_text(model.nodes)
    explicit = (model.title or "").strip()
    repeats = title_repeats_leading_heading(model)
    if explicit and not repeats and not is_placeholder_title(explicit):
        title = explicit
        include_body_title = True
    elif heading:
        title = heading
        include_body_title = False
    else:
        title = "Untitled"
        include_body_title = False
    body = render_body_html(model, include_title=False)
    body, images = _epub_image_files(body, model)
    if any(isinstance(n, Footnote) for n in model.nodes) and 'epub:type="footnotes"' not in body:
        body = body.replace(
            '<section class="footnotes"',
            '<section class="footnotes" epub:type="footnotes"',
            1,
        )
    nav_items = []
    for i, node in enumerate(model.nodes):
        if isinstance(node, Heading) and node.level <= 3:
            nav_items.append(
                f'<li><a href="chapter.xhtml#h-{i}">{html.escape(node.plain_text())}</a></li>'
            )
    return _pack_epub(
        title=title,
        body_html=body,
        nav_ol="".join(nav_items) or None,
        include_body_title=include_body_title,
        images=images,
    )


def _epub_image_files(body: str, model: DocumentModel) -> tuple[str, list[tuple[str, bytes, str]]]:
    """Move data-URI images into EPUB/images so the package has real media."""
    files: list[tuple[str, bytes, str]] = []
    index = 0
    for node in model.nodes:
        if not isinstance(node, Image) or not node.data:
            continue
        index += 1
        mime = node.mime_type or "image/png"
        ext = ".jpg" if "jpeg" in mime else ".gif" if "gif" in mime else ".webp" if "webp" in mime else ".png"
        href = f"images/img{index}{ext}"
        needle = f"data:{mime};base64,{base64.b64encode(node.data).decode('ascii')}"
        if needle not in body:
            continue
        body = body.replace(needle, href, 1)
        files.append((href, node.data, mime))
    return body, files
