"""v16 round-trip fidelity gate (report P0–P3).

These cases are the minimum reproductions from the v16 functional audit.
They stay in CI so a title duplicate, leaked font table, or flattened
code/quote block fails the build instead of waiting for the next audit.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.ast_diff import diff_models
from aduport.core.converter import convert
from aduport.core.document_model import CodeBlock, Comment, DocumentModel, Heading, Paragraph, Revision
from aduport.core.pdf_pages import extract_pages, merge_pdfs, page_count, rotate_pdf, split_pdf
from aduport.core.redact import redact_text, watermark_text
from main import app


def _pdf(width: float, height: float) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    doc.new_page(width, height)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app)


def test_html_h1_is_not_duplicated():
    out = convert(
        b"<body><h1>Only H1</h1><p>x</p></body>",
        "html",
        "md",
        filename="in.html",
    ).decode()
    assert out.count("# Only H1") == 1
    assert "x" in out

    two = convert(b"<h1>First</h1><h1>Second</h1>", "html", "md").decode()
    assert two.count("# First") == 1
    assert two.count("# Second") == 1


def test_html_head_title_does_not_leak_into_body():
    html = (
        b"<head><title>PageTitle</title></head>"
        b"<body><h1>H1 text</h1><p>body</p></body>"
    )
    out = convert(html, "html", "md", filename="page.html").decode()
    assert "PageTitle" not in out
    assert out.count("# H1 text") == 1
    assert "body" in out

    roundtrip = convert(b"# T\n\npara\n", "md", "html", filename="t.md")
    back = convert(roundtrip, "html", "md", filename="t.html").decode()
    assert "\ndocument\n" not in f"\n{back}\n"
    assert back.count("# T") == 1


_STRUCT = "# T\n\n> quote\n\n```js\nlet x=1;\n```\n\npara\n".encode()


def test_latex_roundtrip_keeps_quote():
    tex = convert(_STRUCT, "md", "latex", filename="t.md")
    back = convert(tex, "latex", "md", filename="t.tex").decode()
    assert "> quote" in back
    assert "```" in back
    assert "let x=1;" in back
    assert "para" in back
    assert back.count("# T") == 1


def test_rtf_roundtrip_keeps_fence_and_quote():
    rtf = convert(_STRUCT, "md", "rtf", filename="t.md")
    back = convert(rtf, "rtf", "md", filename="t.rtf").decode()
    assert "Times New Roman" not in back
    assert "Courier New" not in back
    assert "> quote" in back
    assert "```js" in back
    assert "let x=1;" in back
    assert "code:js" not in back
    assert "para" in back
    assert back.count("# T") == 1
    assert not back.lstrip().startswith("# input")
    assert not back.lstrip().startswith("# t\n")


def test_rtf_strips_font_and_color_tables():
    raw = (
        rb"{\rtf1\ansi\deff0{\fonttbl{\f0 Times New Roman;}{\f1 Courier New;}}"
        rb"{\colortbl;\red0\green0\blue255;}\f0\fs24 para\par}"
    )
    out = convert(raw, "rtf", "md", filename="input.rtf").decode()
    assert "Times New Roman" not in out
    assert "Courier New" not in out
    assert "red0" not in out
    assert "para" in out


def test_docx_roundtrip_keeps_fence_quote_and_drops_toc():
    src = "# T\n\n> quote\n\n```js\nlet x=1;\n```\n\npara\n".encode()
    docx = convert(src, "md", "docx", filename="t.md")
    back = convert(docx, "docx", "md", filename="t.docx").decode()
    assert "```js" in back
    assert "let x=1;" in back
    assert "> quote" in back
    assert "*[TOC]*" not in back
    assert "[TOC]" not in back
    assert not back.lstrip().startswith("# t\n")
    assert back.count("# T") == 1


def test_odt_roundtrip_keeps_structure():
    src = "# T\n\n> quote\n\n```js\nlet x=1;\n```\n\npara\n".encode()
    odt = convert(src, "md", "odt", filename="t.md")
    assert odt[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(odt)) as zf:
        content = zf.read("content.xml").decode()
    assert "AduPortCode" in content
    assert "AduPortQuote" in content
    assert "\\`\\`\\`" not in content
    back = convert(odt, "odt", "md", filename="t.odt").decode()
    assert "\\>" not in back
    assert "\\`" not in back
    assert "> quote" in back
    assert "```js" in back
    assert "let x=1;" in back


def test_footnote_id_survives_docx_once():
    src = b"para[^a]\n\n[^a]: note-a\n"
    docx = convert(src, "md", "docx", filename="t.md")
    back = convert(docx, "docx", "md", filename="t.docx").decode()
    assert back.count("[^a]: note-a") == 1
    assert "[^1]:" not in back
    assert "[^a]" in back


def test_json_roundtrip_title_is_not_the_filename():
    src = b"# T\n\npara\n"
    blob = convert(src, "md", "json", filename="t.md")
    back = convert(blob, "json", "md").decode()
    assert "# input" not in back
    assert back.count("# T") == 1


def test_pdf_page_ops_roundtrip_counts():
    a = _pdf(200, 100)
    b = _pdf(100, 200)
    merged = merge_pdfs([a, b])
    assert page_count(merged) == 2
    assert page_count(extract_pages(merged, "2")) == 1
    pieces = split_pdf(merged)
    assert len(pieces) == 2
    rotated = rotate_pdf(a, 90)
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(rotated)
    try:
        assert int(doc[0].get_rotation()) == 90
    finally:
        doc.close()


def test_redact_and_watermark_mvp():
    text, n = redact_text("mail me at ada@example.com or 13800138000")
    assert n >= 2
    assert "ada@example.com" not in text
    assert "13800138000" not in text
    marked = watermark_text(text, "CONFIDENTIAL")
    assert marked.startswith("> CONFIDENTIAL")
    assert marked.rstrip().endswith("> CONFIDENTIAL")


def test_ast_diff_sees_code_block_change():
    left = DocumentModel(
        title="T",
        nodes=[Heading(level=1, text="T"), CodeBlock(lang="js", content="let x=1;")],
    )
    right = DocumentModel(
        title="T",
        nodes=[Heading(level=1, text="T"), Paragraph(text="let x=1;")],
    )
    report = diff_models(left, right)
    assert report["approximateOnly"] is False
    assert report["equal"] is False
    assert any(c["op"] != "equal" for c in report["changes"])
    assert report["review"]["comments_added"] == []


def _form_pdf(name: str = "party", value: str = "Alice") -> bytes:
    objects = [
        "<< /Type /Catalog /Pages 2 0 R /AcroForm << /Fields [4 0 R] >> >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Annots [4 0 R] >>",
        (
            f"<< /Type /Annot /Subtype /Widget /FT /Tx /T ({name}) "
            f"/V ({value}) /Rect [10 10 100 30] >>"
        ),
    ]
    blob = b"%PDF-1.4\n"
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(blob))
        blob += f"{index} 0 obj\n{body}\nendobj\n".encode()
    xref_at = len(blob)
    xref = [f"xref\n0 {len(objects) + 1}\n", "0000000000 65535 f \n"]
    xref.extend(f"{off:010d} 00000 n \n" for off in offsets)
    trailer = (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    )
    return blob + "".join(xref).encode() + trailer.encode()


def test_review_delta_lists_comment_and_revision():
    left = DocumentModel(
        title="T",
        nodes=[Paragraph(text="para"), Comment(author="ann", anchor="p1", text="old")],
    )
    right = DocumentModel(
        title="T",
        nodes=[
            Paragraph(text="para"),
            Comment(author="ann", anchor="p1", text="new"),
            Revision(change="insert", author="ed", text="clause"),
        ],
    )
    report = diff_models(left, right)
    assert report["equal"] is False
    review = report["review"]
    assert review["comments_removed"][0]["text"] == "old"
    assert review["comments_added"][0]["text"] == "new"
    assert review["revisions_added"][0]["change"] == "insert"
    assert review["revisions_added"][0]["author"] == "ed"


def test_form_fill_and_detached_signature(client):
    from aduport.core.pdf_forms import extract_form_fields, fill_form_fields

    raw = _form_pdf()
    extracted = extract_form_fields(raw)
    assert extracted["compressed_form"] is False
    assert extracted["fields"][0]["name"] == "party"
    assert extracted["fields"][0]["value"] == "Alice"
    filled = fill_form_fields(raw, {"party": "Bob"})
    assert extract_form_fields(filled)["fields"][0]["value"] == "Bob"

    api_extract = client.post(
        "/api/pdf/form/extract",
        files={"file": ("form.pdf", raw, "application/pdf")},
    )
    assert api_extract.status_code == 200, api_extract.text
    assert api_extract.json()["fields"][0]["value"] == "Alice"

    api_fill = client.post(
        "/api/pdf/form/fill",
        files={"file": ("form.pdf", raw, "application/pdf")},
        data={"values": '{"party":"Bob"}'},
    )
    assert api_fill.status_code == 200, api_fill.text
    assert b"(Bob)" in api_fill.content

    signed = client.post(
        "/api/sign",
        files={"file": ("n.txt", b"contract", "text/plain")},
        data={"key": "local-test-key", "key_id": "desk"},
    )
    assert signed.status_code == 200, signed.text
    body = signed.json()
    assert body["alg"] == "HMAC-SHA256"
    assert body["key_id"] == "desk"
    assert "local-test-key" not in signed.text
    ok = client.post(
        "/api/verify",
        files={"file": ("n.txt", b"contract", "text/plain")},
        data={"key": "local-test-key", "mac": body["mac"]},
    )
    assert ok.status_code == 200
    assert ok.json()["valid"] is True
    bad = client.post(
        "/api/verify",
        files={"file": ("n.txt", b"changed", "text/plain")},
        data={"key": "local-test-key", "mac": body["mac"]},
    )
    assert bad.json()["valid"] is False
    short = client.post(
        "/api/sign",
        files={"file": ("n.txt", b"contract", "text/plain")},
        data={"key": "short"},
    )
    assert short.status_code == 400


def test_tenant_list_omits_api_keys(client, monkeypatch, tmp_path):
    from aduport.core.tenants import clear_tenant_cache

    path = tmp_path / "keys.json"
    secret = "super-secret-tenant-key"
    path.write_text(
        '[{"tenant_id":"acme","api_key":"%s","rate_limit":9,"daily_quota":3}]' % secret,
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "api_keys", str(path))
    monkeypatch.setattr(settings, "api_key", "")
    clear_tenant_cache()
    try:
        res = client.get("/api/tenants", headers={"X-API-Key": secret})
        assert res.status_code == 200, res.text
        assert secret not in res.text
        row = next(item for item in res.json()["tenants"] if item["tenant_id"] == "acme")
        assert row["daily_quota"] == 3
        assert row["rate_limit"] == 9
        assert "api_key" not in row
    finally:
        clear_tenant_cache()


def test_tools_api_pdf_redact_diff(client):
    a = _pdf(120, 120)
    b = _pdf(80, 80)
    merged = client.post(
        "/api/pdf/merge",
        files=[
            ("files", ("a.pdf", a, "application/pdf")),
            ("files", ("b.pdf", b, "application/pdf")),
        ],
    )
    assert merged.status_code == 200, merged.text
    assert page_count(merged.content) == 2

    red = client.post(
        "/api/redact",
        files={"file": ("n.md", b"see ada@example.com\n", "text/markdown")},
        data={"watermark": "INTERNAL"},
    )
    assert red.status_code == 200, red.text
    body = red.json()
    assert body["replacements"] >= 1
    assert "ada@example.com" not in body["text"]
    assert "INTERNAL" in body["text"]

    diff = client.post(
        "/api/diff",
        files={
            "left": ("a.md", b"# A\n\npara\n", "text/markdown"),
            "right": ("b.md", b"# A\n\nother\n", "text/markdown"),
        },
    )
    assert diff.status_code == 200, diff.text
    assert diff.json()["equal"] is False
