"""Markdown importer using markdown-it-py (single-pass, no token double-consume)."""

from __future__ import annotations

import base64
import html as html_lib
import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.token import Token

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Paragraph,
    RichText,
    Table,
)
from app.core.exceptions import InvalidDocumentError

_DATA_URI_RE = re.compile(
    r"^data:(image/[^;]+);base64,(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$", re.DOTALL)
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:\s*(.*)$", re.MULTILINE)
_DISPLAY_MATH_RE = re.compile(r"\$\$([\s\S]+?)\$\$")
_SAFE_HTML_TAGS = frozenset(
    {
        "p",
        "br",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "code",
        "pre",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "a",
        "blockquote",
        "hr",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "img",
        "span",
        "div",
    }
)


def import_md(source_bytes: bytes) -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("Markdown 文件必须是有效的 UTF-8 编码") from exc

    footnotes: list[Footnote] = []
    text, footnotes = _extract_footnotes(text)
    text = _protect_display_math(text)

    md = (
        MarkdownIt("commonmark", {"html": True, "breaks": False})
        .enable("table")
        .enable("strikethrough")
    )
    tokens = md.parse(text)
    model = DocumentModel(nodes=[])
    pending_table_meta: dict | None = None
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.type == "heading_open":
            level = int(token.tag[1])
            inline = tokens[i + 1]
            model.nodes.extend(_nodes_from_inline(inline, wrap="heading", level=level))
            i += 3
            continue
        if token.type == "paragraph_open":
            inline = tokens[i + 1]
            if inline.type == "inline":
                model.nodes.extend(_nodes_from_inline(inline, wrap="paragraph"))
            i += 3
            continue
        if token.type == "blockquote_open":
            bq, consumed = _parse_blockquote(tokens, i)
            if bq:
                model.nodes.append(bq)
            i += consumed
            continue
        if token.type in ("bullet_list_open", "ordered_list_open"):
            ordered = token.type == "ordered_list_open"
            items, consumed = _parse_list(tokens, i, ordered, base_level=0)
            model.nodes.extend(items)
            i += consumed
            continue
        if token.type == "fence":
            lang = (token.info or "").split()[0] if token.info else ""
            content = token.content.rstrip("\n")
            if lang.lower() in ("math", "latex"):
                model.nodes.append(MathBlock(content=content, display=True))
            else:
                model.nodes.append(CodeBlock(lang=lang, content=content))
            i += 1
            continue
        if token.type == "code_block":
            model.nodes.append(CodeBlock(lang="", content=token.content.rstrip("\n")))
            i += 1
            continue
        if token.type == "table_open":
            table, consumed = _parse_table(tokens, i)
            if table:
                if pending_table_meta:
                    from app.exporters.table_span import apply_table_span_metadata

                    apply_table_span_metadata(table, pending_table_meta)
                    pending_table_meta = None
                model.nodes.append(table)
            i += consumed
            continue
        if token.type == "hr":
            model.nodes.append(HorizontalRule())
            i += 1
            continue
        if token.type in ("html_block", "html_inline"):
            from app.exporters.table_span import parse_table_span_comment

            meta = parse_table_span_comment(token.content or "")
            if meta:
                pending_table_meta = meta
                i += 1
                continue
            nodes = _nodes_from_html(token.content or "")
            model.nodes.extend(nodes)
            i += 1
            continue
        i += 1

    model.nodes.extend(footnotes)
    _postprocess_inline_semantics(model, {fn.id for fn in footnotes})
    return model


_URL_RE = re.compile(r"https?://[^\s<>\[\]()`'\"]+[^\s<>\[\]()`'\".;,!?)]")
_FN_REF_RE = re.compile(r"\[\^([^\]]+)\]")


def _postprocess_inline_semantics(model: DocumentModel, footnote_ids: set[str]) -> None:
    """Autolink bare URLs and turn [^id] into link marks → #fn-id."""
    for node in model.nodes:
        if isinstance(node, (Paragraph, Heading, ListItem)):
            node.text = _enrich_rich(ensure_rich(node.text), footnote_ids)
            if isinstance(node, ListItem):
                for child in node.children:
                    child.text = _enrich_rich(ensure_rich(child.text), footnote_ids)
        elif isinstance(node, Blockquote):
            node.paragraphs = [
                _enrich_rich(ensure_rich(p), footnote_ids) for p in node.paragraphs
            ]


def ensure_rich(value: RichText | str) -> RichText:
    if isinstance(value, RichText):
        return value
    return RichText(text=value)


