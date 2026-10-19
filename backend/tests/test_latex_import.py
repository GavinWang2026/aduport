"""LaTeX importer unit tests."""

from __future__ import annotations

from aduport.core.converter import convert
from aduport.core.document_model import Heading
from aduport.importers.latex_importer import import_latex


def test_import_latex_sections():
    src = (
        r"\documentclass{article}"
        r"\begin{document}"
        r"\title{Demo Title}"
        r"\maketitle"
        r"\section{First}"
        "\nHello world body.\n"
        r"\subsection{Nested}"
        "\nMore text.\n"
        r"\begin{itemize}"
        r"\item Alpha"
        r"\item Beta"
        r"\end{itemize}"
        r"\end{document}"
    ).encode("utf-8")
    model = import_latex(src, "demo.tex")
    assert model.title == "Demo Title"
    heads = [n for n in model.nodes if isinstance(n, Heading)]
    assert any(h.plain_text() == "First" for h in heads)
    paras = " ".join(
        n.plain_text() if hasattr(n, "plain_text") else "" for n in model.nodes
    )
    assert "Hello world" in paras


def test_md_latex_md_roundtrip():
    md = "# Heading A\n\nParagraph with content.\n"
    tex = convert(md.encode("utf-8"), "md", "latex", filename="x.md")
    back = convert(tex, "latex", "md", filename="x.tex").decode("utf-8")
    assert "Heading A" in back
    assert "Paragraph" in back
