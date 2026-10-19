"""v18 depth gates: hyperlinks, images, lists, math, encoding, ops surfaces."""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.exceptions import EmptyFileError, InvalidDocumentError
from aduport.exporters.export_warnings import get_last_export_warnings
from aduport.sdk import capabilities
from main import aduport

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app)


def test_unique_hyperlinks_stay_linear():
    count = 400
    body = "\n".join(f"[t{i}](https://example.com/u/{i})" for i in range(count))
    started = time.perf_counter()
    blob = convert(body.encode(), "md", "docx", filename="links.md")
    elapsed = time.perf_counter() - started
    assert elapsed < 8, elapsed
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        rels = zf.read("word/_rels/document.xml.rels").decode("utf-8")
    assert rels.count("relationships/hyperlink") >= count


def test_local_markdown_image_is_embedded(tmp_path):
    pic = tmp_path / "pic.png"
    pic.write_bytes(_PNG)
    md = tmp_path / "note.md"
    md.write_text("![alt](pic.png)\n", encoding="utf-8")
    docx = convert(md.read_bytes(), "md", "docx", filename=str(md))
    html = convert(md.read_bytes(), "md", "html", filename=str(md))
    epub = convert(md.read_bytes(), "md", "epub", filename=str(md))
    with zipfile.ZipFile(io.BytesIO(docx)) as zf:
        assert any(name.startswith("word/media/") for name in zf.namelist())
    assert b"<img" in html
    with zipfile.ZipFile(io.BytesIO(epub)) as zf:
        assert any(name.startswith("EPUB/images/") for name in zf.namelist())


def test_nested_list_level_survives_docx_roundtrip():
    src = b"- parent\n  - child\n"
    back = convert(convert(src, "md", "docx", filename="list.md"), "docx", "md").decode()
    assert "parent" in back
    assert "  - child" in back or "  * child" in back


def test_display_math_stays_between_paragraphs():
    src = b"before\n\n$$E=mc^2$$\n\nafter\n"
    back = convert(convert(src, "md", "docx", filename="math.md"), "docx", "md").decode()
    before = back.find("before")
    formula = back.find("mc")
    after = back.find("after")
    assert 0 <= before < formula < after
    assert back.count("mc") == 1
    assert "01x dx" not in back


def test_headingless_docx_does_not_invent_filename_title():
    src = b"A paragraph and nothing else.\n"
    back = convert(
        convert(src, "md", "docx", filename="myfile.md"),
        "docx",
        "md",
        filename="myfile.docx",
    ).decode()
    assert "# myfile" not in back
    assert "A paragraph and nothing else." in back


def test_blank_line_between_paragraphs_survives():
    src = b"Alpha paragraph.\n\nBeta paragraph.\n"
    back = convert(convert(src, "md", "docx", filename="blank.md"), "docx", "md").decode()
    assert "Alpha paragraph.\n\nBeta paragraph." in back


def test_rag_filename_and_default_overlap(monkeypatch):
    monkeypatch.setattr(settings, "rag_chunk_overlap", 150)
    assert type(settings).model_fields["rag_chunk_overlap"].default == 150
    payload = json.loads(convert(b"hello rag body\n", "md", "rag", filename="notes.md"))
    assert payload["chunking"]["overlap"] == 150
    assert payload["elements"][0]["metadata"]["filename"] == "notes.md"


def test_encoding_fallback_and_nul(client):
    text = "中文标题\n\n正文。\n"
    gbk = convert(text.encode("gbk"), "md", "md", filename="gbk.md")
    assert "中文标题" in gbk.decode()
    warns = " ".join(get_last_export_warnings())
    assert "source-encoding:" in warns
    assert "gb18030" in warns

    utf16 = convert(text.encode("utf-16"), "md", "html", filename="utf16.md")
    assert "中文标题".encode() in utf16

    response = client.post(
        "/api/convert",
        files={"file": ("gbk.md", text.encode("gbk"), "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
    )
    assert response.status_code == 200, response.text
    assert "gb18030" in response.headers.get("x-aduport-encoding", "")

    nulled = convert(b"keep\x00this\n", "md", "html", filename="nul.md")
    assert b"\x00" not in nulled
    assert b"keepthis" in nulled

    with pytest.raises(InvalidDocumentError):
        convert(bytes(range(256)), "md", "md", filename="junk.md")
    with pytest.raises(EmptyFileError):
        convert(b"", "md", "md", filename="empty.md")
    with pytest.raises(InvalidDocumentError):
        convert("café".encode("latin-1"), "md", "md", filename="latin.md")


def test_capabilities_report_pdf_engine():
    caps = capabilities()
    assert caps["pdf_engine"] in {"weasyprint", "pdf_simple"}
    assert isinstance(caps["pdf_degraded"], bool)


def test_cli_and_mcp_expose_ops_surfaces():
    from typer.testing import CliRunner

    from aduport.cli import aduport as cli

    help_text = CliRunner().invoke(cli, ["--help"]).stdout
    for name in ("jobs", "fidelity", "vectors", "rag-feedback", "usage", "watch"):
        assert name in help_text

    pytest.importorskip("mcp")
    from aduport.mcp_server import _build_server

    server = _build_server()
    manager = getattr(server, "_tool_manager", None)
    names = set(getattr(manager, "_tools", {}) or {})
    assert {
        "convert_batch",
        "watch_scan",
        "list_jobs",
        "resume_job",
        "reprocess_job",
        "fidelity_probe",
        "vector_search",
        "rag_layout_feedback",
        "license_status",
        "usage_status",
    } <= names
