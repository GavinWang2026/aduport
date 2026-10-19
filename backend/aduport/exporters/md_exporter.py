"""Export DocumentModel to Markdown."""

from __future__ import annotations

import base64
import re

from aduport.config import settings
from aduport.core.document_model import (
    Blockquote,
    Bookmark,
    Chart,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    FieldCode,
    Footnote,
    HeaderFooter,
    Heading,
    HorizontalRule,
    PageBreak,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Numbering,
    OleObject,
    PageSetup,
    Paragraph,
    PivotTable,
    Revision,
    RichText,
    RunStyle,
    Section,
    SmartArt,
    SpeakerNote,
    Table,
    TextBox,
    ensure_rich_text,
)
from aduport.core.title_source import is_placeholder_title, title_repeats_leading_heading
from aduport.exporters.export_warnings import node_type_name, placeholder_line, warn_export
from aduport.exporters.md_escape import collapse_blank_lines, escape_md_text
from aduport.exporters.table_span import (
    render_table_html,
    table_flat_matrix,
    table_has_cell_images,
    table_has_span_cells,
)


def _chrome_has_payload(node) -> bool:
    """False for the empty section and generator header docx import adds."""
    if isinstance(node, Section):
        return bool((node.title or "").strip())
    if isinstance(node, HeaderFooter):
        body = (node.text or "").strip()
        return bool(body) and body.casefold() not in {"AduPort", "aduport"}
    if isinstance(node, FieldCode):
        return bool((node.result_text or node.instruction or "").strip())
    if isinstance(node, Bookmark):
        return bool((node.name or node.anchor or "").strip())
    return True


def _html_comment_body(text: str) -> str:
    """HTML comments cannot contain ``--`` or ``-->`` (P3-7)."""
    return (text or "").replace("-->", "→").replace("--", "- -")


