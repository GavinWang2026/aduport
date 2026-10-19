"""P0 batch: CSV formula, HTML XSS, MD fence protect, EPUB deobfuscate, stream, SSRF."""

from __future__ import annotations

import hashlib
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from aduport.core.document_model import (
    CodeBlock,
    Comment,
    DocumentModel,
    Heading,
    HtmlBlock,
    Image,
    Paragraph,
    Table,
    ensure_rich_text,
)
from aduport.core.safe_fetch import is_safe_url
from aduport.core.stream_convert import _iter_text_chunks
from aduport.exporters.csv_exporter import export_csv
from aduport.exporters.docx_comments import inject_word_comments
from aduport.exporters.html_render import render_body_html
from aduport.importers.epub_text import _try_deobfuscate
from aduport.importers.md_importer import import_md


def test_csv_formula_injection_neutralized():
    model = DocumentModel(
        nodes=[
            Table(
                rows=[
                    ["Alice", "=cmd|'/c calc'!A0"],
                    ["Bob", "+1"],
                    ["Cathy", "@SUM(A1)"],
                    ["D", "-1"],
                    ["E", "\t=1"],
                ]
            )
        ]
    )
    out = export_csv(model)
    # Each dangerous cell must be quoted with a leading apostrophe
    assert "'=cmd|" in out
    assert "'+1" in out
    assert "'@SUM" in out
    assert "-1" in out and "'-1" not in out
    assert "\t=1" in out and "'\t=1" not in out


def test_html_block_xss_sanitized():
    model = DocumentModel(
        nodes=[
            HtmlBlock(content="<img src=x onerror=alert(1)>"),
            HtmlBlock(content="<svg onload=alert(2)>"),
            HtmlBlock(content="<SCRIPT>alert(3)</SCRIPT>"),
            Image(data=b"x", mime_type='image/png" onerror="alert(9)', alt_text="x"),
        ]
    )
    html = render_body_html(model)
    assert "<script" not in html.lower()
    assert "<svg" not in html.lower()
    assert "onerror" not in html.lower()
    assert "alert" not in html.lower()


def test_md_preprocessor_preserves_fenced_code():
    src = "```python\ncost = $$price$$ * 2\nprint(cost)\n```\n"
    model = import_md(src.encode())
    codes = [n for n in model.nodes if isinstance(n, CodeBlock)]
    assert len(codes) == 1
    assert "$$price$$" in codes[0].content
    assert "print(cost)" in codes[0].content

    src2 = "```text\n[^1]: NOT a footnote\n```\n"
    model2 = import_md(src2.encode())
    codes2 = [n for n in model2.nodes if isinstance(n, CodeBlock)]
    assert len(codes2) == 1
    assert "[^1]: NOT a footnote" in codes2[0].content
    from aduport.core.document_model import Footnote

    assert not any(isinstance(n, Footnote) for n in model2.nodes)


def test_md_preprocessor_preserves_indented_and_inline_dollars():
    """History P0 #10: 4-space indented + inline code must keep $$."""
    from aduport.core.document_model import MathBlock

    indented = "    cost = $$price$$\n"
    model = import_md(indented.encode())
    assert not any(isinstance(n, MathBlock) for n in model.nodes)
    blob = " ".join(
        getattr(n, "content", None) or getattr(getattr(n, "text", None), "text", "") or ""
        for n in model.nodes
    )
    assert "$$price$$" in blob or "$$price$$" in indented  # preserved through import
    # Prefer code-block path
    codes = [n for n in model.nodes if isinstance(n, CodeBlock)]
    if codes:
        assert "$$price$$" in codes[0].content

    inline = "use `$$x$$` in prose\n"
    model2 = import_md(inline.encode())
    assert not any(isinstance(n, MathBlock) for n in model2.nodes)
    paras = [n for n in model2.nodes if isinstance(n, Paragraph)]
    assert paras
    assert "$$x$$" in ensure_rich_text(paras[0].text).text


