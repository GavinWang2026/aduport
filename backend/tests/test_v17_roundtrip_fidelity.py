"""v17 P0–P3: xlsx contract, titles, footnotes, math order, lists, MCP status."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

from aduport.core.converter import convert
from aduport.core.document_model import (
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
    Table,
)
from aduport.core.redact import redact_docx_bytes, redact_pdf_bytes
from aduport.core.title_source import is_placeholder_title
from aduport.core.watch import scan_inbox
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.epub_exporter import export_epub
from aduport.exporters.latex_exporter import export_latex
from aduport.exporters.md_exporter import export_md
from aduport.exporters.odt_exporter import export_odt
from aduport.exporters.pptx_exporter import export_pptx
from aduport.exporters.rtf_exporter import export_rtf
from aduport.importers.docx_importer import import_docx
from aduport.importers.latex_importer import import_latex
from aduport.importers.md_importer import import_md
from aduport.importers.odt_importer import import_odt
from aduport.importers.rtf_importer import import_rtf
from aduport.mcp_server import _pdf_flags


def test_xlsx_prose_roundtrip_is_readable():
    src = b"# Title Only\n\nA paragraph with no table.\n"
    xlsx = convert(src, "md", "xlsx", filename="note.md")
    back = convert(xlsx, "xlsx", "md", filename="note.xlsx").decode()
    assert "A paragraph with no table." in back
    assert "无工作表数据" not in back
    assert not back.lstrip().startswith("# input")
    assert not back.lstrip().startswith("# Untitled")


def test_epub_does_not_inject_untitled():
    model = DocumentModel(
        title="",
        nodes=[Heading(level=1, text="Sample Doc"), Paragraph(text="Intro")],
    )
    raw = export_epub(model)
    back = convert(raw, "epub", "md", filename="book.epub").decode()
    assert "# Untitled" not in back
    assert back.count("# Sample Doc") == 1


def test_footnote_ref_is_not_wrapped_as_link():
    rich = RichText(
        text="见[^1]",
        marks=[InlineMark(type=InlineMarkType.LINK, start=1, end=5, href="#fn-1")],
    )
    model = DocumentModel(
        nodes=[Paragraph(text=rich), Footnote(id="1", text="注")],
    )
    md = export_md(model)
    assert "[[^" not in md
    assert "[^1]" in md


def test_placeholder_title_stays_out_of_body():
    assert is_placeholder_title("Untitled")
    model = DocumentModel(title="Untitled", nodes=[Paragraph(text="Only a paragraph.")])
    assert not export_md(model).lstrip().startswith("# Untitled")


def test_docx_inline_math_stays_in_the_paragraph():
    model = DocumentModel(nodes=[Paragraph(text="Energy $$E=mc^2$$ next"), Footnote(id="1", text="n")])
    docx = export_docx(model)
    back = import_docx(docx, "math.docx")
    blobs = []
    for node in back.nodes:
        plain = getattr(node, "plain_text", None)
        if callable(plain):
            blobs.append(plain() or "")
    joined = "\n".join(blobs)
    assert "E=mc" in joined
    # The formula must not be a trailing-only node after the footnote.
    para = next(n for n in back.nodes if isinstance(n, Paragraph))
    assert "E=mc" in para.plain_text()


def _zip_text(blob: bytes, suffix: str) -> str:
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        parts = [zf.read(name).decode("utf-8", errors="replace") for name in zf.namelist() if name.endswith(suffix)]
    return "\n".join(parts)


def test_odt_list_roundtrip():
    model = DocumentModel(nodes=[ListItem(text="alpha", ordered=False), ListItem(text="beta")])
    odt = export_odt(model)
    assert "text:list" in _zip_text(odt, "content.xml")
    back = import_odt(odt, "lists.odt")
    assert back is not None
    items = [n for n in back.nodes if isinstance(n, ListItem)]
    assert [n.plain_text() for n in items] == ["alpha", "beta"]


def test_rtf_table_and_footnote_roundtrip():
    model = DocumentModel(
        nodes=[
            Table(rows=[["a", "b"], ["1", "2"]], header_rows=1),
            Footnote(id="1", text="note"),
        ]
    )
    raw = export_rtf(model).decode("latin-1", errors="replace")
    assert r"\trowd" in raw
    assert r"\footnote" in raw
    back = import_rtf(raw.encode("latin-1"), "t.rtf")
    assert any(isinstance(n, Table) for n in back.nodes)
    assert any(isinstance(n, Footnote) and "note" in n.text for n in back.nodes)


def test_latex_footnote_and_texttt():
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="use code", marks=[InlineMark(type=InlineMarkType.CODE, start=4, end=8)])),
            Footnote(id="1", text="src"),
        ]
    )
    tex = export_latex(model)
    assert r"\texttt" in tex
    assert r"\footnote" in tex
    back = import_latex(tex.encode(), "t.tex")
    assert any(isinstance(n, Footnote) for n in back.nodes)
    para = next(n for n in back.nodes if isinstance(n, Paragraph))
    assert any(m.type == InlineMarkType.CODE for m in para.text.marks)


def test_pptx_list_uses_body_placeholder():
    model = DocumentModel(
        title="Deck",
        nodes=[Heading(level=2, text="Section"), ListItem(text="first bullet")],
    )
    xml = _zip_text(export_pptx(model), ".xml")
    assert "first bullet" in xml
    assert 'name="Content Placeholder 2"' in xml


def test_pdf_flags_surface_degradation():
    flags = _pdf_flags(b"% aduport-pdf-engine: pdf_simple\n% aduport-pdf-degraded: true\n")
    assert flags["degraded"] is True
    assert flags["pdf_engine"] == "pdf_simple"


def test_docx_redact_replaces_run_text():
    model = DocumentModel(nodes=[Paragraph(text="mail me at ada@example.com please")])
    raw = export_docx(model)
    out, count = redact_docx_bytes(raw)
    document = _zip_text(out, "document.xml")
    assert count >= 1
    assert "ada@example.com" not in document
    assert "[REDACTED:email]" in document


def test_pdf_literal_mask_keeps_length():
    original = b"%PDF-1.4\n(ada@example.com) Tj\n%%EOF"
    out, count, partial = redact_pdf_bytes(original)
    assert count == 1
    assert partial is False
    assert len(out) == len(original)
    assert b"ada@example.com" not in out


_TEN_FEATURE_MD = """# Title