def _enrich_rich(rich: RichText, footnote_ids: set[str]) -> RichText:
    text = rich.text
    marks = list(rich.marks)
    # Footnote refs first (they are short and explicit)
    for m in _FN_REF_RE.finditer(text):
        fid = m.group(1)
        if footnote_ids and fid not in footnote_ids:
            continue
        start, end = m.start(), m.end()
        if _overlaps_protected(marks, start, end):
            continue
        marks.append(
            InlineMark(
                type=InlineMarkType.LINK,
                start=start,
                end=end,
                href=f"#fn-{fid}",
            )
        )
    # Autolink bare http(s) URLs
    for m in _URL_RE.finditer(text):
        start, end = m.start(), m.end()
        if _overlaps_protected(marks, start, end):
            continue
        marks.append(
            InlineMark(
                type=InlineMarkType.LINK,
                start=start,
                end=end,
                href=m.group(0),
            )
        )
    marks.sort(key=lambda x: (x.start, x.end))
    return RichText(text=text, marks=marks)


def _overlaps_protected(marks: list[InlineMark], start: int, end: int) -> bool:
    for m in marks:
        if m.type in (InlineMarkType.LINK, InlineMarkType.CODE) and not (
            end <= m.start or start >= m.end
        ):
            return True
    return False


def _extract_footnotes(text: str) -> tuple[str, list[Footnote]]:
    footnotes: list[Footnote] = []
    for match in _FOOTNOTE_DEF_RE.finditer(text):
        footnotes.append(Footnote(id=match.group(1), text=match.group(2).strip()))
    cleaned = _FOOTNOTE_DEF_RE.sub("", text)
    # Collapse excessive blank lines left by removal
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip() + ("\n" if text.endswith("\n") else "")
    return cleaned, footnotes


