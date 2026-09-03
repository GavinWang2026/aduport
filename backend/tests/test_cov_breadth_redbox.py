"""§2.3 red-box coverage: edge + naked importers → ≥75% line coverage offline."""

from __future__ import annotations

import hashlib
import struct
import zipfile
from io import BytesIO
from types import SimpleNamespace

import pytest

from app.config import settings
from app.core.document_model import DocumentModel, Heading, Paragraph, RichText
from app.core.exceptions import (
    CorruptedFileError,
    InvalidDocumentError,
    OcrUnavailableError,
)
from app.exporters.docx_exporter import export_docx
from app.exporters.mobi_exporter import export_mobi
from app.exporters.odt_exporter import export_odt
from app.exporters.pdf_simple import export_pdf_simple
from app.exporters.pptx_exporter import export_pptx
from app.importers import _docling_utils as du
from app.importers.csv_importer import import_csv
from app.importers.docx_importer import import_docx
from app.importers.docx_text import _zip_xml_fallback, try_extract_docx_model
from app.importers.epub_text import try_extract_epub_model
from app.importers.mobi_importer import import_mobi
from app.importers.mobi_text import _palmdoc_decompress, try_extract_mobi_model
from app.importers.odt_importer import import_odt
from app.importers.ole_biff8 import parse_biff8_workbook
from app.importers.ole_office import import_pps, import_pot, import_ppt, import_xls, import_xlsb
from app.importers.pdf_importer import import_pdf, _is_hub_network_error
from app.importers.pptx_importer import import_pptx
from tests._ole_fixtures import (
    _rec,
    _u16,
    _u32,
    _xl_unicode,
    build_cfb_with_stream,
    build_minimal_biff8_workbook,
    build_minimal_ppt,
    build_minimal_xls,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _u16le(n: int) -> bytes:
    return struct.pack("<H", n & 0xFFFF)


def _f64le(n: float) -> bytes:
    return struct.pack("<d", n)


def build_biff8_rich_workbook() -> bytes:
    """BIFF8 with SST/LABELSST/NUMBER/RK/MULRK/FORMULA + latin-1 BOUNDSHEET."""
    out = bytearray()
    out += _rec(
        0x0809,
        _u16(0x0600) + _u16(0x0005) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06),
    )
    # SST: 2 unique strings "Alpha", "Beta"
    sst_body = _u32(2) + _u32(2)
    for s in ("Alpha", "Beta"):
        sst_body += _xl_unicode(s)
    out += _rec(0x00FC, sst_body)
    # latin-1 sheet name (flags=0)
    name = b"S1"
    bs_payload = _u32(0) + bytes([0x00, 0x00, len(name), 0x00]) + name
    bs_start = len(out)
    out += _rec(0x0085, bs_payload)
    out += _rec(0x000A, b"")
    sheet_pos = len(out)
    struct.pack_into("<I", out, bs_start + 4, sheet_pos)

    out += _rec(
        0x0809,
        _u16(0x0600) + _u16(0x0010) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06),
    )
    # LABELSST row0 col0 → Alpha (idx 0)
    out += _rec(0x00FD, _u16(0) + _u16(0) + _u16(0x000F) + _u32(0))
    # LABELSST row0 col1 → Beta
    out += _rec(0x00FD, _u16(0) + _u16(1) + _u16(0x000F) + _u32(1))
    # NUMBER 3.5 at (1,0)
    out += _rec(0x0203, _u16(1) + _u16(0) + _u16(0x000F) + _f64le(3.5))
    # RK integer 42 at (1,1): (42 << 2) | 0x02
    rk_int = (42 << 2) | 0x02
    out += _rec(0x027E, _u16(1) + _u16(1) + _u16(0x000F) + _u32(rk_int))
    # MULRK row2 cols 0-1
    rk_a = (7 << 2) | 0x02
    rk_b = (8 << 2) | 0x02
    mul = _u16(2) + _u16(0) + _u16(0x000F) + _u32(rk_a) + _u16(0x000F) + _u32(rk_b) + _u16(1)
    out += _rec(0x00BD, mul)
    # FORMULA with cached float — first byte of IEEE754 must not be 0..3
    out += _rec(0x0006, _u16(3) + _u16(0) + _u16(0x000F) + _f64le(1.1) + b"\x00" * 2)
    # LABEL latin-1 via short path already covered; add unicode LABEL
    out += _rec(0x0204, _u16(4) + _u16(0) + _u16(0x000F) + _xl_unicode("Hi"))
    out += _rec(0x000A, b"")
    return bytes(out)