Intro **bold** *italic* `code`.

## Section

- alpha
- beta

> a quote

| a | b |
| --- | --- |
| 1 | 2 |

```py
print(1)
```

See note[^1].

[^1]: footnote text
"""


def _missing_structure(md: str) -> list[str]:
    lines = md.splitlines()

    def has(pred) -> bool:
        return any(pred(line) for line in lines)

    checks = {
        "H1": has(lambda line: line.startswith("# ") and not line.startswith("##")),
        "H2": has(lambda line: line.startswith("## ") and not line.startswith("###")),
        "bold": "**" in md,
        "italic": "*" in md.replace("**", ""),
        "code": "`" in md and "\\`" not in md,
        "list": has(lambda line: line.lstrip().startswith("- ")),
        "quote": has(lambda line: line.lstrip().startswith(">")),
        "table": has(lambda line: line.lstrip().startswith("|")),
        "fence": has(lambda line: line.startswith("```")),
        "footnote": "[^1]" in md,
    }
    return [name for name, ok in checks.items() if not ok]


def test_redbox_structure_roundtrip():
    """The §4.1 paths that used to drop lists, marks, quotes, fences, or langs."""
    source = _TEN_FEATURE_MD.encode()
    formats = (
        "odt",
        "rtf",
        "latex",
        "csv",
        "txt",
        "odp",
        "ods",
        "xlsx",
        "pptx",
        "pdf",
        "html",
        "epub",
    )
    for fmt in formats:
        mid = convert(source, "md", fmt, filename="sample.md")
        back = convert(mid, fmt, "md", filename=f"sample.{fmt}").decode("utf-8")
        missing = _missing_structure(back)
        assert missing == [], f"{fmt} lost {missing}\n{back[:800]}"
        assert "## Footnotes" not in back, fmt
        model = import_md(back.encode())
        headings = [node for node in model.nodes if isinstance(node, Heading)]
        assert len(headings) == 2, f"{fmt} headings={[h.plain_text() for h in headings]}"
        if fmt in ("latex", "pptx"):
            codes = [node for node in model.nodes if isinstance(node, CodeBlock)]
            assert codes and codes[0].lang == "py", f"{fmt} lang={getattr(codes[0], 'lang', None) if codes else None}"


def test_watch_scans_once(tmp_path: Path):
    inbox = tmp_path / "in"
    out = tmp_path / "out"
    inbox.mkdir()
    (inbox / "note.md").write_text("# T\n\npara\n", encoding="utf-8")
    written = scan_inbox(inbox, out, "txt", source_format="md")
    assert written
    assert (out / "note.txt").is_file()
    assert scan_inbox(inbox, out, "txt", source_format="md", seen={str((inbox / "note.md").resolve()): (inbox / "note.md").stat().st_mtime_ns}) == []
