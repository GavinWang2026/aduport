"""Phase C · cold-path coverage: TXT / OLE / DOC / OCR / xlsb signal paths."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.converter import convert
from app.core.document_model import DocumentModel, Paragraph, Table
from app.core.exceptions import CorruptedFileError, InvalidDocumentError, OcrUnavailableError
from app.exporters.txt_exporter import export_txt
from app.importers import ocr_engine as oe
from app.importers.doc_importer import import_doc
from app.importers.ole_office import import_ppt, import_xls, import_xlsb, import_pps, import_pot
from app.importers.txt_importer import import_txt
from tests._ole_fixtures import build_cfb_with_stream, build_minimal_ppt, build_minimal_xls


def test_txt_import_utf8_and_roundtrip():
    raw = "Line one\n\nSENT_TXT_COLD\n\n验收".encode("utf-8")
    model = import_txt(raw, "cold.txt")
    assert any("SENT_TXT_COLD" in n.plain_text() for n in model.nodes)
    out = export_txt(model)
    assert "SENT_TXT_COLD" in out
    back = convert(raw, "txt", "md", filename="cold.txt")
    assert b"SENT_TXT_COLD" in back


def test_txt_import_rejects_non_utf8():
    with pytest.raises(InvalidDocumentError, match="UTF-8"):
        import_txt("你好".encode("gbk"), "bad.txt")


def test_ole_xls_structured_cold_path():
    blob = build_minimal_xls(
        sheet_name="Cold",
        cells=[(0, 0, "SENT_XLS_COLD"), (1, 0, "99")],
    )
    model = import_xls(blob, "cold.xls")
    parts: list[str] = []
    for n in model.nodes:
        if hasattr(n, "plain_text"):
            parts.append(n.plain_text())
        if isinstance(n, Table):
            for row in n.rows or []:
                parts.extend(str(c) for c in row)
    blob_txt = " ".join(parts)
    assert "SENT_XLS_COLD" in blob_txt
    assert model.metadata.get("importer")


def test_ole_ppt_structured_cold_path():
    blob = build_minimal_ppt(["SENT_PPT_COLD", "slide body"])
    model = import_ppt(blob, "cold.ppt")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "SENT_PPT_COLD" in text


def test_ocr_unavailable_error_payload():
    err = OcrUnavailableError("no engine", install_hint="pip install paddleocr")
    d = err.to_dict()
    assert d["ocr_required"] is True
    assert "paddleocr" in str(d.get("install", "")).lower()


def test_ocr_no_available_engine_raises(monkeypatch):
    class Dead(oe.OcrEngine):
        name = "dead"

        def available(self) -> bool:
            return False

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            return ""

        def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
            return DocumentModel(nodes=[Paragraph(text="x")])

    monkeypatch.setattr(
        oe,
        "_ENGINES",
        {"mineru": Dead(), "paddleocr_vl": Dead(), "docling": Dead()},
    )
    monkeypatch.setattr(oe, "resolve_ocr_chain", lambda **_k: ["mineru", "paddleocr_vl"])
    with pytest.raises(OcrUnavailableError):
        oe.import_pdf_with_ocr(b"%PDF-1.4", "x.pdf")


def test_doc_importer_ole_text_fallback(monkeypatch):
    # Force docling path to fail so OLE text scrape is exercised.
    monkeypatch.setattr(
        "app.importers._docling_converter.get_office_converter",
        lambda: (_ for _ in ()).throw(ImportError("no docling")),
        raising=False,
    )
    payload = ("SENT_DOC_COLD_TEXT " * 4).encode("utf-16-le")
    blob = build_cfb_with_stream("WordDocument", payload)
    model = import_doc(blob, "cold.doc")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "SENT_DOC_COLD" in text
    assert model.metadata.get("degraded") is True

    with pytest.raises(InvalidDocumentError):
        import_doc(b"short", "tiny.doc")
    with pytest.raises(InvalidDocumentError):
        import_doc(b"NOTOLE!!" + b"\x00" * 20, "bad.doc")


def test_doc_importer_empty_ole_raises(monkeypatch):
    monkeypatch.setattr(
        "app.importers.doc_importer._ole_extract_text",
        lambda _data: "   ",
    )
    blob = build_cfb_with_stream("WordDocument", b"\x00" * 64)
    with pytest.raises(CorruptedFileError):
        import_doc(blob, "empty.doc")


def test_xlsb_mislabeled_xlsx_and_true_bin_gate(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"/>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheetData><row r=\"1\"><c r=\"A1\" t=\"inlineStr\"><is><t>SENT_XLSB_MIS</t></is></c></row></sheetData>"
            "</worksheet>",
        )
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
    model = import_xlsb(buf.getvalue(), "mis.xlsb")
    assert model.metadata.get("detected_as") == "xlsx_mislabeled_xlsb"

    true_bin = io.BytesIO()
    with zipfile.ZipFile(true_bin, "w") as zf:
        zf.writestr("xl/workbook.bin", b"\x00BIN")
    monkeypatch.setattr(
        "app.importers.anydoc_bridge.should_handle",
        lambda _fmt: False,
        raising=False,
    )
    with pytest.raises(InvalidDocumentError, match="anydoc"):
        import_xlsb(true_bin.getvalue(), "true.xlsb")


def test_pps_pot_ole_cold_paths():
    blob = build_minimal_ppt(["SENT_PPS_COLD"])
    for fn, importer in (("a.pps", import_pps), ("a.pot", import_pot)):
        model = importer(blob, fn)
        text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
        assert "SENT_PPS_COLD" in text


def test_ocr_resolve_chain_force(monkeypatch):
    chain = oe.resolve_ocr_chain(force_engine="docling")
    assert chain[0] == "docling" or "docling" in chain
    chain2 = oe.resolve_ocr_chain()
    assert isinstance(chain2, list) and len(chain2) >= 1
