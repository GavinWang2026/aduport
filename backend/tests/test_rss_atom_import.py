"""RSS / Atom feed import."""

from __future__ import annotations

from aduport.core.converter import convert
from aduport.core.format_detect import detect_source_format
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.importers.rss_importer import feed_to_markdown, import_rss, looks_like_feed

_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Demo Feed</title>
    <description>Channel &lt;b&gt;desc&lt;/b&gt;</description>
    <item>
      <title>First Post</title>
      <link>https://example.com/1</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>
      <description>&lt;p&gt;Hello &lt;em&gt;world&lt;/em&gt;&lt;/p&gt;</description>
    </item>
  </channel>
</rss>
"""

_ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Demo</title>
  <subtitle>sub</subtitle>
  <entry>
    <title>Entry One</title>
    <updated>2024-02-01T12:00:00Z</updated>
    <link href="https://example.com/e1" rel="alternate"/>
    <summary type="text">Plain summary</summary>
    <content type="html">&lt;p&gt;Body&lt;/p&gt;</content>
  </entry>
</feed>
"""


def test_looks_like_feed_and_detect():
    assert looks_like_feed(_RSS)
    assert looks_like_feed(_ATOM)
    assert detect_source_format("a.rss", _RSS) == "rss"
    assert detect_source_format("a.atom", _ATOM) == "atom"
    assert detect_source_format("a.xml", _RSS) == "rss"


def test_import_rss_markdown():
    md, title, kind = feed_to_markdown(_RSS)
    assert kind == "rss"
    assert title == "Demo Feed"
    assert "First Post" in md
    assert "Hello" in md
    model = import_rss(_RSS, "demo.rss")
    assert model.metadata.get("feed_type") == "rss"
    assert model.title == "Demo Feed"
    out = convert(_RSS, "rss", "md", filename="demo.rss")
    assert b"First Post" in out


def test_import_atom():
    md, title, kind = feed_to_markdown(_ATOM)
    assert kind == "atom"
    assert title == "Atom Demo"
    assert "Entry One" in md
    out = convert(_ATOM, "atom", "md", filename="demo.atom")
    assert b"Entry One" in out or b"Atom Demo" in out


def test_registry_rss_atom():
    ensure_builtin_plugins()
    assert get_importer("rss") is not None
    assert get_importer("atom") is not None