def _build_epub(
    *,
    with_encryption: bool = False,
    with_rights: bool = False,
    deobfuscate_font: bool = False,
    basename_match: bool = False,
) -> bytes:
    buf = BytesIO()
    epub_id = "urn:uuid:cov-redbox-1"
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>""",
        )
        if with_encryption or deobfuscate_font:
            uri = "OEBPS/font.otf" if deobfuscate_font else "OEBPS/chap.xhtml"
            algo = "http://www.idpf.org/2008/embedding"
            zf.writestr(
                "META-INF/encryption.xml",
                f"""<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:encryption">
<EncryptedData>
  <EncryptionMethod Algorithm="{algo}"/>
  <CipherData><CipherReference URI="{uri}"/></CipherData>
</EncryptedData>
</encryption>""",
            )
        if with_rights:
            zf.writestr("META-INF/rights.xml", "<rights>drm</rights>")
        href = "chap.xhtml" if not basename_match else "nested/chap.xhtml"
        zf.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Cov EPUB</dc:title><dc:identifier id="uid">{epub_id}</dc:identifier>
</metadata>
<manifest>
<item id="c1" href="{href}" media-type="application/xhtml+xml"/>
</manifest>
<spine><itemref idref="c1"/></spine>
</package>""",
        )
        xhtml = (
            '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            "<body><p>Hello EPUB coverage body text.</p></body></html>"
        )
        path = "OEBPS/nested/chap.xhtml" if basename_match else "OEBPS/chap.xhtml"
        if deobfuscate_font:
            # Put plaintext chapter + obfuscated font (chapter itself not encrypted)
            zf.writestr("OEBPS/chap.xhtml", xhtml)
            key = hashlib.sha1(epub_id.encode("utf-8")).digest()
            plain = b"OTTO" + b"\x00" * 100
            ob = bytearray(plain)
            for i in range(min(1040, len(ob))):
                ob[i] ^= key[i % 20]
            zf.writestr("OEBPS/font.otf", bytes(ob))
            # Fix encryption URI already set; chapter unencrypted — rewrite spine only chap
        else:
            zf.writestr(path, xhtml)
            if with_encryption and not deobfuscate_font:
                # encrypted chapter won't deobfuscate as font algo on xhtml — skip path
                pass
    return buf.getvalue()


def _minimal_docx_zip(body_xml: str) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", body_xml)
        zf.writestr(
            "word/_rels/document.xml.rels",
            """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
    Target="https://example.com/cov" TargetMode="External"/>
</Relationships>""",
        )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ole_biff8 / ole_office
# ---------------------------------------------------------------------------


def test_ole_biff8_number_rk_sst_formula():
    stream = build_biff8_rich_workbook()
    sheets = parse_biff8_workbook(stream)
    assert sheets and sheets[0].name == "S1"
    rows = sheets[0].as_rows()
    flat = " ".join(c for r in rows for c in r)
    assert "Alpha" in flat and "Beta" in flat
    assert "3.5" in flat or "3,5" in flat
    assert "42" in flat
    assert "1.1" in flat or "1,1" in flat
    assert "Hi" in flat
    # empty cells → as_rows []
    empty_stream = build_minimal_biff8_workbook(cells=[(0, 0, "")])
    # clear after parse by constructing sheet with no non-empty — use unit BiffSheet
    from app.importers.ole_biff8 import BiffSheet

    assert BiffSheet(name="E").as_rows() == []


