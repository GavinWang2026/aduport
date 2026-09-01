"""OCR router unit tests (mocked engines — no MinerU/Paddle installed)."""

from __future__ import annotations

from app.config import settings
from app.core.document_model import DocumentModel, Paragraph
from app.importers import ocr_engine as oe


def test_resolve_chain_balanced(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "auto")
    monkeypatch.setattr(settings, "ocr_mode", "balanced")
    monkeypatch.setattr(settings, "ocr_fallback", "paddleocr_vl")
    assert oe.resolve_ocr_chain()[0] == "mineru"
    assert "paddleocr_vl" in oe.resolve_ocr_chain()


def test_resolve_chain_accurate(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "auto")
    monkeypatch.setattr(settings, "ocr_mode", "accurate")
    monkeypatch.setattr(settings, "ocr_fallback", "mineru")
    chain = oe.resolve_ocr_chain()
    assert chain[0] == "paddleocr_vl"


def test_resolve_chain_forced(monkeypatch):
    monkeypatch.setattr(settings, "ocr_fallback", "docling")
    assert oe.resolve_ocr_chain(force_engine="mineru")[0] == "mineru"


def test_import_pdf_with_ocr_uses_first_available(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "auto")
    monkeypatch.setattr(settings, "ocr_mode", "balanced")
    monkeypatch.setattr(settings, "ocr_fallback", "paddleocr_vl")
    monkeypatch.setattr(settings, "ocr_min_chars", 5)

    class FakeMinerU(oe.OcrEngine):
        name = "mineru"

        def available(self) -> bool:
            return True

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            return "# From MinerU\n\nhello world scanned"

        def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
            return DocumentModel(
                title="m",
                nodes=[Paragraph(text="From MinerU hello world scanned")],
            )

    class Boom(oe.OcrEngine):
        name = "paddleocr_vl"

        def available(self) -> bool:
            return True

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            raise RuntimeError("should not call")

        def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
            raise RuntimeError("should not call")

    monkeypatch.setitem(oe._ENGINES, "mineru", FakeMinerU())
    monkeypatch.setitem(oe._ENGINES, "paddleocr_vl", Boom())
    monkeypatch.setitem(oe._ENGINES, "docling", Boom())

    model = oe.import_pdf_with_ocr(b"%PDF-1.4 fake", "s.pdf")
    assert any("MinerU" in (getattr(n, "text", "") or "") for n in model.nodes)


def test_import_pdf_with_ocr_fallback_on_short(monkeypatch):
    monkeypatch.setattr(settings, "ocr_engine", "auto")
    monkeypatch.setattr(settings, "ocr_mode", "balanced")
    monkeypatch.setattr(settings, "ocr_fallback", "paddleocr_vl")
    monkeypatch.setattr(settings, "ocr_min_chars", 20)

    class ShortMinerU(oe.OcrEngine):
        name = "mineru"

        def available(self) -> bool:
            return True

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            return "x"

        def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
            return DocumentModel(title="m", nodes=[Paragraph(text="x")])

    class GoodPaddle(oe.OcrEngine):
        name = "paddleocr_vl"

        def available(self) -> bool:
            return True

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            return "long"

        def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
            return DocumentModel(
                title="p",
                nodes=[Paragraph(text="PaddleOCR long enough output here")],
            )

    class Unused(oe.OcrEngine):
        name = "docling"

        def available(self) -> bool:
            return False

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            raise NotImplementedError

    monkeypatch.setitem(oe._ENGINES, "mineru", ShortMinerU())
    monkeypatch.setitem(oe._ENGINES, "paddleocr_vl", GoodPaddle())
    monkeypatch.setitem(oe._ENGINES, "docling", Unused())

    model = oe.import_pdf_with_ocr(b"%PDF", "s.pdf")
    assert any("PaddleOCR" in (getattr(n, "text", "") or "") for n in model.nodes)


def test_list_ocr_engines_shape(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    data = oe.list_ocr_engines()
    assert data["enabled"] is True
    assert "available" in data
    assert "mineru" in data["available"]
    assert "paddleocr_vl" in data["available"]


def test_paddle_result_extracts_markdown_texts():
    pages = [
        {
            "markdown_texts": "Hello Paddle OCR Sample",
            "markdown_images": {},
            "page_index": 0,
        }
    ]
    assert oe._paddle_result_to_markdown(pages) == "Hello Paddle OCR Sample"


def test_paddle_result_prefers_markdown_attr_on_dict_subclass():
    class FakeResult(dict):
        @property
        def markdown(self):
            return {
                "markdown_texts": "From .markdown property",
                "markdown_images": {},
            }

    pages = [FakeResult(page_index=0, width=1)]
    assert oe._paddle_result_to_markdown(pages) == "From .markdown property"


def test_paddle_result_ignores_empty_dict_fallback():
    # Must not stringify the whole dict when no known keys
    assert oe._paddle_result_to_markdown({"page_index": 0}) == ""