def export_md(model: DocumentModel) -> str:
    llm = bool(getattr(settings, "llm_mode", False))
    lines: list[str] = []

    if llm and getattr(settings, "llm_mode_front_matter", True):
        lines.extend(_llm_front_matter(model))

    # Skip a second H1 when the body already opens with the same heading (v16 F1).
    # Placeholder titles (Untitled / input / filename stems) stay in metadata only.
    emit_title = (
        bool(model.title)
        and not title_repeats_leading_heading(model)
        and not is_placeholder_title(model.title)
    )
    if emit_title and not (llm and getattr(settings, "llm_mode_front_matter", True)):
        lines.append(f"# {escape_md_text(model.title, at_line_start=True)}")
        lines.append("")
    elif emit_title and llm:
        # Title already in front-matter; still emit H1 for structure.
        lines.append(f"# {escape_md_text(model.title, at_line_start=True)}")
        lines.append("")

    prev_was_list = False
    ordered_nodes = _chrome_before_body(list(model.nodes))
    for note in list((model.metadata or {}).get("fidelity_warnings") or []):
        text = str(note).strip()
        if text:
            lines.append(f"<!-- {text} -->")
            lines.append("")
    skipped_chrome: set[str] = set()
    for node in ordered_nodes:
        if isinstance(node, Heading):
            if prev_was_list:
                lines.append("")
            prefix = "#" * node.level
            lines.append(
                f"{prefix} {_render_rich(ensure_rich_text(node.text), at_line_start=False)}"
            )
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Paragraph):
            if prev_was_list:
                lines.append("")
            lines.append(_render_rich(ensure_rich_text(node.text), at_line_start=True))
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Blockquote):
            if prev_was_list:
                lines.append("")
            for para in node.paragraphs:
                rendered = _render_rich(ensure_rich_text(para), at_line_start=False)
                for line in rendered.split("\n"):
                    lines.append(f"> {line}")
                lines.append(">")
            if lines and lines[-1] == ">":
                lines.pop()
            lines.append("")
            prev_was_list = False
        elif isinstance(node, PageBreak):
            if prev_was_list:
                lines.append("")
            lines.append("<!-- pagebreak -->")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, HorizontalRule):
            lines.append("---")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, ListItem):
            indent = "  " * max(0, int(node.level or 0))
            if node.checked is not None:
                box = "[x]" if node.checked else "[ ]"
                marker = f"- {box}"
            elif (node.marker_label or "").strip():
                label = node.marker_label.strip()
                marker = label if label[-1] in ".)" else f"{label}."
            elif node.ordered:
                num = node.number if node.number is not None else 1
                marker = f"{int(num)}."
            else:
                marker = "-"
            lines.append(
                f"{indent}{marker} {_render_rich(ensure_rich_text(node.text), at_line_start=False)}"
            )
            for child in node.children:
                if isinstance(child, CodeBlock):
                    fence = child.lang or ""
                    lines.append(f"{indent}  ```{fence}")
                    for line in child.content.splitlines() or [""]:
                        lines.append(f"{indent}  {line}")
                    lines.append(f"{indent}  ```")
                elif isinstance(child, Paragraph):
                    lines.append(
                        f"{indent}  {_render_rich(ensure_rich_text(child.text), at_line_start=False)}"
                    )
                else:
                    plain = getattr(child, "plain_text", None)
                    if callable(plain):
                        lines.append(f"{indent}  {plain()}")
                    elif hasattr(child, "text"):
                        lines.append(
                            f"{indent}  {_render_rich(ensure_rich_text(child.text), at_line_start=False)}"
                        )
            prev_was_list = True
        elif isinstance(node, Comment):
            anchor = f' "{node.anchor}"' if node.anchor else ""
            lines.append(
                f"<!-- comment:{node.author}{anchor} {_html_comment_body(node.text)} -->"
            )
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Revision):
            if node.change == "delete":
                lines.append(f"~~{node.text}~~ <!-- del:{node.author} -->")
            else:
                lines.append(f"++{node.text}++ <!-- ins:{node.author} -->")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            lines.append(
                f"**[{label}]** {node.text} <!-- content_control:{node.control_type} -->"
            )
            lines.append("")
            prev_was_list = False
        elif isinstance(node, PivotTable):
            lines.append(f"### Pivot: {node.name or 'table'}")
            if node.location:
                lines.append(f"- location: {node.location}")
            if node.source_range:
                lines.append(f"- source: {node.source_range}")
            if node.row_fields:
                lines.append(f"- rows: {', '.join(node.row_fields)}")
            if node.col_fields:
                lines.append(f"- cols: {', '.join(node.col_fields)}")
            if node.data_fields:
                lines.append(f"- values: {', '.join(node.data_fields)}")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Chart):
            lines.append(f"<!-- chart:{node.chart_type} -->")
            lines.append(f"**{node.plain_text()}**")
            lines.extend(_render_chart_table(node))
            lines.append("")
            prev_was_list = False
        elif isinstance(node, SpeakerNote):
            lines.append(f"> **[SpeakerNote s{node.slide_index}]** {node.text}")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, SmartArt):
            lines.append(f"**{node.plain_text()}**")
            for it in node.items[:50]:
                lines.append(f"- {it}")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, OleObject):
            lines.append(f"*{node.plain_text()}*")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            lines.append(f"> **[文本框{src}]** {node.text}")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Table):
            if prev_was_list:
                lines.append("")
            lines.extend(_render_table(node))
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Image):
            if prev_was_list:
                lines.append("")
            lines.append(_render_image(node, llm=llm))
            lines.append("")
            prev_was_list = False
        elif isinstance(node, MathBlock):
            if prev_was_list:
                lines.append("")
            if node.display:
                lines.append("$$")
                lines.append(node.content)
                lines.append("$$")
            else:
                lines.append(f"${node.content}$")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, Footnote):
            lines.append(f"[^{node.id}]: {node.text}")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, HtmlBlock):
            lines.append(node.content)
            lines.append("")
            prev_was_list = False
        elif isinstance(node, CodeBlock):
            if prev_was_list:
                lines.append("")
            lang = node.lang or ""
            lines.append(f"```{lang}")
            lines.append(node.content)
            lines.append("```")
            lines.append("")
            prev_was_list = False
        elif isinstance(node, (PageSetup, RunStyle, Numbering)):
            # Style catalogs are huge. One warning, no per-entry comment.
            kind = node_type_name(node)
            if kind not in skipped_chrome:
                skipped_chrome.add(kind)
                warn_export(
                    model,
                    f"md: {kind} not expressed (no Markdown equivalent)",
                )
            continue
        elif isinstance(node, (Section, HeaderFooter, FieldCode, Bookmark)):
            # v19 D9: these have no Markdown object, but real text must survive
            # as a comment. Stock section/header shells (empty title, the
            # generator name) are not document content.
            if not _chrome_has_payload(node):
                continue
            kind = node_type_name(node)
            if kind not in skipped_chrome:
                skipped_chrome.add(kind)
                warn_export(
                    model,
                    f"md: {kind} kept as an HTML comment (no Markdown equivalent)",
                )
            detail = node.plain_text() if hasattr(node, "plain_text") else ""
            lines.append(
                f"<!-- {_html_comment_body(placeholder_line(kind, str(detail)[:200]))} -->"
            )
            lines.append("")
            prev_was_list = False
        else:
            kind = node_type_name(node)
            plain = getattr(node, "plain_text", None)
            detail = plain() if callable(plain) else kind
            warn_export(model, f"md: unhandled node {kind} → placeholder")
            lines.append(f"<!-- {placeholder_line(kind, str(detail)[:200])} -->")
            lines.append("")
            prev_was_list = False

    text = "\n".join(lines).rstrip() + "\n"
    if llm:
        text = _normalize_llm_whitespace(text)
    else:
        text = collapse_blank_lines(text)
    return text


