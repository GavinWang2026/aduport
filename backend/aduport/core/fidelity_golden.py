"""Structural golden fidelity helpers (v15 §4.2).

Not a pixel/layout OCR golden — compares AST structure counts and key
sentinels after round-trip so regressions cannot hide behind substring tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aduport.core.document_model import (
    DocumentModel,
    Heading,
    Image,
    ListItem,
    Paragraph,
    Table,
)


@dataclass
class StructureFingerprint:
    headings: int = 0
    heading_levels: list[int] = field(default_factory=list)
    paragraphs: int = 0
    list_items: int = 0
    tables: int = 0
    table_rows: int = 0
    images: int = 0
    images_with_data: int = 0
    tokens: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "headings": self.headings,
            "heading_levels": list(self.heading_levels),
            "paragraphs": self.paragraphs,
            "list_items": self.list_items,
            "tables": self.tables,
            "table_rows": self.table_rows,
            "images": self.images,
            "images_with_data": self.images_with_data,
            "tokens": list(self.tokens),
        }


def fingerprint_model(model: DocumentModel, *, token_limit: int = 24) -> StructureFingerprint:
    """Extract structure counts + a few distinctive text tokens from an AST."""
    fp = StructureFingerprint()
    tokens: list[str] = []
    for node in model.nodes:
        if isinstance(node, Heading):
            fp.headings += 1
            fp.heading_levels.append(int(node.level or 1))
            text = getattr(node.text, "text", None) or str(node.text or "")
            if text.strip():
                tokens.append(text.strip()[:80])
        elif isinstance(node, Paragraph):
            fp.paragraphs += 1
            text = getattr(node.text, "text", None) or str(node.text or "")
            if text.strip():
                tokens.append(text.strip()[:80])
        elif isinstance(node, ListItem):
            fp.list_items += 1
        elif isinstance(node, Table):
            fp.tables += 1
            rows = node.rows or []
            if not rows and node.cells:
                rows = [[c.text for c in row] for row in node.cells]
            fp.table_rows += len(rows)
            for row in rows[:3]:
                for cell in row[:4]:
                    if str(cell).strip():
                        tokens.append(str(cell).strip()[:40])
        elif isinstance(node, Image):
            fp.images += 1
            if node.data:
                fp.images_with_data += 1
            if node.alt_text:
                tokens.append(node.alt_text.strip()[:40])
    # Dedup while preserving order
    seen: set[str] = set()
    for t in tokens:
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        fp.tokens.append(t)
        if len(fp.tokens) >= token_limit:
            break
    return fp


@dataclass
class StructureDiff:
    ok: bool
    score: float
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "score": self.score, "issues": list(self.issues)}


def compare_structure(
    expected: StructureFingerprint,
    actual: StructureFingerprint,
    *,
    min_token_hit_ratio: float = 0.5,
    allow_missing_images: bool = False,
) -> StructureDiff:
    """Compare two fingerprints; return score in [0,1] and issue list."""
    issues: list[str] = []
    checks = 0
    hits = 0

    def _ratio(a: int, b: int, name: str, *, soft: bool = False) -> None:
        nonlocal checks, hits
        checks += 1
        if a == 0 and b == 0:
            hits += 1
            return
        denom = max(a, 1)
        if b >= a or (a > 0 and b / denom >= 0.7):
            hits += 1
            return
        if soft and b > 0:
            hits += 0.5
            issues.append(f"{name}: expected≥{a} got {b} (partial)")
        else:
            issues.append(f"{name}: expected≥{a} got {b}")

    _ratio(expected.headings, actual.headings, "headings")
    _ratio(expected.list_items, actual.list_items, "list_items", soft=True)
    _ratio(expected.tables, actual.tables, "tables")
    _ratio(expected.table_rows, actual.table_rows, "table_rows", soft=True)
    if not allow_missing_images:
        _ratio(expected.images_with_data, actual.images_with_data, "images_with_data", soft=True)

    token_hits = 0
    for tok in expected.tokens:
        if any(tok.lower() in t.lower() or t.lower() in tok.lower() for t in actual.tokens):
            token_hits += 1
    token_ratio = 1.0 if not expected.tokens else token_hits / len(expected.tokens)
    checks += 1
    if token_ratio >= min_token_hit_ratio:
        hits += 1
    else:
        issues.append(
            f"tokens: hit {token_hits}/{len(expected.tokens)} ({token_ratio:.0%} < {min_token_hit_ratio:.0%})"
        )

    score = float(hits) / float(checks) if checks else 1.0
    # Soft/partial issues still allow ok when overall score clears the bar.
    return StructureDiff(ok=score >= 0.85, score=round(score, 3), issues=issues)


def golden_roundtrip_md(
    source_md: bytes,
    *,
    via: str = "docx",
    min_score: float = 0.85,
) -> StructureDiff:
    """MD → ``via`` → MD structural golden check."""
    from aduport.core.converter import convert
    from aduport.importers.md_importer import import_md

    src_model = import_md(source_md)
    expected = fingerprint_model(src_model)
    mid = convert(source_md, "md", via, filename="golden.md")
    back = convert(mid, via, "md", filename=f"golden.{via}")
    actual_model = import_md(back)
    actual = fingerprint_model(actual_model)
    diff = compare_structure(expected, actual, allow_missing_images=via in {"txt", "csv"})
    if diff.score < min_score:
        diff.ok = False
        if f"score {diff.score} < {min_score}" not in diff.issues:
            diff.issues.append(f"score {diff.score} < {min_score}")
    elif not diff.issues:
        diff.ok = True
    return diff