def _protect_display_math(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        body = m.group(1).strip()
        return f"\n```math\n{body}\n```\n"

    return _DISPLAY_MATH_RE.sub(repl, text)


def _parse_blockquote(tokens: list[Token], start: int) -> tuple[Blockquote | None, int]:
    paragraphs: list[RichText | str] = []
    i = start + 1
    while i < len(tokens) and tokens[i].type != "blockquote_close":
        if tokens[i].type == "paragraph_open":
            inline = tokens[i + 1]
            if inline.type == "inline":
                for n in _nodes_from_inline(inline, wrap="paragraph"):
                    if isinstance(n, Paragraph):
                        paragraphs.append(n.text)
                    elif isinstance(n, Image):
                        paragraphs.append(n.alt_text)
            i += 3
            continue
        if tokens[i].type == "blockquote_open":
            # Nested quote: flatten paragraphs
            nested, consumed = _parse_blockquote(tokens, i)
            if nested:
                paragraphs.extend(nested.paragraphs)
            i += consumed
            continue
        if tokens[i].type in ("bullet_list_open", "ordered_list_open"):
            ordered = tokens[i].type == "ordered_list_open"
            items, consumed = _parse_list(tokens, i, ordered, base_level=0)
            for item in items:
                paragraphs.append(item.text)
            i += consumed
            continue
        i += 1
    if i < len(tokens) and tokens[i].type == "blockquote_close":
        i += 1
    if not paragraphs:
        return None, i - start
    return Blockquote(paragraphs=paragraphs), i - start


def _nodes_from_html(raw: str) -> list:
    raw = (raw or "").strip()
    if not raw:
        return []
    if re.search(r"<\s*table\b", raw, re.I):
        from app.importers.html_importer import import_html

        try:
            doc = import_html(raw.encode("utf-8"), "inline.html")
            if doc.nodes:
                return doc.nodes
        except Exception:
            pass
    # Reject dangerous constructs; keep a sanitized fragment for round-trip
    lower = raw.lower()
    if "<script" in lower or "<iframe" in lower or "javascript:" in lower:
        text = re.sub(r"<[^>]+>", "", raw).strip()
        return [Paragraph(text=RichText(text=text))] if text else []

    parser = _SafeHTMLToNodes()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return [HtmlBlock(content=_sanitize_html_fragment(raw))]

    if parser.nodes:
        return parser.nodes
    return [HtmlBlock(content=_sanitize_html_fragment(raw))]


def _sanitize_html_fragment(raw: str) -> str:
    """Strip disallowed tags/attrs; keep a conservative subset."""

    class _San(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.out: list[str] = []

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag not in _SAFE_HTML_TAGS:
                return
            safe_attrs = []
            for k, v in attrs:
                if k.lower() in ("href", "src", "alt", "title") and v and not v.lower().startswith(
                    ("javascript:", "data:text")
                ):
                    safe_attrs.append(f'{k}="{html_lib.escape(v, quote=True)}"')
            attr_s = (" " + " ".join(safe_attrs)) if safe_attrs else ""
            if tag == "br":
                self.out.append("<br/>")
            elif tag == "hr":
                self.out.append("<hr/>")
            elif tag == "img":
                self.out.append(f"<img{attr_s}/>")
            else:
                self.out.append(f"<{tag}{attr_s}>")

        def handle_endtag(self, tag: str) -> None:
            if tag in _SAFE_HTML_TAGS and tag not in ("br", "hr", "img"):
                self.out.append(f"</{tag}>")

        def handle_data(self, data: str) -> None:
            self.out.append(html_lib.escape(data))

    san = _San()
    try:
        san.feed(raw)
        san.close()
    except Exception:
        return html_lib.escape(re.sub(r"<[^>]+>", "", raw))
    return "".join(san.out)


class _SafeHTMLToNodes(HTMLParser):
    """Map simple HTML blocks into AST nodes with inline marks (b/i/code/a)."""

    _MARK_TAGS = {
        "strong": InlineMarkType.BOLD,
        "b": InlineMarkType.BOLD,
        "em": InlineMarkType.ITALIC,
        "i": InlineMarkType.ITALIC,
        "code": InlineMarkType.CODE,
        "s": InlineMarkType.STRIKETHROUGH,
        "del": InlineMarkType.STRIKETHROUGH,
        "strike": InlineMarkType.STRIKETHROUGH,
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list = []
        self._parts: list[str] = []
        self._marks: list[InlineMark] = []
        self._offset = 0
        # (mark_type, start_offset, href)
        self._mark_stack: list[tuple[InlineMarkType, int, str | None]] = []
        self._heading_level: int | None = None
        self._in_blockquote = False
        self._bq_paras: list[RichText] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style", "iframe"):
            self._skip = True
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()
            self._heading_level = int(tag[1])
            return
        if tag == "hr":
            self._flush_para()
            self.nodes.append(HorizontalRule())
            return
        if tag == "blockquote":
            self._flush_para()
            self._in_blockquote = True
            return
        if tag == "br":
            self._append_text("\n")
            return
        if tag == "a":
            href = None
            for k, v in attrs:
                if k.lower() == "href" and v and not str(v).lower().startswith("javascript:"):
                    href = v
                    break
            self._mark_stack.append((InlineMarkType.LINK, self._offset, href))
            return
        mark = self._MARK_TAGS.get(tag)
        if mark is not None:
            self._mark_stack.append((mark, self._offset, None))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "iframe"):
            self._skip = False
            return
        if self._skip:
            return
        if tag == "a" or tag in self._MARK_TAGS:
            self._close_mark(tag)
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading_level:
            rich = self._take_rich()
            if rich.text.strip():
                self.nodes.append(Heading(level=self._heading_level, text=rich))
            self._heading_level = None
            return
        if tag in ("p", "div"):
            self._flush_para()
            return
        if tag == "blockquote":
            self._flush_para()
            if self._bq_paras:
                self.nodes.append(Blockquote(paragraphs=list(self._bq_paras)))
            self._bq_paras = []
            self._in_blockquote = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        if not data:
            return
        self._parts.append(data)
        self._offset += len(data)

    def _close_mark(self, tag: str) -> None:
        want = InlineMarkType.LINK if tag == "a" else self._MARK_TAGS.get(tag)
        if want is None:
            return
        for i in range(len(self._mark_stack) - 1, -1, -1):
            mtype, start, href = self._mark_stack[i]
            if mtype == want:
                self._mark_stack.pop(i)
                end = self._offset
                if end > start:
                    self._marks.append(
                        InlineMark(type=mtype, start=start, end=end, href=href)
                    )
                return

    def _take_rich(self) -> RichText:
        # Close any unclosed marks at current offset
        while self._mark_stack:
            mtype, start, href = self._mark_stack.pop()
            if self._offset > start:
                self._marks.append(
                    InlineMark(type=mtype, start=start, end=self._offset, href=href)
                )
        text = "".join(self._parts)
        marks = sorted(self._marks, key=lambda m: (m.start, m.end))
        self._parts = []
        self._marks = []
        self._offset = 0
        return RichText(text=text, marks=marks)

    def _flush_para(self) -> None:
        rich = self._take_rich()
        if not rich.text.strip() and not rich.marks:
            return
        # strip outer whitespace but keep mark offsets consistent by not stripping
        # for mark fidelity; trim only if no marks
        if not rich.marks:
            rich = RichText(text=rich.text.strip())
            if not rich.text:
                return
        if self._in_blockquote:
            self._bq_paras.append(rich)
        else:
            self.nodes.append(Paragraph(text=rich))


def _nodes_from_inline(
    inline: Token,
    *,
    wrap: str,
    level: int = 1,
) -> list:
    """Convert an inline token into block nodes (paragraphs / heading / images)."""
    if not inline.children:
        text = (inline.content or "").strip()
        if not text:
            return []
        rich = RichText(text=text)
        if wrap == "heading":
            return [Heading(level=level, text=rich)]
        return [Paragraph(text=rich)]

    nodes: list = []
    parts: list[str] = []
    marks: list[InlineMark] = []
    offset = 0
    children = inline.children
    idx = 0

    def flush_text() -> None:
        nonlocal parts, marks, offset
        text = "".join(parts)
        if not text.strip() and not marks:
            parts = []
            marks = []
            offset = 0
            return
        rich = RichText(text=text, marks=list(marks))
        if wrap == "heading" and not nodes:
            nodes.append(Heading(level=level, text=rich))
        else:
            nodes.append(Paragraph(text=rich))
        parts = []
        marks = []
        offset = 0

    while idx < len(children):
        child = children[idx]
        t = child.type

        if t == "text":
            parts.append(child.content)
            offset += len(child.content)
            idx += 1
            continue

        if t == "code_inline":
            start = offset
            parts.append(child.content)
            offset += len(child.content)
            marks.append(InlineMark(type=InlineMarkType.CODE, start=start, end=offset))
            idx += 1
            continue

        if t == "softbreak":
            parts.append(" ")
            offset += 1
            idx += 1
            continue

        if t == "hardbreak":
            parts.append("\n")
            offset += 1
            idx += 1
            continue

        if t in ("strong_open", "em_open", "s_open", "link_open"):
            close_map = {
                "strong_open": "strong_close",
                "em_open": "em_close",
                "s_open": "s_close",
                "link_open": "link_close",
            }
            mark_map = {
                "strong_open": InlineMarkType.BOLD,
                "em_open": InlineMarkType.ITALIC,
                "s_open": InlineMarkType.STRIKETHROUGH,
                "link_open": InlineMarkType.LINK,
            }
            close_type = close_map[t]
            mark_type = mark_map[t]
            href = child.attrGet("href") if t == "link_open" else None
            start = offset
            idx += 1
            inner_parts: list[str] = []
            depth = 1
            while idx < len(children):
                c = children[idx]
                if c.type == t:
                    depth += 1
                    idx += 1
                    continue
                if c.type == close_type:
                    depth -= 1
                    idx += 1
                    if depth == 0:
                        break
                    continue
                if c.type in ("text", "code_inline"):
                    inner_parts.append(c.content)
                elif c.type == "softbreak":
                    inner_parts.append(" ")
                elif c.type == "hardbreak":
                    inner_parts.append("\n")
                elif c.type == "image":
                    alt = c.content or c.attrGet("alt") or ""
                    inner_parts.append(alt)
                idx += 1
            inner = "".join(inner_parts)
            parts.append(inner)
            end = start + len(inner)
            offset = end
            marks.append(InlineMark(type=mark_type, start=start, end=end, href=href))
            continue

        if t in ("strong_close", "em_close", "s_close", "link_close"):
            idx += 1
            continue

        if t == "image":
            flush_text()
            img = _image_from_token(child)
            if img is not None:
                nodes.append(img)
            else:
                alt = child.content or child.attrGet("alt") or "image"
                src = child.attrGet("src") or ""
                nodes.append(Paragraph(text=RichText(text=f"![{alt}]({src})")))
            idx += 1
            continue

        if t in ("html_inline", "html_block"):
            # Keep inline HTML as text content (already in stream) or skip empty
            content = child.content or ""
            stripped = re.sub(r"<[^>]+>", "", content)
            if stripped:
                parts.append(stripped)
                offset += len(stripped)
            idx += 1
            continue

        if child.content:
            parts.append(child.content)
            offset += len(child.content)
        idx += 1

    flush_text()
    return nodes


def _image_from_token(token: Token) -> Image | None:
    src = token.attrGet("src") or ""
    alt = token.content or token.attrGet("alt") or ""
    match = _DATA_URI_RE.match(src.strip())
    if match:
        mime = match.group(1).lower()
        try:
            data = base64.b64decode(match.group(2))
        except Exception:
            return None
        return Image(data=data, alt_text=alt, mime_type=mime)
    if src:
        return Image(data=b"", alt_text=f"{alt}|{src}" if alt else src, mime_type="image/png")
    return None


def _split_task_text(text: RichText | str) -> tuple[RichText | str, bool | None]:
    raw = text.text if isinstance(text, RichText) else text
    m = _TASK_RE.match(raw)
    if not m:
        return text, None
    checked = m.group(1).lower() == "x"
    rest = m.group(2)
    if isinstance(text, RichText):
        # Shift marks by checkbox prefix length
        shift = len(raw) - len(rest)
        marks = [
            InlineMark(
                type=mk.type,
                start=max(0, mk.start - shift),
                end=max(0, mk.end - shift),
                href=mk.href,
            )
            for mk in text.marks
            if mk.end > shift
        ]
        for mk in marks:
            if mk.start < 0:
                mk.start = 0
        return RichText(text=rest, marks=marks), checked
    return rest, checked


def _parse_list(
    tokens: list[Token],
    start: int,
    ordered: bool,
    base_level: int,
) -> tuple[list[ListItem], int]:
    close_type = "ordered_list_close" if ordered else "bullet_list_close"
    items: list[ListItem] = []
    i = start + 1
    while i < len(tokens) and tokens[i].type != close_type:
        if tokens[i].type == "list_item_open":
            i += 1
            item_text: RichText | str | None = None
            item_checked: bool | None = None
            item_children: list[Paragraph] = []
            while i < len(tokens) and tokens[i].type != "list_item_close":
                if tokens[i].type == "paragraph_open":
                    inline = tokens[i + 1]
                    if inline.type == "inline":
                        rich_nodes = _nodes_from_inline(inline, wrap="paragraph")
                        para_text: RichText | str = ""
                        for n in rich_nodes:
                            if isinstance(n, Paragraph):
                                para_text = n.text
                                break
                            if isinstance(n, Image):
                                para_text = n.alt_text
                                break
                        if item_text is None:
                            para_text, item_checked = _split_task_text(para_text if para_text else "")
                            item_text = para_text if para_text else ""
                        else:
                            item_children.append(
                                Paragraph(text=para_text if para_text else "")
                            )
                    i += 3
                    continue
                if tokens[i].type in ("bullet_list_open", "ordered_list_open"):
                    # Flush current item before nested list
                    if item_text is not None:
                        items.append(
                            ListItem(
                                text=item_text,
                                ordered=ordered,
                                level=base_level,
                                checked=item_checked,
                                children=item_children,
                            )
                        )
                        item_text = None
                        item_checked = None
                        item_children = []
                    nested_ordered = tokens[i].type == "ordered_list_open"
                    nested, consumed = _parse_list(tokens, i, nested_ordered, base_level + 1)
                    items.extend(nested)
                    i += consumed
                    continue
                if tokens[i].type == "fence":
                    content = tokens[i].content.rstrip("\n")
                    if item_text is None:
                        item_text = content
                    else:
                        item_children.append(Paragraph(text=content))
                    i += 1
                    continue
                i += 1
            if item_text is not None:
                items.append(
                    ListItem(
                        text=item_text,
                        ordered=ordered,
                        level=base_level,
                        checked=item_checked,
                        children=item_children,
                    )
                )
            if i < len(tokens) and tokens[i].type == "list_item_close":
                i += 1
            continue
        i += 1
    if i < len(tokens) and tokens[i].type == close_type:
        i += 1
    return items, i - start


def _parse_table(tokens: list[Token], start: int) -> tuple[Table | None, int]:
    """Parse GFM table tokens. Cell span is th_open / inline / th_close (3 tokens)."""
    rows: list[list[str]] = []
    i = start + 1
    current_row: list[str] = []
    while i < len(tokens) and tokens[i].type != "table_close":
        t = tokens[i]
        if t.type == "tr_open":
            current_row = []
            i += 1
            continue
        if t.type in ("thead_open", "tbody_open", "thead_close", "tbody_close"):
            i += 1
            continue
        if t.type in ("th_open", "td_open"):
            close_type = "th_close" if t.type == "th_open" else "td_close"
            cell = ""
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                cell = tokens[i + 1].content
                i += 2
            else:
                i += 1
            if i < len(tokens) and tokens[i].type == close_type:
                i += 1
            current_row.append(re.sub(r"\s+", " ", cell.strip()))
            continue
        if t.type == "tr_close":
            if current_row:
                rows.append(current_row)
            current_row = []
            i += 1
            continue
        i += 1
    if i < len(tokens) and tokens[i].type == "table_close":
        i += 1
    return (Table(rows=rows) if rows else None), i - start