def _llm_front_matter(model: DocumentModel) -> list[str]:
    meta = model.metadata or {}
    title = (model.title or "").replace('"', "'")
    importer = str(meta.get("importer") or "")
    lines = ["---", f'title: "{title}"']
    if importer:
        lines.append(f'importer: "{importer}"')
    lines.append('mode: "llm"')
    lines.append("---")
    lines.append("")
    return lines


def _normalize_llm_whitespace(text: str) -> str:
    text = collapse_blank_lines(text)
    # Trim trailing spaces on each line; keep fenced code intact by line-wise trim only.
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
            out.append(line.rstrip())
            continue
        out.append(line if in_fence else line.rstrip())
    return "\n".join(out).rstrip() + "\n"


def _render_image(node: Image, *, llm: bool = False) -> str:
    alt = node.alt_text.split("|", 1)[0] if "|" in node.alt_text else node.alt_text
    if llm and getattr(settings, "llm_mode_strip_images", True):
        # Drop bulky base64; keep a stable placeholder for RAG/Agent consumers.
        if "|" in node.alt_text:
            _a, src = node.alt_text.split("|", 1)
            return f"![{alt}]({src})"
        kind = (node.mime_type or "image").split("/")[-1]
        return f"![{alt or 'image'}](omitted:{kind})"
    if node.data:
        b64 = base64.b64encode(node.data).decode("ascii")
        return f"![{alt}](data:{node.mime_type};base64,{b64})"
    # Placeholder for remote / relative images: alt may encode "alt|url"
    if "|" in node.alt_text:
        a, src = node.alt_text.split("|", 1)
        return f"![{a}]({src})"
    return f"![{node.alt_text}]()"


# Outer → inner open order. Close in reverse. Keeps nested MD like
# **粗体里套*斜体*结束** instead of segment-wise **a****_b_****c**.
_MARK_OUTER_RANK = {
    InlineMarkType.LINK: 0,
    InlineMarkType.STRIKETHROUGH: 1,
    InlineMarkType.BOLD: 2,
    InlineMarkType.ITALIC: 3,
    InlineMarkType.CODE: 4,
}


