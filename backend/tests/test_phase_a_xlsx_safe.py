"""Phase A: XLSX import safe_zip/xml_safe + configurable sheet cap with warnings."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.core.document_model import DocumentModel, Table
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import ZipInflateBudget
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx


def test_xlsx_import_respects_max_sheets_with_warning(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "xlsx_max_sheets", 64)
    tables = [Table(name=f"S{i}", rows=[["h"], [str(i)]]) for i in range(10)]
    blob = export_xlsx(DocumentModel(nodes=tables))

    monkeypatch.setattr(settings, "xlsx_max_sheets", 3)
    back = import_xlsx(blob, "many.xlsx")
    data_tables = [n for n in back.nodes if isinstance(n, Table)]
    assert len(data_tables) <= 3
    assert back.metadata.get("sheets_truncated") == 7
    assert back.metadata.get("xlsx_max_sheets") == 3
    warns = back.metadata.get("export_warnings") or []
    assert any("truncated" in w and "xlsx_max_sheets=3" in w for w in warns)


def test_xlsx_import_no_truncation_under_cap(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "xlsx_max_sheets", 64)
    tables = [Table(name=f"S{i}", rows=[[str(i)]]) for i in range(5)]
    blob = export_xlsx(DocumentModel(nodes=tables))
    back = import_xlsx(blob, "five.xlsx")
    assert "sheets_truncated" not in back.metadata
    data_tables = [n for n in back.nodes if isinstance(n, Table)]
    assert len(data_tables) >= 5


def test_xlsx_import_sheet_read_honors_inflate_budget(monkeypatch):
    """Import must use ZipInflateBudget on sheet XML (oversized member → skip, no unbounded read)."""
    from aduport.config import settings
    from aduport.core import safe_zip

    monkeypatch.setattr(settings, "xlsx_max_sheets", 64)
    blob = export_xlsx(DocumentModel(nodes=[Table(name="A", rows=[["ok", "1"]])]))
    huge = (
        b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + (b"<x/>" * 50_000)
        + b"</worksheet>"
    )
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(blob)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                dst.writestr(info.filename, huge)
            else:
                dst.writestr(info.filename, data)

    class TinyBudget(safe_zip.ZipInflateBudget):
        def __init__(self, limit=None):
            super().__init__(limit=8_000)

    monkeypatch.setattr("aduport.exporters.xlsx_exporter.ZipInflateBudget", TinyBudget)
    from aduport.core.exceptions import CorruptedFileError

    try:
        import_xlsx(out.getvalue(), "bomb.xlsx")
        raised = False
    except CorruptedFileError:
        raised = True
    # Sheet inflate exceeds budget → no worksheet data → corrupted (not silent OOM)
    assert raised


def test_zip_inflate_budget_rejects_zero_remaining():
    blob = export_xlsx(DocumentModel(nodes=[Table(name="A", rows=[["x"]])]))
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        budget = ZipInflateBudget(limit=0)
        try:
            budget.read(zf, "xl/workbook.xml")
            raised = False
        except FileTooLargeError:
            raised = True
        assert raised