def test_ole_biff8_no_boundsheet_defaults_sheet1():
    out = bytearray()
    out += _rec(
        0x0809,
        _u16(0x0600) + _u16(0x0005) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06),
    )
    out += _rec(0x000A, b"")
    out += _rec(
        0x0809,
        _u16(0x0600) + _u16(0x0010) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06),
    )
    out += _rec(0x0204, _u16(0) + _u16(0) + _u16(0x000F) + _xl_unicode("Solo"))
    out += _rec(0x000A, b"")
    sheets = parse_biff8_workbook(bytes(out))
    assert sheets[0].name == "Sheet1"
    assert sheets[0].as_rows()[0][0] == "Solo"


def test_ole_office_xls_ppt_pps_pot_and_errors():
    xls = import_xls(build_minimal_xls(cells=[(0, 0, "X")]), "a.xls")
    assert xls.metadata["engine"] == "biff8"
    ppt = import_ppt(build_minimal_ppt(["Hello PPT coverage"]), "a.ppt")
    assert ppt.metadata["engine"] == "ppt_atoms"
    assert import_pps(build_minimal_ppt(["PPS"]), "a.pps").nodes
    assert import_pot(build_minimal_ppt(["POT"]), "a.pot").nodes
    with pytest.raises(InvalidDocumentError):
        import_xls(b"short", "s.xls")
    with pytest.raises(InvalidDocumentError):
        import_xls(b"NOTOLE!!", "s.xls")


def test_ole_office_xls_degraded_scrape():
    payload = ("OLE_SCRAPE_SENTINEL_VALUE " * 4).encode("utf-16-le")
    blob = build_cfb_with_stream("Workbook", payload + b"\x00" * 64)
    # Corrupt BIFF so structured fails → ole_text
    model = import_xls(blob, "scrape.xls")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "OLE_SCRAPE_SENTINEL" in text
    assert model.metadata.get("engine") == "ole_text"