_FN_LINK_TEXT = re.compile(r"^\[\^[^\]]+\]$")


def _footnote_link(mark: InlineMark, text: str) -> bool:
    span = text[max(0, mark.start) : max(0, mark.end)]
    href = mark.href or ""
    return bool(_FN_LINK_TEXT.match(span)) or href.startswith("#fn-")


def _render_rich(rich: RichText, *, at_line_start: bool = False) -> str:
    """Serialize RichText to Markdown with true nested delimiters (D-01 / V-A)."""
    if not rich.marks:
        return escape_md_text(rich.text, at_line_start=at_line_start)

    text = rich.text
    n = len(text)
    opens_at: dict[int, list[InlineMark]] = {}
    closes_at: dict[int, list[InlineMark]] = {}
    for mark in rich.marks:
        start = max(0, min(mark.start, n))
        end = max(0, min(mark.end, n))
        if start >= end:
            continue
        opens_at.setdefault(start, []).append(mark)
        closes_at.setdefault(end, []).append(mark)

    for pos, marks in opens_at.items():
        marks.sort(key=lambda m: (_MARK_OUTER_RANK.get(m.type, 99), -(m.end - m.start)))
    for pos, marks in closes_at.items():
        marks.sort(key=lambda m: (-_MARK_OUTER_RANK.get(m.type, 99), m.end - m.start))

    parts: list[str] = []
    stack: list[tuple[InlineMark, str]] = []
    line_start = at_line_start
    in_code = 0

    def _open(mark: InlineMark) -> None:
        nonlocal in_code
        if mark.type == InlineMarkType.LINK:
            if _footnote_link(mark, text):
                stack.append((mark, ""))
                return
            parts.append("[")
            stack.append((mark, f"]({mark.href or ''})"))
            return
        if mark.type == InlineMarkType.BOLD:
            parts.append("**")
            stack.append((mark, "**"))
            return
        if mark.type == InlineMarkType.ITALIC:
            bold_open = next((m for m, _ in stack if m.type == InlineMarkType.BOLD), None)
            if (
                bold_open is not None
                and bold_open.start == mark.start
                and bold_open.end == mark.end
            ):
                delim = "_"
            else:
                delim = "*"
            parts.append(delim)
            stack.append((mark, delim))
            return
        if mark.type == InlineMarkType.CODE:
            parts.append("`")
            stack.append((mark, "`"))
            in_code += 1
            return
        if mark.type == InlineMarkType.STRIKETHROUGH:
            parts.append("~~")
            stack.append((mark, "~~"))
            return

    def _close(mark: InlineMark) -> None:
        nonlocal in_code
        pending: list[tuple[InlineMark, str]] = []
        while stack:
            top, tok = stack.pop()
            parts.append(tok)
            if top.type == InlineMarkType.CODE:
                in_code = max(0, in_code - 1)
            if top is mark:
                break
            pending.append((top, tok))
        for m, _tok in reversed(pending):
            _open(m)

    buf: list[str] = []

    def _flush_buf() -> None:
        nonlocal line_start
        if not buf:
            return
        chunk = "".join(buf)
        buf.clear()
        parts.append(
            escape_md_text(chunk, at_line_start=line_start, in_code=in_code > 0)
        )
        line_start = chunk.endswith("\n")

    for i, ch in enumerate(text):
        for mark in closes_at.get(i, ()):
            _flush_buf()
            _close(mark)
        for mark in opens_at.get(i, ()):
            _flush_buf()
            _open(mark)
        buf.append(ch)
    _flush_buf()
    for mark in closes_at.get(n, ()):
        _close(mark)
    while stack:
        top, tok = stack.pop()
        parts.append(tok)
        if top.type == InlineMarkType.CODE:
            in_code = max(0, in_code - 1)
    return "".join(parts)


