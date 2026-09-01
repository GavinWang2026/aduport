"""HTML importer with nested-context stack + inline marks (stdlib html.parser)."""

from __future__ import annotations

from html.parser import HTMLParser

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Heading,
    HorizontalRule,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
    Table,
)
from app.core.exceptions import InvalidDocumentError

_SKIP_TAGS = frozenset({"script", "style", "noscript", "iframe", "svg", "template"})
_BLOCK_FLUSH = frozenset({"p", "div", "section", "article", "main", "header", "footer", "aside"})
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


class _HTMLDocParser(HTMLParser):
    """Stack-based HTML → DocumentModel; preserves b/i/code/a as InlineMark."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list = []
        self._parts: list[str] = []
        self._marks: list[InlineMark] = []
        self._offset = 0
        self._mark_stack: list[tuple[InlineMarkType, int, str | None]] = []
        self._stack: list[str] = []
        self._list_stack: list[bool] = []
        self._table_rows: list[list[str]] = []
        self._row: list[str] = []
        self._bq_paras: list[RichText] = []
        self._in_pre = False
        self._pre_lang = ""
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_text_as_para()
            self._stack.append(tag)
            self._reset_buf()
        elif tag == "p":
            self._flush_text_as_para()
            self._stack.append(tag)
            self._reset_buf()
        elif tag in ("ul", "ol"):
            self._flush_text_as_para()
            self._list_stack.append(tag == "ol")
            self._stack.append(tag)
        elif tag == "li":
            self._reset_buf()
            self._stack.append(tag)
        elif tag == "table":
            self._flush_text_as_para()
            self._table_rows = []
            self._stack.append(tag)
        elif tag == "tr":
            self._row = []
            self._stack.append(tag)
        elif tag in ("td", "th"):
            self._reset_buf()
            self._stack.append(tag)
        elif tag == "blockquote":
            self._flush_text_as_para()
            self._bq_paras = []
            self._stack.append(tag)
        elif tag == "hr":
            self._flush_text_as_para()
            self.nodes.append(HorizontalRule())
        elif tag == "pre":
            self._flush_text_as_para()
            self._in_pre = True
            self._reset_buf()
            self._stack.append(tag)
        elif tag == "br":
            self._append_text("\n")
        elif tag in _BLOCK_FLUSH:
            self._flush_text_as_para()
            self._stack.append(tag)
        elif tag == "a":
            href = None
            for k, v in attrs:
                if k.lower() == "href" and v and not str(v).lower().startswith("javascript:"):
                    href = v
                    break
            self._mark_stack.append((InlineMarkType.LINK, self._offset, href))
            self._stack.append(tag)
        elif tag in _MARK_TAGS and not (tag == "code" and self._in_pre):
            self._mark_stack.append((_MARK_TAGS[tag], self._offset, None))
            self._stack.append(tag)
        elif tag == "code" and self._in_pre:
            self._stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if tag == "a" or (tag in _MARK_TAGS and not (tag == "code" and self._in_pre)):
            self._close_mark(tag)
            self._pop(tag)
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            rich = self._take_rich()
            if rich.text.strip():
                self.nodes.append(Heading(level=level, text=rich))
            self._pop(tag)
        elif tag == "p":
            rich = self._take_rich()
            if rich.text.strip() or rich.marks:
                if self._in_blockquote():
                    self._bq_paras.append(rich)
                else:
                    self.nodes.append(Paragraph(text=rich))
            self._pop(tag)
        elif tag == "li":
            rich = self._take_rich()
            ordered = self._list_stack[-1] if self._list_stack else False
            level = max(0, len(self._list_stack) - 1)
            if rich.text.strip() or rich.marks:
                self.nodes.append(ListItem(text=rich, ordered=ordered, level=level))
            self._pop(tag)
        elif tag in ("ul", "ol"):
            self._pop(tag)
            if self._list_stack:
                self._list_stack.pop()
        elif tag in ("td", "th"):
            self._row.append(self._take_rich().text.strip())
            self._pop(tag)
        elif tag == "tr":
            if self._row:
                self._table_rows.append(self._row)
            self._row = []
            self._pop(tag)
        elif tag == "table":
            if self._table_rows:
                self.nodes.append(Table(rows=self._table_rows))
            self._table_rows = []
            self._pop(tag)
        elif tag == "blockquote":
            rich = self._take_rich()
            if rich.text.strip() or rich.marks:
                self._bq_paras.append(rich)
            if self._bq_paras:
                self.nodes.append(Blockquote(paragraphs=list(self._bq_paras)))
            self._bq_paras = []
            self._pop(tag)
        elif tag == "pre":
            content = "".join(self._parts).rstrip("\n")
            self.nodes.append(CodeBlock(lang=self._pre_lang, content=content))
            self._reset_buf()
            self._in_pre = False
            self._pop(tag)
        elif tag == "code":
            self._pop(tag)
        elif tag in _BLOCK_FLUSH:
            self._flush_text_as_para()
            self._pop(tag)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        if not data:
            return
        self._parts.append(data)
        self._offset += len(data)

    def _reset_buf(self) -> None:
        self._parts = []
        self._marks = []
        self._offset = 0
        self._mark_stack = []

    def _close_mark(self, tag: str) -> None:
        want = InlineMarkType.LINK if tag == "a" else _MARK_TAGS.get(tag)
        if want is None:
            return
        for i in range(len(self._mark_stack) - 1, -1, -1):
            mtype, start, href = self._mark_stack[i]
            if mtype == want:
                self._mark_stack.pop(i)
                if self._offset > start:
                    self._marks.append(
                        InlineMark(type=mtype, start=start, end=self._offset, href=href)
                    )
                return

    def _take_rich(self) -> RichText:
        while self._mark_stack:
            mtype, start, href = self._mark_stack.pop()
            if self._offset > start:
                self._marks.append(
                    InlineMark(type=mtype, start=start, end=self._offset, href=href)
                )
        text = "".join(self._parts)
        marks = sorted(self._marks, key=lambda m: (m.start, m.end))
        self._reset_buf()
        if not marks:
            return RichText(text=text.strip())
        return RichText(text=text, marks=marks)

    def _in_blockquote(self) -> bool:
        return "blockquote" in self._stack

    def _flush_text_as_para(self) -> None:
        if self._stack and self._stack[-1] in (
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "td",
            "th",
            "pre",
            "p",
            "code",
            "a",
            "strong",
            "b",
            "em",
            "i",
        ):
            return
        rich = self._take_rich()
        if not rich.text.strip() and not rich.marks:
            return
        if self._in_blockquote():
            self._bq_paras.append(rich)
        else:
            self.nodes.append(Paragraph(text=rich))

    def _pop(self, tag: str) -> None:
        if tag in self._stack:
            while self._stack:
                top = self._stack.pop()
                if top == tag:
                    break


def import_html(source_bytes: bytes, filename: str = "input.html") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidDocumentError("HTML 文件必须是有效的 UTF-8 编码") from exc

    if text.startswith("\ufeff"):
        text = text[1:]

    parser = _HTMLDocParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise InvalidDocumentError(f"HTML 解析失败: {filename} — {exc}") from exc

    if parser._parts:
        rich = parser._take_rich()
        if rich.text.strip() or rich.marks:
            parser.nodes.append(Paragraph(text=rich))

    title = ""
    for n in parser.nodes:
        if isinstance(n, Heading) and n.level == 1:
            title = n.plain_text()
            break
    if not title:
        title = filename.rsplit(".", 1)[0] if filename else ""
    return DocumentModel(title=title, nodes=parser.nodes)
