"""Vertical shortboard upgrades: RTF export, xlsx sheet names, registry completeness."""

from __future__ import annotations

from aduport.core.document_model import DocumentModel, Heading, Paragraph, Table
from aduport.core.registry import ensure_builtin_plugins, get_exporter, registered_export_formats
from aduport.exporters.rtf_exporter import export_rtf
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx
from aduport.importers.rtf_importer import import_rtf


def test_registry_includes_rtf_xlsx_odt():
    ensure_builtin_plugins()
    ex = set(registered_export_formats())
    assert {"rtf", "xlsx", "odt", "csv", "json", "latex"}.issubset(ex)
    assert get_exporter("rtf") is not None


def test_export_rtf_roundtrip_text():
    model = DocumentModel(
        title="T",
        nodes=[Heading(level=1, text="Hi"), Paragraph(text="body")],
    )
    raw = export_rtf(model)
    assert raw.startswith(b"{\\rtf1")
    back = import_rtf(raw, "x.rtf")
    plain = " ".join(
        n.plain_text() for n in back.nodes if hasattr(n, "plain_text")
    )
    assert "Hi" in plain or "body" in plain


def test_xlsx_preserves_sheet_names():
    model = DocumentModel(
        nodes=[
            Table(name="Sales", rows=[["A", "B"], ["1", "2"]], header_rows=1),
            Table(name="Inventory", rows=[["Sku"], ["x"]], header_rows=1),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw)
    names = [n.name for n in back.nodes if isinstance(n, Table)]
    assert names == ["Sales", "Inventory"]
