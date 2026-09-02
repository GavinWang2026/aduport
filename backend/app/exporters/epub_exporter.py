"""Minimal EPUB 3 exporter (ZIP + XHTML, no external deps)."""

from __future__ import annotations

import html
import uuid
import zipfile
from io import BytesIO

from app.core.document_model import DocumentModel, Heading
from app.exporters.html_render import render_body_html


def export_epub(model: DocumentModel) -> bytes:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    title = model.title or "Untitled"
    body = render_body_html(model, include_title=False)
    # Build nav from headings
    nav_items = []
    for i, node in enumerate(model.nodes):
        if isinstance(node, Heading) and node.level <= 3:
            nav_items.append(
                f'<li><a href="chapter.xhtml#h-{i}">{html.escape(node.plain_text())}</a></li>'
            )
    nav_ol = "".join(nav_items) or f'<li><a href="chapter.xhtml">{html.escape(title)}</a></li>'

    chapter = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">
<head><meta charset="utf-8"/><title>{html.escape(title)}</title>
<style>
body{{font-family:serif;line-height:1.6;margin:1em}}
img{{max-width:100%}} table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 8px}}
blockquote{{border-left:3px solid #ccc;padding-left:1em;color:#444}}
.comment{{border-left:3px solid #9ab;font-size:0.92em;margin:1em 0;padding:0.5em 1em;background:#f8f9fc;color:#333}}
.comment-author{{font-weight:600}}
.comment-anchor{{color:#666;font-weight:normal}}
</style></head>
<body>
<h1>{html.escape(title)}</h1>
{body}
</body></html>
"""

    nav = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh">
<head><meta charset="utf-8"/><title>Nav</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>目录</h1><ol>{nav_ol}</ol></nav>
</body></html>
"""

    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">{book_id}</dc:identifier>
    <dc:title>{html.escape(title)}</dc:title>
    <dc:language>zh</dc:language>
    <meta property="dcterms:modified">2026-08-31T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
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
        # mimetype must be first and stored (no compression) per EPUB spec
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("EPUB/content.opf", opf)
        zf.writestr("EPUB/nav.xhtml", nav)
        zf.writestr("EPUB/chapter.xhtml", chapter)
    return buf.getvalue()
