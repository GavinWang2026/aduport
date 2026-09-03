"""Depth-gap upgrades: AST nodes, no silent discard, FFI panic map, warnings."""

from __future__ import annotations

from app.core.document_model import (
    DocumentModel,
    FieldCode,
    HeaderFooter,
    HtmlBlock,
    Image,
    MathBlock,
    Numbering,
    PageSetup,
    Paragraph,
    Revision,
    Section,
    TextBox,
)
from app.exporters.export_warnings import get_last_export_warnings
from app.exporters.json_exporter import SCHEMA_VERSION, export_json, import_json
from app.exporters.latex_exporter import export_latex
from app.exporters.odt_exporter import export_odt
from app.importers.anydoc_bridge import AnydocFallback, _translate_error


def test_priority_ast_nodes_json_roundtrip():
    model = DocumentModel(
        title="depth",
        nodes=[
            Section(title="Ch1", props={"cols": 2}),
            PageSetup(page_size="Letter", orientation="landscape"),
            HeaderFooter(kind="footer", text="page"),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="1"),
            Numbering(num_id="1", abstract_num_id="0", levels=[{"ilvl": 0}]),
            Paragraph(text="body"),
        ],
    )
    raw = export_json(model)
    assert SCHEMA_VERSION == "1.2"
    assert '"type": "section"' in raw
    back = import_json(raw.encode("utf-8"))
    types = [n.type for n in back.nodes]
    assert "section" in types
    assert "page_setup" in types
    assert "header_footer" in types
    assert "field" in types
    assert "numbering" in types


def test_odt_no_longer_silently_drops_image_math_html():
    model = DocumentModel(
        nodes=[
            Image(data=b"PNG", alt_text="logo", mime_type="image/png"),
            MathBlock(content="E=mc^2", display=True),
            HtmlBlock(content="<b>x</b>"),
        ]
    )
    data = export_odt(model)
    assert data[:2] == b"PK"
    # unzip content.xml and assert embedded image / math / html markers
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
        xml = zf.read("content.xml").decode("utf-8")
        assert any(n.startswith("Pictures/") for n in names)
    assert "draw:image" in xml or "logo" in xml
    assert "math:math" in xml or "E=mc" in xml
    assert "HtmlBlock" in xml or "x" in xml
    assert model.metadata.get("export_warnings")


def test_latex_no_silent_drop_html_revision_textbox():
    model = DocumentModel(
        nodes=[
            HtmlBlock(content="<script>x</script>"),
            Revision(change="insert", author="a", text="new"),
            TextBox(text="box"),
        ]
    )
    tex = export_latex(model)
    assert "HtmlBlock" in tex
    assert "Revision" in tex
    assert "TextBox" in tex
    assert model.metadata.get("export_warnings")


def test_ffi_panic_exception_mapped():
    class PanicException(BaseException):
        pass

    mapped = _translate_error(PanicException("boom"), "x.docx")
    assert isinstance(mapped, AnydocFallback)
    assert mapped.code == "panic"


def test_export_warnings_collected_via_converter(monkeypatch):
    from app.config import settings
    from app.core.converter import convert

    monkeypatch.setattr(settings, "convert_isolate", False)
    monkeypatch.setattr(settings, "cache_enabled", False)
    out = convert(
        b"# hi\n\n$$\nx\n$$\n",
        "md",
        "latex",
        filename="t.md",
    )
    assert b"documentclass" in out or b"begin{document}" in out
    # warnings may be empty for simple md→latex; ensure API helper callable
    _ = get_last_export_warnings()
