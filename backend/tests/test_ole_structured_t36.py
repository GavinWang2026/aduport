"""T36: structured OLE XLS (BIFF8) + PPT atom import."""

from __future__ import annotations

from aduport.core.document_model import Table
from aduport.importers.ole_biff8 import parse_biff8_workbook
from aduport.importers.ole_cfb import read_cfb_stream
from aduport.importers.ole_office import import_ppt, import_xls
from aduport.importers.ole_ppt import extract_ppt_texts
from tests._ole_fixtures import (
    build_minimal_biff8_workbook,
    build_minimal_ppt,
    build_minimal_ppt_stream,
    build_minimal_xls,
)


def test_biff8_label_cells_roundtrip_unit():
    stream = build_minimal_biff8_workbook(
        sheet_name="Sales",
        cells=[(0, 0, "Region"), (0, 1, "Amt"), (1, 0, "East"), (1, 1, "100")],
    )
    sheets = parse_biff8_workbook(stream)
    assert sheets and sheets[0].name == "Sales"
    rows = sheets[0].as_rows()
    assert rows[0][:2] == ["Region", "Amt"]
    assert rows[1][:2] == ["East", "100"]


def test_ppt_atoms_extract_unit():
    stream = build_minimal_ppt_stream(["Title Alpha", "Body Bravo"])
    texts = extract_ppt_texts(stream)
    assert "Title Alpha" in texts
    assert "Body Bravo" in texts


def test_import_xls_structured_table():
    blob = build_minimal_xls(
        sheet_name="Data",
        cells=[(0, 0, "Name"), (0, 1, "Qty"), (1, 0, "Widget"), (1, 1, "7")],
    )
    # CFB stream readable
    assert read_cfb_stream(blob, "Workbook")
    model = import_xls(blob, "t36.xls")
    assert model.metadata.get("engine") == "biff8"
    assert model.metadata.get("degraded") is False
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert tables
    assert tables[0].rows[0][0] == "Name"
    assert any("Widget" in row for row in tables[0].rows)


def test_import_ppt_structured_atoms():
    blob = build_minimal_ppt(["Deck Title", "Point One", "Point Two"])
    assert read_cfb_stream(blob, "PowerPoint Document")
    model = import_ppt(blob, "t36.ppt")
    assert model.metadata.get("engine") == "ppt_atoms"
    assert model.metadata.get("degraded") is False
    text = "\n".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "Deck Title" in text
    assert "Point One" in text
