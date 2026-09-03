"""Shared ODF package helpers (meta/styles/manifest) for ODS/ODP/ODT-ish exporters."""

from __future__ import annotations

from xml.sax.saxutils import escape


def odf_meta_xml(*, title: str = "", generator: str = "md-bridge") -> str:
    t = escape((title or "document")[:200])
    g = escape(generator[:80])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-meta
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 office:version="1.2">
 <office:meta>
  <dc:title>{t}</dc:title>
  <meta:generator>{g}</meta:generator>
 </office:meta>
</office:document-meta>
"""


def odf_styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<office:document-styles
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"
 office:version="1.2">
 <office:styles/>
 <office:automatic-styles/>
</office:document-styles>
"""


def odf_manifest_xml(
    root_mime: str,
    *,
    extra_entries: list[str] | None = None,
) -> str:
    entries = [
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{escape(root_mime)}"/>',
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>',
        '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>',
        '<manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>',
    ]
    if extra_entries:
        entries.extend(extra_entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">\n '
        + "\n ".join(entries)
        + "\n</manifest:manifest>\n"
    )


def image_ext(mime: str | None) -> str:
    m = (mime or "").lower()
    if "jpeg" in m or m.endswith("/jpg"):
        return ".jpg"
    if "gif" in m:
        return ".gif"
    if "webp" in m:
        return ".webp"
    if "svg" in m:
        return ".svg"
    return ".png"
