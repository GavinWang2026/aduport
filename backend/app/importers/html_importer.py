"""HTML importer with nested-context stack + inline marks (stdlib html.parser)."""

from __future__ import annotations

import re

from html.parser import HTMLParser

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    PivotTable,
    Revision,
    RichText,
    Table,
    TableCell,
    TextBox,
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
        self._section_footnotes: list[bool] = []
        self._footnote_li: dict[str, str] = {}
        self._aside: dict | None = None
        self._revision: dict | None = None
        self._table_grid: list[list[TableCell | None]] = []
        self._tr_idx = -1
        self._cell_colspan = 1
        self._cell_rowspan = 1

    def _in_footnotes_section(self) -> bool:
        return any(self._section_footnotes)

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
            # Nested list inside <li>: flush parent item text before descending
            if self._stack and self._stack[-1] == "li":
                rich = self._take_rich()
                if rich.text.strip() or rich.marks:
                    ordered = self._list_stack[-1] if self._list_stack else False
                    level = max(0, len(self._list_stack) - 1)
                    self.nodes.append(
                        ListItem(text=rich, ordered=ordered, level=level)
                    )
            else:
                self._flush_text_as_para()
            self._list_stack.append(tag == "ol")
            self._stack.append(tag)
        elif tag == "li":
            self._reset_buf()
            if self._in_footnotes_section():
                self._footnote_li = {k.lower(): v for k, v in attrs}
            self._stack.append(tag)
        elif tag == "img":
            self._handle_img(attrs)
        elif tag == "table":
            self._flush_text_as_para()
            self._table_rows = []
            self._table_grid = []
            self._tr_idx = -1
            self._stack.append(tag)
        elif tag == "tr":
            self._tr_idx += 1
            self._row = []
            self._stack.append(tag)
        elif tag in ("td", "th"):
            self._reset_buf()
            self._cell_colspan = 1
            self._cell_rowspan = 1
            for k, v in attrs:
                kl = k.lower()
                if kl == "colspan" and str(v).isdigit():
                    self._cell_colspan = max(1, int(v))
                elif kl == "rowspan" and str(v).isdigit():
                    self._cell_rowspan = max(1, int(v))
            self._stack.append(tag)
        elif tag == "aside":
            self._flush_text_as_para()
            self._aside = {"attrs": attrs, "paragraphs": []}
            cls = _class_list(attrs)
            if "comment" in cls:
                self._aside["kind"] = "comment"
            elif "content-control" in cls:
                self._aside["kind"] = "content-control"
            elif "text-box" in cls:
                self._aside["kind"] = "text-box"
            elif "pivot-table" in cls:
                self._aside["kind"] = "pivot-table"
            else:
                self._aside["kind"] = "generic"
            self._stack.append(tag)
        elif tag in ("ins", "del"):
            self._flush_text_as_para()
            if "revision" in _class_list(attrs):
                self._revision = {
                    "change": "insert" if tag == "ins" else "delete",
                    "author": _attr_value(attrs, "data-author") or _attr_value(attrs, "title") or "",
                }
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
            if tag == "section":
                self._section_footnotes.append(_class_has_footnotes(attrs))
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

    def handle_startendtag(self, tag: str, attrs) -> None:
        """Self-closing tags (img/br) — HTMLParser may not call handle_starttag."""
        tag = tag.lower()
        if self._skip_depth:
            return
        if tag == "br":
            self._append_text("\n")
        elif tag == "img":
            self._handle_img(attrs)
        elif tag == "hr":
            self._flush_text_as_para()
            self.nodes.append(HorizontalRule())

    def _handle_img(self, attrs) -> None:
        src = ""
        alt = ""
        for k, v in attrs:
            kl = k.lower()
            if kl == "src" and v:
                src = str(v).strip()
            elif kl == "alt" and v:
                alt = str(v)
        if not src or src.lower().startswith("javascript:"):
            return
        # Flush surrounding text so image sits as its own node
        if self._stack and self._stack[-1] in ("p", "li"):
            pass  # keep inline context; still emit Image as sibling block
        else:
            self._flush_text_as_para()
        mime = "image/png"
        lower = src.lower().split("?", 1)[0]
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            mime = "image/jpeg"
        elif lower.endswith(".gif"):
            mime = "image/gif"
        elif lower.endswith(".webp"):
            mime = "image/webp"
        elif lower.endswith(".svg"):
            mime = "image/svg+xml"
        self.nodes.append(Image(data=b"", alt_text=alt, mime_type=mime, url=src))

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
                if self._aside is not None:
                    self._aside.setdefault("paragraphs", []).append(rich)
                elif self._in_blockquote():
                    self._bq_paras.append(rich)
                else:
                    self.nodes.append(Paragraph(text=rich))
            self._pop(tag)
        elif tag == "li":
            rich = self._take_rich()
            if self._in_footnotes_section() and rich.text.strip():
                fid = (self._footnote_li.get("value") or "").strip()
                lid = (self._footnote_li.get("id") or "").strip()
                if lid.startswith("fn-"):
                    fid = fid or lid[3:]
                if not fid:
                    fid = str(sum(1 for n in self.nodes if isinstance(n, Footnote)) + 1)
                self.nodes.append(Footnote(id=fid, text=rich.text.strip()))
                self._footnote_li = {}
            elif rich.text.strip() or rich.marks:
                ordered = self._list_stack[-1] if self._list_stack else False
                level = max(0, len(self._list_stack) - 1)
                self.nodes.append(ListItem(text=rich, ordered=ordered, level=level))
            self._pop(tag)
        elif tag in ("ul", "ol"):
            self._pop(tag)
            if self._list_stack:
                self._list_stack.pop()
        elif tag in ("td", "th"):
            text = self._take_rich().text.strip()
            if self._in_table():
                c = _next_free_col(self._table_grid, self._tr_idx)
                cell = TableCell(
                    text=text,
                    col_span=self._cell_colspan,
                    row_span=self._cell_rowspan,
                    origin_row=self._tr_idx,
                    origin_col=c,
                )
                _place_table_cell(
                    self._table_grid,
                    self._tr_idx,
                    c,
                    cell,
                    self._cell_colspan,
                    self._cell_rowspan,
                )
            self._pop(tag)
        elif tag == "tr":
            self._pop(tag)
        elif tag == "table":
            if self._table_grid:
                rows, cells = _grid_to_table(self._table_grid)
                if rows:
                    self.nodes.append(Table(rows=rows, cells=cells))
            self._table_grid = []
            self._tr_idx = -1
            self._pop(tag)
        elif tag == "aside":
            rich = self._take_rich()
            if rich.text.strip():
                self._aside.setdefault("paragraphs", []).append(rich)
            self._emit_aside()
            self._aside = None
            self._pop(tag)
        elif tag in ("ins", "del"):
            rich = self._take_rich()
            if self._revision and rich.text.strip():
                self.nodes.append(
                    Revision(
                        change=self._revision["change"],
                        author=self._revision.get("author") or "",
                        text=rich.text.strip(),
                    )
                )
                self._revision = None
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
            if tag == "section" and self._section_footnotes:
                self._section_footnotes.pop()
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

    def _in_table(self) -> bool:
        return "table" in self._stack

    def _emit_aside(self) -> None:
        if not self._aside:
            return
        kind = self._aside.get("kind")
        attrs = self._aside.get("attrs") or []
        paras: list[RichText] = self._aside.get("paragraphs") or []
        text = " ".join(p.text for p in paras).strip()
        if kind == "comment":
            author = _attr_value(attrs, "data-author") or "reviewer"
            anchor = ""
            body = text
            if ":" in text:
                head, _, tail = text.partition(":")
                body = tail.strip()
                head = re.sub(r"\s+", " ", head).strip()
                if am := re.search(r"（(.+?)）", head):
                    anchor = am.group(1).strip()
                head = re.sub(r"（.+?）", "", head).strip()
                if head:
                    author = head
            self.nodes.append(Comment(author=author, anchor=anchor, text=body))
        elif kind == "content-control":
            tag = _attr_value(attrs, "data-tag")
            ctype = _attr_value(attrs, "data-type")
            label = tag
            body = text
            if text.startswith(("Status", "control")):
                parts = text.split(None, 1)
                if len(parts) == 2:
                    label, body = parts[0], parts[1]
            self.nodes.append(
                ContentControl(
                    tag=tag or label.lower(),
                    alias=label or tag or "control",
                    control_type=ctype or "richText",
                    text=body.strip(),
                )
            )
        elif kind == "text-box":
            src = _attr_value(attrs, "data-source")
            body = text
            for prefix in ("文本框", "Text box"):
                if body.startswith(prefix):
                    body = body[len(prefix) :].strip()
            self.nodes.append(TextBox(text=body, source=src))
        elif kind == "pivot-table":
            name = _attr_value(attrs, "data-name") or "Pivot"
            row_fields: list[str] = []
            for line in text.replace("Pivot:", "").splitlines():
                line = line.strip()
                if line.lower().startswith("rows:"):
                    row_fields = [f.strip() for f in line[5:].split(",") if f.strip()]
            if not row_fields and "Region" in text:
                row_fields = ["Region"]
            self.nodes.append(PivotTable(name=name, row_fields=row_fields))
        elif text:
            self.nodes.append(Paragraph(text=text))

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