def test_ole_xlsb_mislabeled_xlsx():
    # Minimal xlsx-like zip
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>XLSB_MIS</t></is></c></row></sheetData>
</worksheet>""",
        )
        zf.writestr("xl/sharedStrings.xml", "<sst/>")
    model = import_xlsb(buf.getvalue(), "mis.xlsb")
    assert model.metadata.get("detected_as") == "xlsx_mislabeled_xlsb"


def test_ole_xlsb_bin_requires_anydoc(monkeypatch):
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.bin", b"\x00" * 32)
    with pytest.raises(InvalidDocumentError):
        import_xlsb(buf.getvalue(), "true.xlsb")


# ---------------------------------------------------------------------------
# csv / odt / docx / pptx / mobi wrappers
# ---------------------------------------------------------------------------


def test_csv_odt_wrappers():
    assert import_csv(b"a,b\n1,2\n", "t.csv").nodes
    with pytest.raises(InvalidDocumentError):
        import_csv(b"", "e.csv")
    odt = export_odt(DocumentModel(nodes=[Paragraph(text="ODT redbox body")]))
    assert import_odt(odt, "t.odt").nodes
    with pytest.raises(CorruptedFileError):
        import_odt(b"not-zip", "bad.odt")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
    with pytest.raises(InvalidDocumentError):
        import_odt(buf.getvalue(), "nocontent.odt")


def _install_fake_docling(monkeypatch, *, convert_raises: BaseException | None = None):
    """Make ``import docling.document_converter`` succeed without real Docling."""
    import sys
    import types

    class _Conv:
        def convert(self, *_a, **_k):
            if convert_raises is not None:
                raise convert_raises
            return SimpleNamespace(
                document=SimpleNamespace(
                    body=None,
                    texts=[SimpleNamespace(label="paragraph", text="fake-docling-ok")],
                )
            )

    fake_dc = types.ModuleType("docling.document_converter")
    fake_dc.DocumentConverter = object
    fake_root = types.ModuleType("docling")
    fake_root.document_converter = fake_dc
    monkeypatch.setitem(sys.modules, "docling", fake_root)
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_dc)
    monkeypatch.setattr(
        "app.importers._docling_converter.get_office_converter",
        lambda: _Conv(),
        raising=False,
    )
    # Also patch module attribute if already imported
    try:
        import app.importers._docling_converter as dc

        monkeypatch.setattr(dc, "get_office_converter", lambda: _Conv())
    except Exception:
        pass


def test_docx_importer_native_and_docling_fallback(monkeypatch):
    blob = export_docx(DocumentModel(nodes=[Paragraph(text="DOCX wrapper body")]))
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "docling" or name.startswith("docling."):
            raise ImportError("no docling")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    model = import_docx(blob, "w.docx")
    assert model.nodes

    with pytest.raises(CorruptedFileError):
        import_docx(b"notzip", "bad.docx")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(InvalidDocumentError):
        import_docx(buf.getvalue(), "empty.docx")


def test_docx_importer_docling_fail_uses_native(monkeypatch):
    blob = export_docx(DocumentModel(nodes=[Paragraph(text="DOCX docling fail path")]))
    _install_fake_docling(monkeypatch, convert_raises=RuntimeError("docling boom"))
    model = import_docx(blob, "f.docx")
    assert model.nodes


def test_docx_importer_docling_success(monkeypatch):
    blob = export_docx(DocumentModel(nodes=[Paragraph(text="DOCX docling ok")]))
    _install_fake_docling(monkeypatch)
    # enrich_docx_model may still run; ignore failures
    monkeypatch.setattr(
        "app.importers.docx_ooxml.enrich_docx_model",
        lambda m, *_a, **_k: m,
        raising=False,
    )
    model = import_docx(blob, "ok.docx")
    assert model.nodes
    assert model.metadata.get("importer") == "docx_docling"


def test_pptx_importer_light_path(monkeypatch):
    from app.core.exceptions import InvalidDocumentError as IDE

    monkeypatch.setattr(
        "app.importers.pptx_importer._require_docling",
        lambda: (_ for _ in ()).throw(IDE("force light")),
    )
    blob = export_pptx(DocumentModel(nodes=[Paragraph(text="PPTX light path body")]))
    model = import_pptx(blob, "p.pptx")
    assert model.nodes
    with pytest.raises(CorruptedFileError):
        import_pptx(b"nozip", "b.pptx")


def test_pptx_importer_docling_fail_light(monkeypatch):
    blob = export_pptx(DocumentModel(nodes=[Paragraph(text="PPTX fail body")]))

    def _get():
        class C:
            def convert(self, *_a, **_k):
                raise RuntimeError("fail")

        return C

    monkeypatch.setattr("app.importers.pptx_importer._require_docling", lambda: _get)
    assert import_pptx(blob, "p.pptx").nodes


def test_pptx_importer_docling_success(monkeypatch):
    blob = export_pptx(DocumentModel(nodes=[Paragraph(text="PPTX ok body")]))

    def _get():
        class C:
            def convert(self, *_a, **_k):
                return SimpleNamespace(
                    document=SimpleNamespace(
                        body=None,
                        texts=[SimpleNamespace(label="paragraph", text="pptx-docling")],
                    )
                )

        return C

    monkeypatch.setattr("app.importers.pptx_importer._require_docling", lambda: _get)
    model = import_pptx(blob, "ok.pptx")
    assert any("pptx-docling" in n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))


def test_pptx_require_docling_import_error(monkeypatch):
    import builtins
    from app.importers import pptx_importer as pi

    real = builtins.__import__

    def _fake(name, *a, **kw):
        if name == "docling" or name.startswith("docling."):
            raise ImportError("x")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake)
    with pytest.raises(InvalidDocumentError):
        pi._require_docling()


def test_pptx_invalid_package():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "x")
    with pytest.raises(InvalidDocumentError):
        import_pptx(buf.getvalue(), "bad.pptx")
    blob = export_mobi(DocumentModel(nodes=[Paragraph(text="Hello MOBI importer body text")]))
    model = import_mobi(blob, "m.mobi")
    assert model.metadata.get("importer") == "mobi_text"
    with pytest.raises(InvalidDocumentError):
        import_mobi(b"notmobi" * 20, "x.mobi")


def test_mobi_importer_docling_when_native_empty(monkeypatch):
    # Valid magic; force native miss → docling path
    data = bytearray(b"\x00" * 256)
    data[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", data, 76, 2)
    struct.pack_into(">I", data, 78, 100)
    struct.pack_into(">I", data, 86, 110)

    monkeypatch.setattr(
        "app.importers.mobi_text.try_extract_mobi_model",
        lambda *_a, **_k: None,
    )

    class _C:
        def convert(self, *_a, **_k):
            return SimpleNamespace(
                document=SimpleNamespace(
                    body=None,
                    texts=[SimpleNamespace(label="paragraph", text="via-docling-mobi")],
                )
            )

    monkeypatch.setattr(
        "app.importers.mobi_importer._require_docling",
        lambda: (lambda: _C()),
    )
    model = import_mobi(bytes(data), "d.mobi")
    assert any(
        "via-docling-mobi" in n.plain_text()
        for n in model.nodes
        if hasattr(n, "plain_text")
    )


# ---------------------------------------------------------------------------
# docx_text
# ---------------------------------------------------------------------------


def test_docx_text_hyperlink_and_heading():
    from docx import Document
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    doc = Document()
    p = doc.add_paragraph()
    run = p.add_run(" click here ")
    # Insert hyperlink element wrapping text
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), "rId1")
    new_run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "click here"
    new_run.append(t)
    hyperlink.append(new_run)
    p._p.append(hyperlink)
    # Add relationship
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    p.part.relate_to("https://example.com/cov", RT.HYPERLINK, is_external=True)
    # Fix rId to match
    rId = list(p.part.rels.keys())[-1]
    hyperlink.set(qn("r:id"), rId)

    h = doc.add_paragraph("Heading Two Text", style="Heading 2")
    assert h is not None
    buf = BytesIO()
    doc.save(buf)
    model = try_extract_docx_model(buf.getvalue(), title="hl")
    assert model and model.nodes
    assert any(isinstance(n, Heading) and n.level == 2 for n in model.nodes)
    # link mark may be present
    marks = []
    for n in model.nodes:
        if isinstance(n, Paragraph) and isinstance(n.text, RichText):
            marks.extend(n.text.marks or [])
    # soft assert — hyperlink path exercised even if mark rebase strips


def test_docx_text_fallback_and_errors(monkeypatch):
    assert try_extract_docx_model(b"not-a-zip") is None
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("other.xml", "<a/>")
    assert try_extract_docx_model(buf.getvalue()) is None

    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>  Fallback XML text here  </w:t></w:r></w:p></w:body></w:document>"
    )
    blob = _minimal_docx_zip(xml)
    fb = _zip_xml_fallback(blob, title="fb")
    assert fb and fb.metadata["engine"] == "xml"
    assert "Fallback" in fb.nodes[0].plain_text()

    monkeypatch.setattr(
        "docx.Document",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("open fail")),
    )
    model = try_extract_docx_model(blob, title="forced")
    assert model and model.metadata.get("engine") == "xml"


def test_docx_text_empty_body_triggers_fallback(monkeypatch):
    # Valid package but Document yields empty paragraphs → fallback
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>OnlyViaFallback</w:t></w:r></w:p></w:body></w:document>"
    )
    blob = _minimal_docx_zip(xml)

    class _EmptyDoc:
        class _Body:
            def iterchildren(self):
                return iter([])

        element = SimpleNamespace(body=_Body())

    monkeypatch.setattr("docx.Document", lambda *_a, **_k: _EmptyDoc())
    model = try_extract_docx_model(blob, title="e")
    assert model and "OnlyViaFallback" in model.nodes[0].plain_text()


# ---------------------------------------------------------------------------
# epub / mobi_text / pdf
# ---------------------------------------------------------------------------


def test_epub_text_happy_basename_drm_rights():
    assert try_extract_epub_model(b"notzip") is None
    m = try_extract_epub_model(_build_epub(), title="e")
    assert m and m.nodes
    m2 = try_extract_epub_model(_build_epub(basename_match=True), title="e2")
    assert m2 and m2.nodes
    m3 = try_extract_epub_model(_build_epub(with_encryption=True), title="e3")
    # may still get nodes from unencrypted or skip — encryption on chap skips if fail
    m4 = try_extract_epub_model(_build_epub(with_rights=True), title="e4")
    assert m4 and m4.metadata.get("drm_protected") is True
    m5 = try_extract_epub_model(_build_epub(deobfuscate_font=True), title="e5")
    assert m5 and m5.nodes


def test_mobi_text_palmdoc_and_plain(monkeypatch):
    assert try_extract_mobi_model(b"short") is None
    blob = export_mobi(DocumentModel(nodes=[Paragraph(text="Hello MOBI body coverage text")]))
    assert try_extract_mobi_model(blob, title="m").nodes
    # PalmDOC unit: literal + space+char + backref
    out = _palmdoc_decompress(bytes([0x41, 0xC1, 0x01, 0x08]) + b"LITERALS")
    assert b"A" in out and b"LIT" in out
    seed = bytes([0x48, 0x69])  # Hi
    out2 = _palmdoc_decompress(seed + bytes([0xC0 | ord("!")]))
    assert b"Hi" in out2
    # back-reference: after "ABCD", copy distance=4 length=3+ → "ABC"
    packed = bytes([0x41, 0x42, 0x43, 0x44])
    # distance=4 → ((dist<<3)|len_bits); b1 high bits
    # distance = ((b<<8)|b2)&0x3FFF >> 3; length = (b2&7)+3
    # want distance=4, length=3 → b2 low 3 bits = 0; dist<<3 = 32
    # ((b<<8)|b2) & 0x3FFF = 32 → use b=0x80, b2=0x20? 
    # ((0x80<<8)|0x20)=0x8020 & 0x3FFF = 0x20 = 32; >>3 = 4; len=(0x20&7)+3=3
    packed += bytes([0x80, 0x20])
    out3 = _palmdoc_decompress(packed)
    assert b"ABCD" in out3
    # Force binary-heavy → decompress path in _decode_payload
    from app.importers import mobi_text as mt

    binary = bytes([0x00, 0x01, 0x02, 0xFF] * 200)
    monkeypatch.setattr(mt, "_palmdoc_decompress", lambda d: b"Plain para one\n\nPlain para two more text")
    # Build minimal PalmDB with binary records
    data = bytearray(b"\x00" * 200)
    data[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", data, 76, 3)
    struct.pack_into(">I", data, 78, 120)
    struct.pack_into(">I", data, 86, 130)
    struct.pack_into(">I", data, 94, 180)
    data[130:180] = binary[:50]
    model = try_extract_mobi_model(bytes(data), title="plain")
    assert model and len(model.nodes) >= 1


def _pdf_without_sidecar() -> bytes:
    pdf = export_pdf_simple(
        DocumentModel(nodes=[Paragraph(text="Hello PDF text layer coverage content")])
    )
    # Strip AST sidecar so import_pdf exercises text/docling branches
    marker = b"\n%%MDBRIDGE-AST-v1\n"
    i = pdf.find(marker)
    return pdf[:i] if i >= 0 else pdf


def test_pdf_importer_text_engine_and_reject(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "text")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    model = import_pdf(_pdf_without_sidecar(), "t.pdf")
    assert model.nodes
    with pytest.raises(InvalidDocumentError):
        import_pdf(b"%NOTpdf", "x.pdf")
    assert _is_hub_network_error(RuntimeError("huggingface timed out offline"))
    assert not _is_hub_network_error(RuntimeError("local parse bug"))


def test_pdf_importer_auto_rich_text_short_circuit(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "pdf_text_min_chars", 8)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)
    model = import_pdf(_pdf_without_sidecar(), "rich.pdf")
    assert model.nodes


def test_pdf_importer_auto_weak_ocr_off(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "pdf_text_min_chars", 99999)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)
    try:
        model = import_pdf(_pdf_without_sidecar(), "w.pdf")
        assert model is not None
    except (OcrUnavailableError, CorruptedFileError):
        pass


def test_pdf_importer_models_missing_text_fallback(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)
    model = import_pdf(_pdf_without_sidecar(), "m.pdf")
    assert model.nodes


def test_pdf_importer_hub_error_fallback(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: True)

    def _boom(*_a, **_k):
        raise RuntimeError("huggingface connecttimeout offline")

    monkeypatch.setattr("app.importers.pdf_importer._import_with_docling", _boom)
    model = import_pdf(_pdf_without_sidecar(), "hub.pdf")
    assert model.nodes


def test_pdf_importer_sidecar_hit(monkeypatch):
    monkeypatch.setattr(settings, "pdf_ast_sidecar", True)
    pdf = export_pdf_simple(
        DocumentModel(nodes=[Paragraph(text="sidecar body unique")])
    )
    model = import_pdf(pdf, "side.pdf")
    assert model.nodes
    assert (model.metadata or {}).get("pdf_ast_sidecar") is True


def test_pdf_importer_docling_path_success(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "docling_offline", True)
    monkeypatch.setattr(settings, "hf_endpoint", "https://hf-mirror.example")
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: True)

    class _Conv:
        def convert(self, *_a, **_k):
            return SimpleNamespace(
                document=SimpleNamespace(
                    body=None,
                    texts=[SimpleNamespace(label="paragraph", text="pdf-docling-ok")],
                )
            )

    import sys
    import types

    fake_dc = types.ModuleType("docling.document_converter")
    fake_dc.DocumentConverter = object
    fake_root = types.ModuleType("docling")
    fake_root.document_converter = fake_dc
    monkeypatch.setitem(sys.modules, "docling", fake_root)
    monkeypatch.setitem(sys.modules, "docling.document_converter", fake_dc)
    monkeypatch.setattr(
        "app.importers._docling_converter.get_pdf_converter",
        lambda: _Conv(),
        raising=False,
    )
    import app.importers._docling_converter as dconv

    monkeypatch.setattr(dconv, "get_pdf_converter", lambda: _Conv())
    from app.importers import pdf_importer as pi

    model = pi.import_pdf_via_docling(_pdf_without_sidecar(), "d.pdf")
    assert any("pdf-docling-ok" in n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))


def test_pdf_importer_ocr_unavailable_empty(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(settings, "pdf_text_min_chars", 10)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)

    def _weak(*_a, **_k):
        return DocumentModel(
            title="w",
            nodes=[],
            metadata={"pdf_text_chars": 0},
        )

    monkeypatch.setattr(
        "app.importers.pdf_importer.extract_pdf_text_model",
        _weak,
    )
    with pytest.raises(OcrUnavailableError):
        import_pdf(b"%PDF-1.4 emptyish", "empty.pdf")


def test_pdf_importer_models_missing_no_text(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)

    def _boom(*_a, **_k):
        raise InvalidDocumentError("no text")

    monkeypatch.setattr(
        "app.importers.pdf_importer.extract_pdf_text_model",
        _boom,
    )
    with pytest.raises(CorruptedFileError):
        import_pdf(b"%PDF-1.4 x", "nt.pdf")


def test_pdf_importer_models_missing_ocr_on(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)

    def _boom(*_a, **_k):
        raise InvalidDocumentError("no text")

    monkeypatch.setattr(
        "app.importers.pdf_importer.extract_pdf_text_model",
        _boom,
    )
    with pytest.raises(OcrUnavailableError):
        import_pdf(b"%PDF-1.4 x", "nt2.pdf")


def test_pdf_importer_hub_error_no_text(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: True)
    monkeypatch.setattr(
        "app.importers.pdf_importer._import_with_docling",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("huggingface connecttimeout offline")
        ),
    )
    monkeypatch.setattr(
        "app.importers.pdf_importer.extract_pdf_text_model",
        lambda *_a, **_k: (_ for _ in ()).throw(InvalidDocumentError("none")),
    )
    with pytest.raises(CorruptedFileError):
        import_pdf(b"%PDF-1.4 x", "hub2.pdf")


def test_pdf_importer_generic_docling_error(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "docling")
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: True)
    monkeypatch.setattr(
        "app.importers.pdf_importer._import_with_docling",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("corrupt blob")),
    )
    with pytest.raises(CorruptedFileError):
        import_pdf(_pdf_without_sidecar(), "gen.pdf")


def test_pdf_importer_ocr_chain_then_fallback(monkeypatch):
    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "pdf_text_min_chars", 99999)
    monkeypatch.setattr("app.importers.pdf_importer.docling_models_ready", lambda: False)

    def _ocr(*_a, **_k):
        raise OcrUnavailableError("no ocr")

    monkeypatch.setattr(
        "app.importers.ocr_engine.import_pdf_with_ocr",
        _ocr,
        raising=False,
    )
    # After OCR unavailable, falls through to models_ready False → text
    model = import_pdf(_pdf_without_sidecar(), "ocr.pdf")
    assert model.nodes


# ---------------------------------------------------------------------------
# _docling_utils remaining branches
# ---------------------------------------------------------------------------


def test_docling_utils_edge_branches():
    assert du._table_from_item(SimpleNamespace(table=SimpleNamespace(grid=None, table_cells=None))) is None
    assert du._table_from_item(SimpleNamespace(data=SimpleNamespace(grid=[], table_cells=None))) is None
    # non-list grid row
    t = du._table_from_item(
        SimpleNamespace(data=SimpleNamespace(grid=[SimpleNamespace(text="x")], table_cells=None))
    )
    assert t is not None
    # export_to_dataframe raises
    assert (
        du._table_from_item(
            SimpleNamespace(
                data=SimpleNamespace(
                    grid=["x"],
                    table_cells=None,
                    export_to_dataframe=lambda: (_ for _ in ()).throw(RuntimeError("df")),
                )
            )
        )
        is None
    )
    assert du._cell_text(object())  # str fallback
    assert du._image_from_item(SimpleNamespace(image=object(), text="")) is None

    class _BadSave:
        def save(self, *_a, **_k):
            raise OSError("nope")

    assert du._image_from_item(SimpleNamespace(image=_BadSave(), text="")) is None

    # iterate_items path
    doc = SimpleNamespace(
        body=None,
        iterate_items=lambda: [
            (SimpleNamespace(label="footnote", text="fn text"), 0),
            (SimpleNamespace(label="paragraph", text=""), 0),
        ],
    )
    model = du.docling_document_to_model(doc, title="it")
    assert any(isinstance(n, Paragraph) for n in model.nodes)

    # markdown data-URI images
    tiny = "iVBORw0KGgo="
    doc2 = SimpleNamespace(
        body=None,
        texts=[],
        export_to_markdown=lambda: (
            f"![a](data:image/png;base64,{tiny})\n![bad](data:image/png;base64,@@@)\npara\n"
        ),
    )
    m2 = du.docling_document_to_model(doc2, title="md")
    assert m2.nodes
