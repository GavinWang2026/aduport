"""Minimal RTF exporter with rich-text marks, multiline code, and node coverage."""

from __future__ import annotations

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMarkType,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TextBox,
    ensure_rich_text,
)
from aduport.core.title_source import title_repeats_leading_heading
from aduport.exporters.rich_visible import list_item_children


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in ("\\", "{", "}"):
            out.append("\\" + ch)
        elif code < 128:
            out.append(ch)
        else:
            if code > 32767:
                code -= 65536
            out.append(f"\\u{code}?")
    return "".join(out)


def _rtf_matrix_table(matrix: list[list[str]]) -> str:
    """\\trowd rows so the importer can rebuild a Table (not pipe text)."""
    unit = 3000
    parts: list[str] = []
    for row in matrix:
        row_def = [r"\trowd\trgaph108\trleft-108"]
        cells: list[str] = []
        pos = 0
        for cell in row:
            pos += unit
            row_def.append(rf"\cellx{pos}")
            cells.append(rf"\pard\intbl {_rtf_escape(str(cell))}\cell")
        if cells:
            parts.append("".join(row_def) + "".join(cells) + r"\row")
    return "".join(parts)


def _rtf_nl(text: str) -> str:
    """Escape text and map newlines to RTF \\line (bare newlines are ignored by RTF)."""
    parts = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return r"\line ".join(_rtf_escape(p) for p in parts)


def _render_rich(rich) -> str:
    rich = ensure_rich_text(rich)
    if not rich.marks:
        return _rtf_nl(rich.text)
    from aduport.exporters.md_exporter import _split_by_marks

    parts: list[str] = []
    for segment, active in _split_by_marks(rich):
        s = _rtf_escape(segment.replace("\n", " "))
        types = {m.type for m in active}
        if InlineMarkType.BOLD in types:
            s = rf"{{\b {s}}}"
        if InlineMarkType.ITALIC in types:
            s = rf"{{\i {s}}}"
        if InlineMarkType.CODE in types:
            s = rf"{{\f1 {s}}}"
        if InlineMarkType.STRIKETHROUGH in types:
            s = rf"{{\strike {s}}}"
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active if m.type == InlineMarkType.LINK), "") or ""
            href = href.replace("\\", "/").replace('"', "")
            s = (
                rf"{{\field{{\*\fldinst HYPERLINK "
                + '"'
                + _rtf_escape(href)
                + '"'
                + rf"}}{{\fldrslt{{\ul\cf1 {s}}}}}}}"
            )
        parts.append(s)
    return "".join(parts)