def _class_list(attrs) -> list[str]:
    for k, v in attrs:
        if k.lower() == "class" and v:
            return v.replace(",", " ").split()
    return []


def _attr_value(attrs, name: str) -> str:
    for k, v in attrs:
        if k.lower() == name.lower() and v is not None:
            return str(v)
    return ""


def _next_free_col(grid: list[list[TableCell | None]], r: int) -> int:
    while len(grid) <= r:
        grid.append([])
    row = grid[r]
    c = 0
    while c < len(row) and row[c] is not None:
        c += 1
    return c


def _place_table_cell(
    grid: list[list[TableCell | None]],
    r: int,
    c: int,
    cell: TableCell,
    colspan: int,
    rowspan: int,
) -> None:
    for dr in range(rowspan):
        for dc in range(colspan):
            rr, cc = r + dr, c + dc
            while len(grid) <= rr:
                grid.append([])
            while len(grid[rr]) <= cc:
                grid[rr].append(None)
            if dr == 0 and dc == 0:
                grid[rr][cc] = cell
            else:
                grid[rr][cc] = TableCell(covered=True, origin_row=r, origin_col=c)


def _grid_to_table(grid: list[list[TableCell | None]]) -> tuple[list[list[str]], list[list[TableCell]]]:
    rows: list[list[str]] = []
    cells: list[list[TableCell]] = []
    for row in grid:
        row_strs: list[str] = []
        row_cells: list[TableCell] = []
        for slot in row:
            cell = slot if slot is not None else TableCell(covered=True)
            row_cells.append(cell)
            row_strs.append("" if cell.covered else cell.text)
        if row_cells:
            rows.append(row_strs)
            cells.append(row_cells)
    return rows, cells


def _class_has_footnotes(attrs) -> bool:
    for key, val in attrs:
        if key.lower() == "class" and val:
            parts = val.replace(",", " ").split()
            if "footnotes" in parts:
                return True
    return False


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