def _split_by_marks(rich: RichText) -> list[tuple[str, list[InlineMark]]]:
    """Segment split kept for callers/tests; nested export uses event walk above."""
    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    segments: list[tuple[str, list[InlineMark]]] = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        segments.append((rich.text[start:end], active))
    return segments


def _chrome_before_body(nodes: list) -> list:
    """TOC fields and page chrome are appended by enrich; show them first."""

    def lead(node) -> bool:
        if isinstance(node, (HeaderFooter, PageSetup)):
            return True
        return isinstance(node, FieldCode) and (node.field_type or "").upper().startswith("TOC")

    return [node for node in nodes if lead(node)] + [node for node in nodes if not lead(node)]


def _chart_cell(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    elif value is None:
        text = ""
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _render_chart_table(node: Chart) -> list[str]:
    """Write category × series values so chart data survives as a pipe table."""
    series = [s for s in (node.series or []) if isinstance(s, dict)]
    cats = [str(c) for c in (node.categories or [])]
    lines: list[str] = []
    axes = []
    if node.x_axis_title:
        axes.append(f"x: {node.x_axis_title}")
    if node.y_axis_title:
        axes.append(f"y: {node.y_axis_title}")
    if axes:
        lines.append("- " + "; ".join(axes))
    if not series:
        if cats:
            lines.append(f"- categories: {', '.join(cats[:20])}")
        return lines
    names = [str(s.get("name") or f"S{i + 1}") for i, s in enumerate(series)]
    corner = node.x_axis_title or ""
    header = [corner, *names]
    lines.append("| " + " | ".join(_chart_cell(c) for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    lengths = [len(s.get("values") or []) for s in series]
    n = max([len(cats), *lengths] or [0])
    for i in range(min(n, 40)):
        cat = cats[i] if i < len(cats) else str(i + 1)
        vals = []
        for s in series:
            values = s.get("values") or []
            vals.append(values[i] if i < len(values) else "")
        lines.append("| " + " | ".join(_chart_cell(x) for x in [cat, *vals]) + " |")
    return lines


def _render_table(table: Table) -> list[str]:
    # v9 P2: span → HTML when html_table_for_span (default True); else flat GFM.
    # Cell-embedded images also prefer HTML so <img> survives inside <td>.
    use_html = getattr(settings, "html_table_for_span", True) and (
        table_has_span_cells(table) or table_has_cell_images(table)
    )
    if use_html:
        html_table = render_table_html(table)
        return ["", html_table, ""] if html_table else []

    matrix = table_flat_matrix(table)
    if not matrix:
        return []

    col_count = max((len(row) for row in matrix), default=0)
    if col_count == 0:
        return []

    widths = [3] * col_count
    for row in matrix:
        for i, cell in enumerate(row[:col_count]):
            widths[i] = max(widths[i], len(cell), 3)

    def fmt_row(cells: list[str]) -> str:
        padded = [cells[i].ljust(widths[i]) if i < len(cells) else "".ljust(widths[i]) for i in range(col_count)]
        return "| " + " | ".join(padded) + " |"

    separator = "| " + " | ".join("-" * widths[i] for i in range(col_count)) + " |"
    header_end = _table_md_header_rows(table, len(matrix))
    lines: list[str] = []
    sheet = (table.name or "").strip()
    if sheet:
        lines.append(f"<!-- sheet: {sheet} -->")

    if len(matrix) == 1:
        lines.append(fmt_row(matrix[0]))
        return lines

    for i in range(header_end):
        lines.append(fmt_row(matrix[i]))
    lines.append(separator)
    for row in matrix[header_end:]:
        lines.append(fmt_row(row))
    return lines


def _table_md_header_rows(table: Table, row_count: int) -> int:
    if table.header_rows > 0:
        return min(int(table.header_rows), row_count)
    return 1 if settings.table_first_row_header and row_count > 1 else 0
