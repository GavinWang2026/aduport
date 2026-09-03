"""CI contract: A/B benchmarks must disable cache and respect thresholds."""

from __future__ import annotations

from app.config import settings
from app.core.document_model import InlineMarkType
from app.importers.md_importer import import_md


def test_ab_contract_doc_exists():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert (root / "enterprise" / "AB_TEST_CONTRACT.md").is_file()


def test_ab_fixture_disables_cache(ab_benchmark):
    assert settings.cache_enabled is False


def test_v_a_nested_span_chinese_md_roundtrip(ab_benchmark):
    """D-01: nested CJK marks survive md → AST → md → AST (exporter emitter)."""
    from app.exporters.md_exporter import export_md

    cases = [
        "**粗体里套*斜体*结束**",
        "*斜体里套**粗体**结束*",
        "**粗体里套`代码`结束**",
        "~~删除**粗体**结束~~",
        "[**粗链接**](https://e.com)",
        "**粗*斜`码`斜*粗**",
    ]
    for src in cases:
        m1 = import_md((src + "\n").encode())
        out = export_md(m1)
        if isinstance(out, bytes):
            out = out.decode()
        m2 = import_md(out.encode())
        r1, r2 = m1.nodes[0].text, m2.nodes[0].text
        t1 = {(m.type, m.start, m.end) for m in r1.marks}
        t2 = {(m.type, m.start, m.end) for m in r2.marks}
        assert r1.text == r2.text, f"{src!r}: text {r1.text!r} -> {r2.text!r}; export={out!r}"
        assert t1 == t2, f"{src!r}: marks {t1} -> {t2}; export={out!r}"


def test_v_a_nested_span_chinese_cases_preserved(ab_benchmark):
    """Report V-A cases that previously collapsed nested marks (D-01)."""
    cases = [
        ("**粗体里套*斜体*结束**", {InlineMarkType.BOLD, InlineMarkType.ITALIC}),
        ("*斜体里套**粗体**结束*", {InlineMarkType.BOLD, InlineMarkType.ITALIC}),
        ("**粗体里套`代码`结束**", {InlineMarkType.BOLD, InlineMarkType.CODE}),
        ("~~删除**粗体**结束~~", {InlineMarkType.STRIKETHROUGH, InlineMarkType.BOLD}),
        ("[**粗链接**](https://e.com)", {InlineMarkType.LINK, InlineMarkType.BOLD}),
        ("**粗*斜`码`斜*粗**", {InlineMarkType.BOLD, InlineMarkType.ITALIC, InlineMarkType.CODE}),
    ]
    for src, need in cases:
        model = import_md((src + "\n").encode())
        assert model.nodes, src
        rich = model.nodes[0].text
        types = {m.type for m in rich.marks}
        missing = need - types
        assert not missing, f"{src!r} missing {missing}; got {types}"
