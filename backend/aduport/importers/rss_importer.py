"""RSS 2.0 / Atom feed importer → DocumentModel via Markdown.

Uses :mod:`defusedxml` only (already a core dependency). Adapted from the
MarkItDown RssConverter approach (MIT), without BeautifulSoup/markdownify:
HTML-ish item bodies are lightly stripped to text, then parsed by
:func:`import_md`.
"""

from __future__ import annotations

import io
import re
import textwrap
from html import unescape
from pathlib import Path
from xml.dom import Node
from xml.dom.minidom import Document, Element

from defusedxml import minidom

from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.md_importer import import_md

_BR_RE = re.compile(r"(?i)<br\s*/?>")
_P_CLOSE_RE = re.compile(r"(?i)</p\s*>")
_TAG_RE = re.compile(r"<[^>]+>")


def _local_name(tag: str) -> str:
    if ":" in tag:
        return tag.rsplit(":", 1)[-1]
    return tag


def _child_text(element: Element, tag_name: str) -> str | None:
    """First matching child element's text (local-name aware)."""
    want = _local_name(tag_name).lower()
    for node in element.childNodes:
        if node.nodeType != Node.ELEMENT_NODE:
            continue
        if _local_name(node.tagName).lower() != want:
            continue
        parts: list[str] = []
        for child in node.childNodes:
            if child.nodeType == Node.TEXT_NODE and hasattr(child, "data"):
                parts.append(child.data)
            elif child.nodeType == Node.CDATA_SECTION_NODE and hasattr(child, "data"):
                parts.append(child.data)
            elif child.nodeType == Node.ELEMENT_NODE:
                # Nested markup: serialize children for HTML stripping later.
                parts.append(child.toxml())
        text = "".join(parts).strip()
        return text or None
    # Fallback: getElementsByTagName (handles some prefixed cases)
    nodes = element.getElementsByTagName(tag_name)
    if not nodes:
        return None
    fc = nodes[0].firstChild
    if fc and hasattr(fc, "data"):
        return fc.data
    return None


def _htmlish_to_markdownish(content: str) -> str:
    """Turn common feed HTML into plain-ish Markdown-friendly text."""
    if not content:
        return ""
    if "<" not in content:
        return content.strip() + "\n"
    text = _BR_RE.sub("\n", content)
    text = _P_CLOSE_RE.sub("\n\n", text)
    text = _TAG_RE.sub("", text)
    text = unescape(text)
    lines = text.splitlines()
    if len(lines) > 1:
        text = lines[0] + "\n" + textwrap.dedent("\n".join(lines[1:]))
    return text.strip() + "\n"


def _feed_type(doc: Document) -> str | None:
    if doc.getElementsByTagName("rss"):
        return "rss"
    feeds = doc.getElementsByTagName("feed")
    if feeds and feeds[0].getElementsByTagName("entry"):
        return "atom"
    # Namespace-prefixed Atom: look for localName feed/entry via tag scan
    for el in doc.getElementsByTagName("*"):
        if _local_name(el.tagName).lower() == "feed":
            for child in el.getElementsByTagName("*"):
                if _local_name(child.tagName).lower() == "entry":
                    return "atom"
            break
    return None


def _parse_rss(doc: Document) -> tuple[str, str | None]:
    root = doc.getElementsByTagName("rss")[0]
    channels = root.getElementsByTagName("channel")
    if not channels:
        raise InvalidDocumentError("RSS 缺少 channel")
    channel = channels[0]
    title = _child_text(channel, "title")
    description = _child_text(channel, "description")
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if description:
        parts.append(_htmlish_to_markdownish(description).rstrip())
    for item in channel.getElementsByTagName("item"):
        item_title = _child_text(item, "title")
        pub = _child_text(item, "pubDate")
        desc = _child_text(item, "description")
        # content:encoded often used by WordPress etc.
        content = _child_text(item, "encoded") or _child_text(item, "content:encoded")
        link = _child_text(item, "link")
        block: list[str] = []
        if item_title:
            block.append(f"## {item_title}")
        if pub:
            block.append(f"*Published:* {pub}")
        if link:
            block.append(f"*Link:* {link}")
        if desc:
            block.append(_htmlish_to_markdownish(desc).rstrip())
        if content and content != desc:
            block.append(_htmlish_to_markdownish(content).rstrip())
        if block:
            parts.append("\n".join(block))
    return "\n\n".join(parts).strip() + "\n", title


