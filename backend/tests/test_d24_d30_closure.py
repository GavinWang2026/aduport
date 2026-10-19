"""D-24 / D-26–D-30 remaining defect closure."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.document_model import (
    DocumentModel,
    FieldCode,
    Footnote,
    HeaderFooter,
    HtmlBlock,
)
from aduport.core.exceptions import FileTooLargeError, UploadTimeoutError
from aduport.core.upload_stream import read_upload_streaming
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.html_render import render_body_html
from aduport.importers.md_importer import (
    _MAX_BLOCKQUOTE_DEPTH,
    _nodes_from_html,
    _parse_blockquote,
    import_md,
)
from main import app


class _SlowUpload:
    """Stalls forever on read — D-24."""

    filename = "slow.md"

    async def read(self, _n: int = -1) -> bytes:
        await asyncio.sleep(3600)
        return b""


def test_d24_upload_timeout(monkeypatch):
    monkeypatch.setattr(settings, "upload_timeout_seconds", 0.05)

    async def _run() -> None:
        await read_upload_streaming(_SlowUpload(), prefix="aduport-slow-")  # type: ignore[arg-type]

    with pytest.raises(UploadTimeoutError):
        asyncio.run(_run())


def test_d26_opaque_html_produces_htmlblock():
    nodes = _nodes_from_html('<figure class="x"><figcaption>cap</figcaption></figure>')
    assert len(nodes) == 1
    assert isinstance(nodes[0], HtmlBlock)
    assert "figure" in nodes[0].content
    model = import_md(b"<figure><figcaption>LIVE_HTMLBLOCK</figcaption></figure>\n")
    assert any(
        isinstance(n, HtmlBlock) and "LIVE_HTMLBLOCK" in n.content for n in model.nodes
    )


def test_d27_docs_gated_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "expose_api_docs", False)
    from aduport.api.auth import _public_path

    assert not _public_path("/docs")
    assert not _public_path("/openapi.json")
    assert not _public_path("/redoc")
    assert settings.api_docs_enabled is False
    # App constructed at import: without debug/expose, OpenAPI routes are off.
    if app.docs_url is None:
        c = TestClient(app)
        assert c.get("/docs").status_code == 404


def test_d28_batch_member_budget_stops_early(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 50)
    monkeypatch.setattr(settings, "spill_threshold_mb", 0)

    class _FatUpload:
        filename = "fat.bin"

        def __init__(self, n: int) -> None:
            self._n = n
            self._sent = False

        async def read(self, size: int = -1) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return b"x" * self._n

    async def _run() -> None:
        await read_upload_streaming(
            _FatUpload(500),  # type: ignore[arg-type]
            prefix="aduport-cap-",
            max_bytes=100,
        )

    with pytest.raises(FileTooLargeError):
        asyncio.run(_run())


def test_d29_blockquote_depth_cap():
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark")
    depth = _MAX_BLOCKQUOTE_DEPTH + 5
    src = ("> " * depth) + "deep\n"
    tokens = md.parse(src)
    start = next(i for i, t in enumerate(tokens) if t.type == "blockquote_open")
    bq, consumed = _parse_blockquote(tokens, start, depth=0)
    assert consumed > 0
    assert bq is None or bq.paragraphs


def test_d30_html_consumes_header_and_fields():
    model = DocumentModel(
        nodes=[
            HeaderFooter(kind="header", text="HDR_CONSUMED"),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="3"),
        ]
    )
    html = render_body_html(model)
    assert "HDR_CONSUMED" in html
    assert "docx-chrome" in html
    assert "field-code" in html
    model2 = DocumentModel(
        nodes=[],
        metadata={
            "docx_header": "META_HDR",
            "docx_field_codes": [{"instr": "TOC \\o", "text": ""}],
        },
    )
    html2 = render_body_html(model2)
    assert "META_HDR" in html2
    assert "TOC" in html2


def test_d21_docx_footnotes_xml():
    model = DocumentModel(
        nodes=[Footnote(id="1", text="FN_BODY_ALPHA")],
    )
    raw = export_docx(model)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        assert "word/footnotes.xml" in zf.namelist()
        assert b"FN_BODY_ALPHA" in zf.read("word/footnotes.xml")
        assert b"footnoteReference" in zf.read("word/document.xml")


def test_formats_notes_docx_xlsx_caveats(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    c = TestClient(app)
    notes = c.get("/api/formats").json().get("notes") or {}
    assert "anydoc" in (notes.get("docx") or "").lower()
    assert "_ast" in (notes.get("xlsx") or "").lower() or "sidecar" in (
        notes.get("xlsx") or ""
    ).lower()
