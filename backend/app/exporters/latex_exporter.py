"""Export DocumentModel to a minimal LaTeX article."""

from __future__ import annotations

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    Image,
    InlineMarkType,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Table,
    ensure_rich_text,
)


def export_latex(model: DocumentModel) -> str:
    parts: list[str] = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{graphicx}",
        r"\usepackage{hyperref}",
        r"\usepackage{amsmath}",
        r"\begin{document}",
    ]
    if model.title:
        parts.append(rf"\title{{{_esc(model.title)}}}")
        parts.append(r"\maketitle")

    i = 0
    nodes = model.nodes
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, Heading):
            cmd = {1: "section", 2: "subsection", 3: "subsubsection"}.get(node.level, "paragraph")
            parts.append(rf"\{cmd}{{{_esc(node.plain_text())}}}")
            i += 1
        elif isinstance(node, Paragraph):
            parts.append(_render_rich(ensure_rich_text(node.text)) + "\n")
            i += 1
        elif isinstance(node, ListItem):
            ordered = node.ordered
            env = "enumerate" if ordered else "itemize"
            parts.append(rf"\begin{{{env}}}")
            while i < len(nodes) and isinstance(nodes[i], ListItem) and nodes[i].ordered == ordered:
                li = nodes[i]
                box = ""
                if li.checked is True:
                    box = r"[$\boxtimes$] "
                elif li.checked is False:
                    box = r"[$\square$] "
                parts.append(rf"\item {box}{_render_rich(ensure_rich_text(li.text))}")
                i += 1
            parts.append(rf"\end{{{env}}}")
        elif isinstance(node, Table):
            parts.append(_render_table(node))
            i += 1
        elif isinstance(node, CodeBlock):
            parts.append(r"\begin{verbatim}")
            parts.append(node.content)
            parts.append(r"\end{verbatim}")
            i += 1
        elif isinstance(node, MathBlock):
            if node.display:
                parts.append(r"\[")
                parts.append(node.content)
                parts.append(r"\]")
            else:
                parts.append(rf"\({node.content}\)")
            i += 1
        elif isinstance(node, Blockquote):
            parts.append(r"\begin{quote}")
            for p in node.paragraphs:
                parts.append(_render_rich(ensure_rich_text(p)))
            parts.append(r"\end{quote}")
            i += 1
        elif isinstance(node, HorizontalRule):
            parts.append(r"\hrulefill")
            i += 1
        elif isinstance(node, Footnote):
            parts.append(rf"\footnote{{{_esc(node.text)}}}")  # id lost; text kept
            i += 1
        elif isinstance(node, Comment):
            anchor = f" on {_esc(node.anchor)}" if node.anchor else ""
            parts.append(
                rf"% comment by {_esc(node.author or 'reviewer')}{anchor}: {_esc(node.text)}"
            )
            i += 1
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            parts.append(
                rf"% pivot {_esc(node.name or 'table')}: {_esc(fields)}"
                + (rf" src={_esc(node.source_range)}" if node.source_range else "")
            )
            i += 1
        elif isinstance(node, Image):
            alt = _esc(node.alt_text.split("|", 1)[0] if node.alt_text else "image")
            parts.append(rf"% image: {alt}")
            i += 1
        else:
            i += 1

    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


def _esc(text: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = []
    for ch in text:
        out.append(repl.get(ch, ch))
    return "".join(out)


def _render_rich(rich) -> str:
    if not rich.marks:
        return _esc(rich.text)
    from app.exporters.md_exporter import _split_by_marks

    parts = []
    for segment, active in _split_by_marks(rich):
        s = _esc(segment)
        types = {m.type for m in active}
        if InlineMarkType.CODE in types:
            s = rf"\texttt{{{s}}}"
        if InlineMarkType.BOLD in types:
            s = rf"\textbf{{{s}}}"
        if InlineMarkType.ITALIC in types:
            s = rf"\textit{{{s}}}"
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active if m.type == InlineMarkType.LINK), "") or ""
            href = href.replace("\\", "/").replace("%", r"\%")
            s = rf"\href{{{href}}}{{{s}}}"
        if InlineMarkType.STRIKETHROUGH in types:
            s = rf"\sout{{{s}}}"
        parts.append(s)
    return "".join(parts)


def _render_table(table: Table) -> str:
    from app.exporters.table_span import (
        render_table_html,
        render_table_latex,
        table_flat_matrix,
        table_has_span_cells,
    )

    if not table.rows and not table.cells:
        return ""
    if table_has_span_cells(table):
        return render_table_latex(table)
    matrix = table_flat_matrix(table)
    if not matrix:
        return ""
    cols = max(len(r) for r in matrix)
    spec = "l" * cols
    lines: list[str] = []
    lines.append(rf"\begin{{tabular}}{{{spec}}}")
    lines.append(r"\hline")
    for r_idx, row in enumerate(matrix):
        cells = [_esc(row[c] if c < len(row) else "") for c in range(cols)]
        lines.append(" & ".join(cells) + r" \\")
        if r_idx == 0:
            lines.append(r"\hline")
    lines.append(r"\hline")
    lines.append(r"\end{tabular}")
    return "\n".join(lines)
