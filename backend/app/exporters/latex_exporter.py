"""Export DocumentModel to a minimal LaTeX article."""

from __future__ import annotations

import re

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
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
from app.exporters.export_warnings import node_type_name, placeholder_line, warn_export
from app.exporters.rich_visible import list_item_children

# Math/code paths inject raw TeX; neutralize shell-out / file-read primitives.
_DANGEROUS_TEX_RE = re.compile(
    r"\\(?:write(?:18)?|input|include|includeonly|openout|read|catcode|"
    r"immediate|special|csname|endcsname|@@input|verbatiminput|lstinputlisting)\b",
    re.IGNORECASE,
)


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


def _sanitize_math(tex: str) -> str:
    """Keep math mostly intact but strip primitives that can escape the math mode."""
    return _DANGEROUS_TEX_RE.sub(r"\\texttt{[blocked]}", tex or "")


def _sanitize_verbatim(content: str) -> str:
    """Prevent early verbatim termination; neutralize dangerous primitives even inside verbatim."""
    s = content or ""
    s = s.replace(r"\end{verbatim}", "[end-verbatim]").replace(r"\begin{verbatim}", "[begin-verbatim]")
    return _DANGEROUS_TEX_RE.sub("[blocked]", s)


def _safe_href(href: str) -> str:
    h = (href or "").strip()
    low = h.lower()
    if low.startswith(("javascript:", "data:", "vbscript:", "file:")):
        return "#"
    return h.replace("\\", "/").replace("%", r"\%").replace("{", "").replace("}", "")


def _plain(text: str) -> str:
    """Comment line for greppable sentinels — strip TeX primitives so comments stay inert."""
    safe = _sanitize_verbatim(text or "")
    safe = safe.replace("\n", " ").replace("%", " pct ")[:400]
    return f"% mdbridge-plain: {safe}"