def export_rtf(model: DocumentModel) -> bytes:
    parts: list[str] = [
        r"{\rtf1\ansi\deff0",
        # Ignorable destination. Readers skip it; we use it to keep our own
        # RTF on the native importer (anydoc flattens \fs headings and \li lists).
        r"{\*\generator AduPort;}",
        r"{\fonttbl{\f0 Times New Roman;}{\f1 Courier New;}}",
        r"{\colortbl;\red0\green0\blue255;}",
        r"\f0\fs24",
    ]
    if any(isinstance(node, ListItem) and node.ordered for node in model.nodes):
        start = 1
        for node in model.nodes:
            if isinstance(node, ListItem) and node.ordered and node.number:
                start = int(node.number)
                break
        levels = "".join(
            rf"{{\listlevel\levelnfc0\leveljc0\levelfollow0\levelstartat{start}"
            rf"\levelspace0\levelindent0{{\leveltext\'02\'0{ilvl}.;}}"
            rf"{{\levelnumbers\'01;}}}}"
            for ilvl in range(9)
        )
        parts.append(rf"{{\*\listtable{{\list\listtemplateid1\listid1{levels}}}}}")
        parts.append(r"{\*\listoverridetable{\listoverride\listid1\listoverridecount0\ls1}}")
    if model.title and not title_repeats_leading_heading(model):
        parts.append(r"{\b\fs36 " + _rtf_escape(model.title) + r"}\par")

    ordered_counters: dict[int, int] = {}
    for node in model.nodes:
        if isinstance(node, Heading):
            size = max(28, 48 - 4 * max(1, min(6, node.level)))
            parts.append(
                rf"{{\fs{size}\b {_render_rich(node.text)}}}\par"
            )
        elif isinstance(node, Paragraph):
            parts.append(_render_rich(node.text) + r"\par")
        elif isinstance(node, ListItem):
            level = max(0, int(node.level or 0))
            indent = rf"\li{360 * (level + 1)}"
            if node.checked is True:
                prefix = "[x] "
            elif node.checked is False:
                prefix = "[ ] "
            elif node.ordered:
                ordered_counters[level] = (
                    int(node.number)
                    if node.number is not None
                    else ordered_counters.get(level, 0) + 1
                )
                for k in list(ordered_counters):
                    if k > level:
                        del ordered_counters[k]
                prefix = f"{{\\*\\mdn {ordered_counters[level]}}}"
                indent = rf"\ls1\ilvl{level}"
            else:
                for k in list(ordered_counters):
                    if k >= level:
                        del ordered_counters[k]
                prefix = r"\bullet "
            # A space ends the \li parameter. Without it, "\li360" + "1." becomes
            # \li3601 and the visible marker is ". OLSENT" (v19 D8).
            parts.append(rf"{{\pard{indent} {prefix}{_render_rich(node.text)}\par}}")
            # v6/T1-#3: keep multi-paragraph / fenced-code content inside the
            # list item instead of dropping it silently.
            for _is_code, text in list_item_children(node):
                if not text:
                    continue
                for line in text.splitlines() or [""]:
                    parts.append(rf"{{\pard{indent}\~{_rtf_escape(line)}\par}}")
        elif isinstance(node, Table):
            from aduport.exporters.table_span import (
                render_table_rtf,
                table_flat_matrix,
                table_has_span_cells,
            )

            if table_has_span_cells(node):
                parts.append(render_table_rtf(node))
            else:
                parts.append(_rtf_matrix_table(table_flat_matrix(node)))
        elif isinstance(node, HorizontalRule):
            parts.append(r"\par --------------------\par")
        elif isinstance(node, CodeBlock):
            body = _rtf_nl(node.content or "")
            lang = _rtf_escape(node.lang or "")
            header = rf"{{\i code:{lang}}}\par" if lang else ""
            parts.append(header + rf"{{\pard\f1 {body}\par}}")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                parts.append(
                    rf"{{\pard\li360\i {_render_rich(ensure_rich_text(p))}\par}}"
                )
        elif isinstance(node, MathBlock):
            body = _rtf_nl(node.content or "")
            parts.append(rf"{{\pard\i $$ {body} $$\par}}")
        elif isinstance(node, Image):
            alt = (node.alt_text or "image").split("|", 1)[0]
            alt = alt.replace("{", "").replace("}", "")
            if node.data:
                mime = (node.mime_type or "").lower()
                blip = r"\jpegblip" if mime in {"image/jpeg", "image/jpg"} else r"\pngblip"
                hexdata = node.data.hex()
                wrapped = "\n".join(hexdata[i : i + 64] for i in range(0, len(hexdata), 64))
                parts.append(
                    rf"{{\*\mdalt {_rtf_escape(alt)}}}{{\pict{blip} {wrapped}}}"
                )
            else:
                parts.append(_rtf_escape(f"[Image: {alt}]") + r"\par")
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                parts.append(_rtf_nl(text) + r"\par")
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            parts.append(
                _rtf_escape(f"[Comment {node.author or 'reviewer'}{anchor}] {node.text}")
                + r"\par"
            )
        elif isinstance(node, Footnote):
            fid = _rtf_escape(node.id or "1")
            body = _rtf_escape(node.text or "")
            parts.append(rf"{{\super [^{fid}]}}{{\footnote {body}}}")
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            parts.append(_rtf_escape(f"[{tag} {node.author}] {node.text}") + r"\par")
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            parts.append(_rtf_escape(f"[{label}] {node.text}") + r"\par")
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            parts.append(_rtf_escape(f"[TextBox{src}] {node.text}") + r"\par")
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            parts.append(_rtf_escape(f"[PivotTable {node.name}] {fields}") + r"\par")
        else:
            text = getattr(node, "plain_text", None)
            if callable(text):
                t = text()
                if t:
                    parts.append(_rtf_escape(str(t)) + r"\par")

    parts.append("}")
    return "\n".join(parts).encode("utf-8")
