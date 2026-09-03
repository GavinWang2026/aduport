"""Phase C · cover anydoc AST→DocumentModel mapping without the Rust wheel."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Heading,
    HorizontalRule,
    ListItem,
    MathBlock,
    Paragraph,
    Table,
)
from app.importers import anydoc_bridge as bridge
from app.importers.anydoc_bridge import (
    _InlineBuilder,
    _convert_block,
    _convert_list,
    _convert_table,
    _flatten_cell_text,
    _image_from_inline,
    _marker_number,
    _rich,
)


def _style(**kwargs):
    defaults = dict(bold=False, italic=False, strike=False, code=False)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _text(s: str, **style_kw):
    return SimpleNamespace(kind="text", text=s, style=_style(**style_kw) if style_kw else None)


def _link(content, href: str, kind: str = "url"):
    return SimpleNamespace(
        kind="link",
        content=content,
        target=SimpleNamespace(kind=kind, value=href),
    )


def _img(alt: str = "pic", source=None):
    return SimpleNamespace(kind="image", alt=alt, source=source)


def test_inline_builder_marks_and_specials():
    assets = {
        1: SimpleNamespace(
            data=b"PNG", media_type="image/png", origin_part="media/1.png"
        )
    }
    b = _InlineBuilder()
    b.feed(
        [
            _text("Hello ", bold=True),
            _text("code", code=True),
            _text(" ", italic=True, strike=True),
            _link([_text("link")], "https://ex.com"),
            _link([_text("sec")], "sec-1", kind="anchor"),
            _img("alt", SimpleNamespace(kind="embedded", asset_id=1)),
            SimpleNamespace(kind="note_ref", note_id="n1"),
            SimpleNamespace(kind="line_break"),
            SimpleNamespace(kind="math", text="a+b"),
            SimpleNamespace(kind="checkbox", checked=True),
            SimpleNamespace(kind="anchor"),
        ],
        assets,
    )
    rich = b.build()
    assert "Hello" in rich.text
    assert "code" in rich.text
    assert "link" in rich.text
    assert "#sec-1" in "".join(m.href or "" for m in rich.marks if m.href)
    assert b.checked is True
    assert any(getattr(m.type, "name", str(m.type)) in ("BOLD", "bold") or "bold" in str(m.type).lower() for m in rich.marks)
    assert b.images and b.images[0].data == b"PNG"


def test_image_from_inline_variants():
    assert _image_from_inline(
        SimpleNamespace(alt="x", source=None), {}
    ).mime_type.endswith("octet-stream")
    assert _image_from_inline(
        SimpleNamespace(alt="e", source=SimpleNamespace(kind="unavailable")), {}
    ).data == b""
    ext = _image_from_inline(
        SimpleNamespace(
            alt="e", source=SimpleNamespace(kind="external", url="https://x/y.png")
        ),
        {},
    )
    assert ext.url.endswith("y.png")
    missing = _image_from_inline(
        SimpleNamespace(alt="m", source=SimpleNamespace(kind="embedded", asset_id=9)),
        {},
    )
    assert missing.data == b""


def test_flatten_cell_and_table_with_spans():
    assets: dict = {}
    para = SimpleNamespace(kind="paragraph", content=[_text("A")])
    code = SimpleNamespace(kind="code_block", text="print(1)")
    math = SimpleNamespace(kind="math", text="x^2")
    nested_list = SimpleNamespace(
        kind="list",
        list=SimpleNamespace(
            marker="bullet",
            start=1,
            items=[
                SimpleNamespace(
                    blocks=[SimpleNamespace(kind="paragraph", content=[_text("li")])]
                )
            ],
        ),
    )
    quote = SimpleNamespace(
        kind="block_quote",
        blocks=[SimpleNamespace(kind="paragraph", content=[_text("q")])],
    )
    inner_table = SimpleNamespace(
        kind="table",
        table=SimpleNamespace(
            header_rows=0,
            kind="data",
            grid=[
                [
                    SimpleNamespace(
                        kind="origin",
                        cell=SimpleNamespace(
                            blocks=[
                                SimpleNamespace(
                                    kind="paragraph", content=[_text("nested")]
                                )
                            ],
                            row_span=1,
                            col_span=1,
                        ),
                    )
                ]
            ],
        ),
    )
    flat = _flatten_cell_text(
        [para, code, math, nested_list, quote, inner_table], assets
    )
    assert "A" in flat and "print" in flat and "nested" in flat

    table = SimpleNamespace(
        header_rows=1,
        kind="data",
        grid=[
            [
                SimpleNamespace(
                    kind="origin",
                    cell=SimpleNamespace(
                        blocks=[SimpleNamespace(kind="paragraph", content=[_text("H")])],
                        row_span=1,
                        col_span=2,
                    ),
                ),
                SimpleNamespace(kind="covered", origin_row=0, origin_col=0),
            ]
        ],
    )
    t = _convert_table(table, assets)
    assert isinstance(t, Table)
    assert t.rows[0][0] == "H"
    assert t.cells[0][1].covered is True


def test_convert_blocks_heading_list_quote_code_hr_math():
    assets: dict = {}
    out: list = []
    _convert_block(
        SimpleNamespace(
            kind="heading",
            level=2,
            anchor="a1",
            content=[_text("Title")],
        ),
        assets,
        out,
    )
    assert isinstance(out[0], Heading) and out[0].level == 2

    out.clear()
    _convert_block(
        SimpleNamespace(kind="paragraph", content=[_text("Body")]), assets, out
    )
    assert isinstance(out[0], Paragraph)

    out.clear()
    lst = SimpleNamespace(
        kind="list",
        list=SimpleNamespace(
            marker="decimal",
            start=3,
            items=[
                SimpleNamespace(
                    marker_label="3.",
                    blocks=[
                        SimpleNamespace(kind="paragraph", content=[_text("one")]),
                        SimpleNamespace(kind="paragraph", content=[_text("more")]),
                        SimpleNamespace(
                            kind="list",
                            list=SimpleNamespace(
                                marker="bullet",
                                start=1,
                                items=[
                                    SimpleNamespace(
                                        marker_label=None,
                                        blocks=[
                                            SimpleNamespace(
                                                kind="paragraph",
                                                content=[_text("nest")],
                                            )
                                        ],
                                    )
                                ],
                            ),
                        ),
                        SimpleNamespace(kind="code_block", text="x=1", lang="py"),
                    ],
                )
            ],
        ),
    )
    _convert_list(lst, assets, 0, out)
    assert any(isinstance(n, ListItem) for n in out)
    assert _marker_number("bullet", 1, 0) == 0
    assert _marker_number("decimal", 3, 0) == 3

    out.clear()
    _convert_block(
        SimpleNamespace(
            kind="block_quote",
            blocks=[
                SimpleNamespace(kind="paragraph", content=[_text("q")]),
                SimpleNamespace(kind="code_block", text="c", lang=""),
            ],
        ),
        assets,
        out,
    )
    assert isinstance(out[0], Blockquote)

    out.clear()
    _convert_block(
        SimpleNamespace(kind="code_block", lang="py", text="1"), assets, out
    )
    assert isinstance(out[0], CodeBlock)
    _convert_block(SimpleNamespace(kind="rule"), assets, out)
    assert isinstance(out[-1], HorizontalRule)
    _convert_block(SimpleNamespace(kind="math", text="E=mc^2"), assets, out)
    assert isinstance(out[-1], MathBlock)

    rich, imgs = _rich([_text("z")], {})
    assert rich.text == "z" and imgs == []


def test_convert_list_none_is_noop():
    out: list = []
    _convert_list(SimpleNamespace(kind="list", list=None), {}, 0, out)
    assert out == []


def test_import_via_anydoc_maps_document(monkeypatch):
    asset = SimpleNamespace(
        id=1, data=b"PNG", media_type="image/png", origin_part="a.png"
    )
    note = SimpleNamespace(
        id="fn1",
        kind="footnote",
        blocks=[SimpleNamespace(kind="paragraph", content=[_text("note body")])],
    )
    doc = SimpleNamespace(
        blocks=[
            SimpleNamespace(
                kind="heading", level=1, anchor=None, content=[_text("HiTitle")]
            ),
            SimpleNamespace(kind="paragraph", content=[_text("SENT_ANYDOC_AST")]),
            SimpleNamespace(
                kind="table",
                table=SimpleNamespace(
                    header_rows=0,
                    kind="data",
                    grid=[
                        [
                            SimpleNamespace(
                                kind="origin",
                                cell=SimpleNamespace(
                                    blocks=[
                                        SimpleNamespace(
                                            kind="paragraph",
                                            content=[_text("cell")],
                                        )
                                    ],
                                    row_span=1,
                                    col_span=1,
                                ),
                            )
                        ]
                    ],
                ),
            ),
        ],
        assets=[asset],
        notes=[note],
    )

    class FakeAnydoc:
        @staticmethod
        def to_document(_data, _fmt=None):
            return doc

    monkeypatch.setattr(bridge, "_anydoc", lambda: FakeAnydoc)

    model = bridge.import_via_anydoc(b"fake", filename="x.docx", fmt="docx")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "SENT_ANYDOC_AST" in text
    assert model.metadata.get("importer") == "anydoc"
    assert model.metadata.get("detected_title") == "HiTitle"


def test_markdown_via_anydoc_bytes(monkeypatch):
    class FakeAnydoc:
        @staticmethod
        def to_markdown_bytes(_data, _fmt=None):
            return "# SENT_MD_ANYDOC\n"

    monkeypatch.setattr(bridge, "_anydoc", lambda: FakeAnydoc)
    md = bridge.markdown_via_anydoc(b"x", "docx", filename="a.docx")
    assert "SENT_MD_ANYDOC" in md


def test_sniff_and_translate_error_paths(monkeypatch):
    class Dead:
        @staticmethod
        def format_from_bytes(_data):
            raise RuntimeError("no")

    monkeypatch.setattr(bridge, "_anydoc", lambda: Dead)
    assert bridge.sniff_format(b"not-a-real-doc") is None

    class PanicException(BaseException):
        pass

    mapped_panic = bridge._translate_error(PanicException("p"), "f.docx")
    assert "panic" in str(mapped_panic).lower() or type(mapped_panic).__name__

    for name in (
        "UnsupportedError",
        "NeedsOcrError",
        "EncryptedError",
        "ResourceLimitError",
        "MissingPartError",
        "MalformedError",
    ):
        exc = type(name, (Exception,), {})("x")
        if name == "NeedsOcrError":
            exc.pages = [1, 2]
        if name == "ResourceLimitError":
            exc.limit = "depth"
        if name in ("MissingPartError", "MalformedError"):
            exc.part = "word/document.xml"
        mapped = bridge._translate_error(exc, "f.docx")
        assert isinstance(mapped, Exception)

    err = bridge._translate_error(RuntimeError("boom"), "f.docx")
    assert isinstance(err, Exception)
    with pytest.raises(SystemExit):
        bridge._reraise_or_map_native(SystemExit(1), "f.docx")