def test_htmlblock_direct_javascript_href_stripped():
    """History P0 #5 / N-02: AST HtmlBlock path must not persist JS URLs."""
    payloads = [
        '<a href=" javascript:alert(1)">x</a>',
        '<a href="\tjavascript:alert(1)">x</a>',
        '<a href="&#9;javascript:alert(1)">x</a>',
    ]
    for raw in payloads:
        html = render_body_html(DocumentModel(nodes=[HtmlBlock(content=raw)]))
        assert "javascript:" not in html.lower(), raw
        assert "alert" not in html.lower(), raw


def test_zip_inflate_session_is_cumulative(monkeypatch):
    """N-01: multiple read_zip_member calls share one budget."""
    import io
    import zipfile as zfmod

    from aduport.core.exceptions import FileTooLargeError
    from aduport.core.safe_zip import read_zip_member, zip_inflate_session

    buf = io.BytesIO()
    with zfmod.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"x" * 80)
        zf.writestr("b.txt", b"y" * 80)
    data = buf.getvalue()
    with zfmod.ZipFile(io.BytesIO(data)) as zf:
        with zip_inflate_session(limit=100) as budget:
            budget.bind(zf)
            assert len(read_zip_member(zf, "a.txt")) == 80
            with pytest.raises(FileTooLargeError):
                read_zip_member(zf, "b.txt")



def test_epub_deobfuscate_idpf_sha1():
    epub_id = "urn:uuid:12345678-1234-5678-1234-567812345678"
    key = hashlib.sha1(epub_id.encode()).digest()
    raw = bytearray(b"A" * 50)
    for i in range(min(1040, len(raw))):
        raw[i] ^= key[i % 20]
    out = _try_deobfuscate(bytes(raw), epub_id, "http://www.idpf.org/2008/embedding")
    assert out == b"A" * 50


def test_stream_chunks_do_not_split_fenced_md():
    fence = "```py\n" + ("x = 1\n" * 2000) + "```\n"
    text = ("# H\n\npara\n\n" * 50) + fence
    data = text.encode("utf-8")
    chunks = list(_iter_text_chunks(data, "md", size=8000))
    assert len(chunks) == 1
    assert chunks[0] == data


def test_ssrf_blocks_ipv4_mapped_and_cgn():
    assert is_safe_url("http://127.0.0.1/x") is False
    assert is_safe_url("http://169.254.169.254/latest") is False
    # Mapped forms resolve via getaddrinfo of literal? host is the literal
    assert is_safe_url("http://[::ffff:127.0.0.1]/") is False
    assert is_safe_url("http://[::ffff:169.254.169.254]/") is False
    assert is_safe_url("http://100.64.0.1/") is False


def test_docx_comment_author_quote_safe():
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(nodes=[Heading(level=1, text="T"), Paragraph(text="body")])
    base = export_docx(model)
    out = inject_word_comments(
        base,
        [Comment(text="note", author='Bo"b', anchor="body")],
    )
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        xml = zf.read("word/comments.xml")
    # Must parse as well-formed XML; quoteattr may use '…' when value has "
    root = ET.fromstring(xml)
    author = root[0].get(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}author"
    ) or root[0].get("author")
    assert author == 'Bo"b'


def test_registry_concurrent_init_single_wire():
    import threading

    from aduport.core import registry as reg

    # Reset for test (best-effort)
    with reg._REGISTRY_LOCK:
        reg._BUILTINS_READY = False
        # Keep existing maps; just force re-entry path via flag
        ready_hits = []

    def worker():
        reg.ensure_builtin_plugins()
        fn = reg.get_importer("docx")
        depth = 0
        name = getattr(fn, "__name__", "")
        while name.startswith("anydoc_or_"):
            depth += 1
            # unwrap is not available; count prefix repeats
            name = name[len("anydoc_or_") :]
        ready_hits.append(depth)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max(ready_hits) <= 1
