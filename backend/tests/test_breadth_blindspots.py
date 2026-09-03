"""Breadth blind spots: Chart AST, DOCX TOC, PPTX notesSlide, PDF Annot/AcroForm."""

from __future__ import annotations

import io
import zipfile

from app.core.document_model import (
    Chart,
    DocumentModel,
    Heading,
    Paragraph,
    SpeakerNote,
)
from app.exporters import docx_exporter, json_exporter, pptx_exporter
from app.exporters.pdf_simple import export_pdf_simple
from app.importers.pptx_text import try_extract_pptx_model


def test_chart_json_roundtrip_and_visible_export():
    model = DocumentModel(
        title="Charts",
        nodes=[
            Chart(
                title="Revenue",
                chart_type="bar",
                categories=["Q1", "Q2"],
                series=[{"name": "A", "values": [1, 2]}],
            )
        ],
    )
    raw = json_exporter.export_json(model)
    assert '"type": "chart"' in raw
    back = json_exporter.import_json(raw.encode("utf-8"))
    charts = [n for n in back.nodes if isinstance(n, Chart)]
    assert len(charts) == 1
    assert charts[0].title == "Revenue"
    assert charts[0].categories == ["Q1", "Q2"]

    pdf = export_pdf_simple(model).decode("latin-1", errors="ignore")
    assert "Chart:bar" in pdf or "[Chart" in pdf


def test_docx_auto_toc_field():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="Intro"),
            Paragraph(text="body"),
            Heading(level=2, text="Detail"),
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "w:fldChar" in xml
    assert "TOC" in xml
    assert "instrText" in xml


def test_pptx_speaker_notes_roundtrip():
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=1, text="Slide One"),
            Paragraph(text="Body text here"),
            SpeakerNote(text="Say this aloud", slide_index=1),
        ],
    )
    blob = pptx_exporter.export_pptx(model)
    assert blob[:2] == b"PK"
    # notesSlide part should exist
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        note_parts = [n for n in zf.namelist() if "notesSlide" in n]
        assert note_parts, f"no notesSlide in {zf.namelist()}"
        joined = b"".join(zf.read(n) for n in note_parts).decode("utf-8", errors="ignore")
        assert "Say this aloud" in joined

    back = try_extract_pptx_model(blob, title="Deck")
    assert back is not None
    notes = [n for n in back.nodes if isinstance(n, SpeakerNote)]
    assert notes
    assert any("Say this aloud" in n.text for n in notes)


def test_pdf_simple_acroform_and_annot():
    model = DocumentModel(nodes=[Paragraph(text="Hello form")])
    blob = export_pdf_simple(model)
    text = blob.decode("latin-1", errors="ignore")
    assert "/AcroForm" in text
    assert "/Subtype /Widget" in text or "/FT /Tx" in text
    assert "/Subtype /Text" in text
    assert "mdbridge_field" in text or "mdbridge-pdf-annot" in text
