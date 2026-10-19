"""v0.9: mobi export, gzip cache, HTML harden, PDF parallel, warmup."""

from __future__ import annotations

import gzip

from aduport.config import settings
from aduport.core.cache import ResultCache
from aduport.core.converter import SUPPORTED_TARGET_FORMATS, convert
from aduport.core.document_model import Blockquote, Heading, HorizontalRule, ListItem
from aduport.core.warmup import warmup_sync
from aduport.exporters.mobi_exporter import export_mobi
from aduport.importers.html_importer import import_html
from aduport.core.document_model import DocumentModel, Paragraph


def test_mobi_in_targets():
    assert "mobi" in SUPPORTED_TARGET_FORMATS


def test_md_to_mobi():
    out = convert(b"# Book\n\nHello MOBI.\n", "md", "mobi")
    assert b"BOOKMOBI" in out[:128] or b"MOBI" in out
    assert len(out) > 100


def test_mobi_exporter_direct():
    model = DocumentModel(title="T", nodes=[Paragraph(text="hi")])
    data = export_mobi(model)
    assert b"BOOK" in data[60:64] or b"BOOKMOBI" in data[:128]


def test_gzip_cache(monkeypatch):
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "cache_gzip", True)
    monkeypatch.setattr(settings, "cache_gzip_min_bytes", 10)
    c = ResultCache(max_entries=8, ttl_seconds=60)
    payload = b"x" * 100
    key = c.make_key(payload, "md", "txt", "")
    c.put(key, payload)
    got = c.get(key)
    assert got == payload
    # stored form should be smaller when gzip works
    with c._lock:
        stored = c._data[key][1]
        compressed_flag = c._data[key][2]
    assert compressed_flag is True
    assert stored.startswith(b"\x1f\x8b")
    assert len(stored) < len(payload)
    assert gzip.decompress(stored) == payload


def test_html_nested_div_blockquote_hr():
    html = b"""
    <html><body>
      <div><div>
        <h1>Title</h1>
        <div><p>Para1</p></div>
        <blockquote><p>Quote</p></blockquote>
        <hr/>
        <ul><li>one</li><li>two</li></ul>
      </div></div>
    </body></html>
    """
    model = import_html(html, "n.html")
    types = [type(n).__name__ for n in model.nodes]
    assert "Heading" in types
    assert any(isinstance(n, Blockquote) for n in model.nodes)
    assert any(isinstance(n, HorizontalRule) for n in model.nodes)
    items = [n for n in model.nodes if isinstance(n, ListItem)]
    assert len(items) >= 2
    assert any(isinstance(n, Heading) and n.plain_text() == "Title" for n in model.nodes)


def test_html_skips_script():
    html = b"<p>ok</p><script>evil()</script><p>after</p>"
    model = import_html(html)
    texts = " ".join(n.plain_text() if hasattr(n, "plain_text") else "" for n in model.nodes)
    assert "ok" in texts and "after" in texts
    assert "evil" not in texts


def test_warmup_runs():
    # Should not raise even if docling missing
    result = warmup_sync()
    assert result.get("status") in ("done", "already")