def export_latex(model: DocumentModel) -> str:
    parts: list[str] = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{graphicx}",
        r"\usepackage{hyperref}",
        r"\usepackage{amsmath}",
        r"\usepackage[normalem]{ulem}",
        r"\begin{document}",
    ]
    if model.title:
        parts.append(_plain(model.title))
        parts.append(rf"\title{{{_esc(model.title)}}}")
        parts.append(r"\maketitle")

    i = 0
    nodes = model.nodes
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, Heading):
            parts.append(_plain(node.plain_text()))
            cmd = {1: "section", 2: "subsection", 3: "subsubsection"}.get(node.level, "paragraph")
            parts.append(rf"\{cmd}{{{_esc(node.plain_text())}}}")
            i += 1
        elif isinstance(node, Paragraph):
            rt = ensure_rich_text(node.text)
            parts.append(_plain(rt.text))
            parts.append(_render_rich(rt) + "\n")
            i += 1
        elif isinstance(node, ListItem):
            ordered = node.ordered
            env = "enumerate" if ordered else "itemize"
            parts.append(rf"\begin{{{env}}}")
            while i < len(nodes) and isinstance(nodes[i], ListItem) and nodes[i].ordered == ordered:
                li = nodes[i]
                parts.append(_plain(ensure_rich_text(li.text).text))
                box = ""
                if li.checked is True:
                    box = "[x] "
                elif li.checked is False:
                    box = "[ ] "
                parts.append(rf"\item {box}{_render_rich(ensure_rich_text(li.text))}")
                # v6/T1-#3: keep multi-paragraph / fenced-code content that
                # lives inside the list item instead of dropping it silently.
                for is_code, text in list_item_children(li):
                    if not text:
                        continue
                    if is_code:
                        parts.append(_plain(text))
                        parts.append(r"\begin{verbatim}")
                        parts.append(text)
                        parts.append(r"\end{verbatim}")
                    else:
                        parts.append(_plain(text))
                        parts.append(_render_rich(ensure_rich_text(text)) + "\n")
                i += 1
            parts.append(rf"\end{{{env}}}")
        elif isinstance(node, Table):
            if node.rows:
                for row in node.rows:
                    for cell in row:
                        parts.append(_plain(str(cell)))
            parts.append(_render_table(node))
            i += 1
        elif isinstance(node, CodeBlock):
            parts.append(_plain(node.content))
            parts.append(r"\begin{verbatim}")
            parts.append(_sanitize_verbatim(node.content))
            parts.append(r"\end{verbatim}")
            i += 1
        elif isinstance(node, MathBlock):
            safe_math = _sanitize_math(node.content)
            parts.append(_plain(node.content))
            if node.display:
                parts.append(r"\[")
                parts.append(safe_math)
                parts.append(r"\]")
            else:
                parts.append(rf"\({safe_math}\)")
            i += 1
        elif isinstance(node, Blockquote):
            parts.append(r"\begin{quote}")
            for p in node.paragraphs:
                parts.append(_plain(ensure_rich_text(p).text))
                parts.append(_render_rich(ensure_rich_text(p)))
            parts.append(r"\end{quote}")
            i += 1
        elif isinstance(node, HorizontalRule):
            parts.append(r"\par\noindent\rule{\linewidth}{0.4pt}\par % ---")
            i += 1
        elif isinstance(node, Footnote):
            parts.append(_plain(node.text))
            parts.append(rf"\footnote{{{_esc(node.text)}}}")
            i += 1
        elif isinstance(node, Comment):
            parts.append(_plain(f"{node.author}: {node.text}"))
            anchor = f" on {_esc(node.anchor)}" if node.anchor else ""
            parts.append(
                rf"% comment by {_esc(node.author or 'reviewer')}{anchor}: {_esc(node.text)}"
            )
            warn_export(model, "latex: Comment kept as % comment (not marginpar)")
            i += 1
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            parts.append(_plain(f"{node.name} {fields}"))
            parts.append(
                rf"% pivot {_esc(node.name or 'table')}: {_esc(fields)}"
                + (rf" src={_esc(node.source_range)}" if node.source_range else "")
            )
            warn_export(model, "latex: PivotTable degraded to % comment")
            i += 1
        elif isinstance(node, Image):
            import base64

            alt = node.alt_text.split("|", 1)[0] if node.alt_text else "image"
            parts.append(_plain(alt))
            if node.data:
                assets = model.metadata.setdefault("latex_assets", {})
                if not isinstance(assets, dict):
                    assets = {}
                    model.metadata["latex_assets"] = assets
                n = sum(1 for k in assets if str(k).startswith("mdbridge_img_"))
                mime = (node.mime_type or "image/png").lower()
                ext = ".jpg" if ("jpeg" in mime or "jpg" in mime) else ".png"
                name = f"mdbridge_img_{n}{ext}"
                assets[name] = {
                    "mime": node.mime_type or "image/png",
                    "base64": base64.b64encode(node.data).decode("ascii"),
                    "bytes": len(node.data),
                }
                parts.append(r"\begin{center}")
                parts.append(rf"\includegraphics[width=0.85\linewidth]{{{name}}}")
                parts.append(r"\end{center}")
                parts.append(rf"% mdbridge-asset: {name} ({len(node.data)} bytes)")
                if alt:
                    parts.append(rf"% alt: {_esc(alt)}")
                warn_export(
                    model,
                    f"latex: Image → \\includegraphics{{{name}}} (bytes in metadata latex_assets)",
                )
            else:
                parts.append(f"% image: {alt}")
                parts.append(rf"\par [Image: {_esc(alt)}]")
                warn_export(model, "latex: Image missing binary → text placeholder")
            i += 1
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            parts.append(_plain(text or (node.content or "")[:160]))
            snippet = (node.content or "")[:160].replace("\n", " ")
            parts.append(rf"\par [HtmlBlock: {_esc(snippet)}]")
            warn_export(model, "latex: HtmlBlock → text placeholder")
            i += 1
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            parts.append(_plain(node.text))
            parts.append(
                rf"\par [Revision/{tag} {_esc(node.author)}: {_esc(node.text[:120])}]"
            )
            warn_export(model, "latex: Revision → text placeholder")
            i += 1
        elif isinstance(node, TextBox):
            parts.append(_plain(node.text))
            parts.append(rf"\par [TextBox: {_esc(node.text[:160])}]")
            warn_export(model, "latex: TextBox → text placeholder")
            i += 1
        else:
            plain = getattr(node, "plain_text", None)
            kind = node_type_name(node)
            detail = plain() if callable(plain) else ""
            parts.append(_plain(str(detail)))
            parts.append(rf"\par [{_esc(placeholder_line(kind, str(detail)[:160]))}]")
            warn_export(model, f"latex: unhandled node {kind} → placeholder")
            i += 1

    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"


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
            href = _safe_href(href)
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