def _atom_entries(root: Element) -> list[Element]:
    entries = root.getElementsByTagName("entry")
    if entries:
        return list(entries)
    return [
        el
        for el in root.getElementsByTagName("*")
        if el.nodeType == Node.ELEMENT_NODE and _local_name(el.tagName).lower() == "entry"
    ]


def _parse_atom(doc: Document) -> tuple[str, str | None]:
    feeds = doc.getElementsByTagName("feed")
    if not feeds:
        feeds = [
            el
            for el in doc.getElementsByTagName("*")
            if _local_name(el.tagName).lower() == "feed"
        ]
    if not feeds:
        raise InvalidDocumentError("Atom 缺少 feed")
    root = feeds[0]
    title = _child_text(root, "title")
    subtitle = _child_text(root, "subtitle")
    parts: list[str] = []
    if title:
        parts.append(f"# {title}")
    if subtitle:
        parts.append(subtitle.strip())
    for entry in _atom_entries(root):
        entry_title = _child_text(entry, "title")
        updated = _child_text(entry, "updated")
        summary = _child_text(entry, "summary")
        content = _child_text(entry, "content")
        # link href
        link = None
        for node in entry.childNodes:
            if node.nodeType != Node.ELEMENT_NODE:
                continue
            if _local_name(node.tagName).lower() != "link":
                continue
            href = node.getAttribute("href")
            rel = (node.getAttribute("rel") or "alternate").lower()
            if href and rel in ("", "alternate"):
                link = href
                break
        block: list[str] = []
        if entry_title:
            block.append(f"## {entry_title}")
        if updated:
            block.append(f"*Updated:* {updated}")
        if link:
            block.append(f"*Link:* {link}")
        if summary:
            block.append(_htmlish_to_markdownish(summary).rstrip())
        if content and content != summary:
            block.append(_htmlish_to_markdownish(content).rstrip())
        if block:
            parts.append("\n".join(block))
    return "\n\n".join(parts).strip() + "\n", title


def feed_to_markdown(source_bytes: bytes) -> tuple[str, str | None, str]:
    """Return (markdown, title, feed_kind) where feed_kind is ``rss`` or ``atom``."""
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = source_bytes.decode("utf-8", errors="replace")
    try:
        doc = minidom.parse(io.StringIO(text))
    except Exception as exc:
        raise CorruptedFileError(f"无法解析 XML 订阅源: {exc}") from exc
    kind = _feed_type(doc)
    if kind == "rss":
        md, title = _parse_rss(doc)
        return md, title, "rss"
    if kind == "atom":
        md, title = _parse_atom(doc)
        return md, title, "atom"
    raise InvalidDocumentError("不是 RSS 或 Atom 订阅源（缺 rss/feed+entry）")


def import_rss(source_bytes: bytes, filename: str = "feed.rss") -> DocumentModel:
    """Import RSS 2.0 or Atom into :class:`DocumentModel`."""
    md, title, kind = feed_to_markdown(source_bytes)
    model = import_md(md.encode("utf-8"))
    model.title = title or Path(filename or "feed").stem
    model.metadata["importer"] = "rss"
    model.metadata["source_format"] = kind
    model.metadata["feed_type"] = kind
    return model


# Alias used by registry for ``atom`` extension.
import_atom = import_rss


def looks_like_feed(data: bytes) -> bool:
    """Cheap sniff: XML with rss or feed/entry markers in the head."""
    head = data[:4096].lstrip().lower()
    if not (head.startswith(b"<?xml") or head.startswith(b"<rss") or head.startswith(b"<feed")):
        # Still allow BOM / whitespace then root
        sample = data[:8192].lower()
        if b"<rss" not in sample and b"<feed" not in sample:
            return False
    sample = data[:16384].lower()
    if b"<rss" in sample:
        return True
    if b"<feed" in sample and b"<entry" in sample:
        return True
    return False
