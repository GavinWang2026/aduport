"""ODS/ODP depth+breadth upgrades: package meta, typed cells, notes, roundtrip."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.core.document_model import (
    Chart,
    DocumentModel,
    Heading,
    Image,
    Paragraph,
    SpeakerNote,
    Table,
)
from app.exporters.odp_exporter import export_odp
from app.exporters.ods_exporter import export_ods
from app.importers.odp_importer import import_odp
from app.importers.ods_importer import import_ods

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_ods_package_has_meta_styles_and_float_cells():
    model = DocumentModel(
        title="Sales",
        nodes=[Table(name="T1", rows=[["Amt"], ["12.5"], ["abc"]])],
    )
    blob = export_ods(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert "meta.xml" in names
        assert "styles.xml" in names
        xml = zf.read("content.xml").decode("utf-8", "ignore")
        meta = zf.read("meta.xml").decode("utf-8", "ignore")
    assert "office:value-type=\"float\"" in xml
    assert "office:value=\"12.5\"" in xml
    assert "Sales" in meta or "dc:title" in meta


def test_ods_roundtrip_table_and_chart_sheet():
    model = DocumentModel(
        nodes=[
            Table(name="Grid", rows=[["H"], ["v1"]]),
            Chart(title="SENT_CH", chart_type="bar", categories=["A"], series=[]),
        ]
    )
    blob = export_ods(model)
    back = import_ods(blob, "round.ods")
    texts = " ".join(n.plain_text() if hasattr(n, "plain_text") else "" for n in back.nodes)
    assert "Grid" in texts or any(
        isinstance(n, Table) and n.name == "Grid" for n in back.nodes
    )
    assert "v1" in texts or any(
        isinstance(n, Table) and any("v1" in row for row in (n.rows or []))
        for n in back.nodes
    )
    # ChartsPivots sheet imported as table
    assert "SENT_CH" in texts or any("SENT_CH" in str(n) for n in back.nodes)


def test_odp_notes_and_image_roundtrip():
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=1, text="Intro"),
            Paragraph(text="SENT_BODY"),
            Image(data=TINY_PNG, alt_text="SENT_IMG", mime_type="image/png"),
            SpeakerNote(text="SENT_NOTE", slide_index=1),
        ],
    )
    blob = export_odp(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
        xml = zf.read("content.xml").decode("utf-8", "ignore")
        assert "meta.xml" in names
        assert any(n.startswith("Pictures/") for n in names)
    assert "presentation:notes" in xml
    assert "SENT_NOTE" in xml
    assert "draw:image" in xml

    back = import_odp(blob, "round.odp")
    notes = [n for n in back.nodes if isinstance(n, SpeakerNote)]
    assert notes and "SENT_NOTE" in notes[0].text
    body = " ".join(
        n.plain_text() if hasattr(n, "plain_text") else getattr(n, "text", "")
        for n in back.nodes
        if not isinstance(n, SpeakerNote)
    )
    assert "SENT_BODY" in body
    imgs = [n for n in back.nodes if isinstance(n, Image)]
    assert imgs and imgs[0].data and len(imgs[0].data) > 20


def test_ods_cell_span_export():
    from app.core.document_model import TableCell

    model = DocumentModel(
        nodes=[
            Table(
                name="SpanSheet",
                rows=[],
                cells=[
                    [
                        TableCell(text="MERGE", col_span=2),
                        TableCell(text="", covered=True),
                    ],
                    [TableCell(text="a"), TableCell(text="b")],
                ],
            )
        ]
    )
    blob = export_ods(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        xml = zf.read("content.xml").decode("utf-8", "ignore")
    assert "number-columns-spanned=\"2\"" in xml
    assert "covered-table-cell" in xml
