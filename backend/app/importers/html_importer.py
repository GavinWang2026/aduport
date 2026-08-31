"""HTML importer with nested-context stack (stdlib html.parser)."""

from __future__ import annotations

from html.parser import HTMLParser

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Heading,
    HorizontalRule,
    ListItem,
    Paragraph,
    RichText,
    Table,
)
from app.core.exceptions import InvalidDocumentError

_SKIP_TAGS = frozenset({"script", "style", "noscript", "iframe", "svg", "template"})
_BLOCK_FLUSH = frozenset({"p", "div", "section", "article", "main", "header", "footer", "aside"})


class _HTMLDocParser(HTMLParser):
    """Stack-based HTML → DocumentModel; tolerates nested div/section/blockquote."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list = []
        self._buf: list[str] = []
        # Context stack entries: tag name
        self._stack: list[str] = []
        self._list_stack: list[bool] = []  # ordered?
        self._table_rows: list[list[str]] = []
        self._row: list[str] = []
        self._bq_paras: list[str] = []
        self._in_pre = False
        self._pre_lang = ""
        self._skip_depth = 0
        self._list_level = 0

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
            self._buf = []
        elif tag == "p":
            self._flush_text_as_para()
            self._stack.append(tag)
            self._buf = []
        elif tag in ("ul", "ol"):
            self._flush_text_as_para()
            self._list_stack.append(tag == "ol")
            self._list_level = len(self._list_stack) - 1
            self._stack.append(tag)
        elif tag == "li":
            self._buf = []
            self._stack.append(tag)
        elif tag == "table":
            self._flush_text_as_para()
            self._table_rows = []
            self._stack.append(tag)
        elif tag == "tr":
            self._row = []
            self._stack.append(tag)
        elif tag in ("td", "th"):
            self._buf = []
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
            self._buf = []
            self._stack.append(tag)
        elif tag == "br":
            self._buf.append("\n")
        elif tag in _BLOCK_FLUSH:
            self._flush_text_as_para()
            self._stack.append(tag)
        elif tag == "code" and not self._in_pre:
            self._stack.append(tag)
        else:
            # ignore unknown inline/block but keep stack optional
            pass

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = "".join(self._buf).strip()
            if text:
                self.nodes.append(Heading(level=level, text=RichText(text=text)))
            self._buf = []
            self._pop(tag)
        elif tag == "p":
            text = "".join(self._buf).strip()
            if text:
                if self._in_blockquote():
                    self._bq_paras.append(text)
                else:
                    self.nodes.append(Paragraph(text=RichText(text=text)))
            self._buf = []
            self._pop(tag)
        elif tag == "li":
            text = "".join(self._buf).strip()
            ordered = self._list_stack[-1] if self._list_stack else False
            level = max(0, len(self._list_stack) - 1)
            if text:
                self.nodes.append(ListItem(text=text, ordered=ordered, level=level))
            self._buf = []
            self._pop(tag)
        elif tag in ("ul", "ol"):
            self._pop(tag)
            if self._list_stack:
                self._list_stack.pop()
            self._list_level = max(0, len(self._list_stack) - 1)
        elif tag in ("td", "th"):
            self._row.append("".join(self._buf).strip())
            self._buf = []
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
            # flush any remaining buffer into quote
            text = "".join(self._buf).strip()
            if text:
                self._bq_paras.append(text)
            self._buf = []
            if self._bq_paras:
                self.nodes.append(
                    Blockquote(paragraphs=[RichText(text=p) for p in self._bq_paras])
                )
            self._bq_paras = []
            self._pop(tag)
        elif tag == "pre":
            content = "".join(self._buf).rstrip("\n")
            self.nodes.append(CodeBlock(lang=self._pre_lang, content=content))
            self._buf = []
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
        self._buf.append(data)

    def _in_blockquote(self) -> bool:
        return "blockquote" in self._stack

    def _flush_text_as_para(self) -> None:
        """Emit buffered text as paragraph only when not inside a structured container."""
        text = "".join(self._buf).strip()
        if not text:
            self._buf = []
            return
        # Inside heading/li/td/pre/p — don't auto-flush as free paragraph
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
        ):
            return
        if self._in_blockquote():
            self._bq_paras.append(text)
        else:
            self.nodes.append(Paragraph(text=RichText(text=text)))
        self._buf = []

    def _pop(self, tag: str) -> None:
        # Pop until matching tag (handles mismatched nesting gracefully)
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

    # Strip BOM
    if text.startswith("\ufeff"):
        text = text[1:]

    parser = _HTMLDocParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise InvalidDocumentError(f"HTML 解析失败: {filename} — {exc}") from exc

    trailing = "".join(parser._buf).strip()
    if trailing:
        parser.nodes.append(Paragraph(text=RichText(text=trailing)))

    title = ""
    # Prefer first H1 as title if filename is generic
    for n in parser.nodes:
        if isinstance(n, Heading) and n.level == 1:
            title = n.plain_text()
            break
    if not title:
        title = filename.rsplit(".", 1)[0] if filename else ""
    return DocumentModel(title=title, nodes=parser.nodes)
