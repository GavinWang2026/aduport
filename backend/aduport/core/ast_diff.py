"""Structural diff of two DocumentModel trees (not token Jaccard)."""

from __future__ import annotations

import difflib
from collections import Counter

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    DocumentModel,
    Footnote,
    Heading,
    ListItem,
    Paragraph,
    Revision,
    Table,
)


def _plain(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "plain_text"):
        return str(value.plain_text() or "").strip()
    text = getattr(value, "text", value)
    if hasattr(text, "text"):
        return str(text.text or "").strip()
    return str(text or "").strip()


def node_signature(node) -> tuple[str, str]:
    """Return ``(kind, detail)`` stable enough for a semantic diff."""
    if isinstance(node, Heading):
        return ("heading", f"h{node.level}:{_plain(node)}")
    if isinstance(node, CodeBlock):
        return ("code", f"{node.lang}:{node.content}")
    if isinstance(node, Blockquote):
        body = " | ".join(_plain(p) for p in node.paragraphs)
        return ("blockquote", body)
    if isinstance(node, ListItem):
        mark = "ol" if node.ordered else "ul"
        return ("list", f"{mark}:{node.level}:{_plain(node)}")
    if isinstance(node, Table):
        rows = " / ".join(" | ".join(str(c) for c in row) for row in (node.rows or []))
        return ("table", rows)
    if isinstance(node, Footnote):
        return ("footnote", f"{node.id}:{node.text}")
    if isinstance(node, Comment):
        return ("comment", f"{node.author}|{node.anchor}|{node.text}")
    if isinstance(node, Revision):
        return ("revision", f"{node.change}|{node.author}|{node.anchor}|{node.text}")
    if isinstance(node, Paragraph):
        return ("paragraph", _plain(node))
    kind = str(getattr(node, "type", type(node).__name__))
    return (kind, _plain(node)[:500])


def _review_rows(model: DocumentModel) -> tuple[list[dict], list[dict]]:
    comments: list[dict] = []
    revisions: list[dict] = []
    for node in model.nodes:
        if isinstance(node, Comment):
            comments.append(
                {"author": node.author or "", "anchor": node.anchor or "", "text": node.text or ""}
            )
        elif isinstance(node, Revision):
            revisions.append(
                {
                    "change": node.change,
                    "author": node.author or "",
                    "anchor": node.anchor or "",
                    "text": node.text or "",
                }
            )
    return comments, revisions


def _bag_delta(before: list[dict], after: list[dict]) -> tuple[list[dict], list[dict], int]:
    def key(item: dict) -> tuple:
        return tuple(sorted((str(k), str(v)) for k, v in item.items()))

    left = Counter(key(item) for item in before)
    right = Counter(key(item) for item in after)
    added: list[dict] = []
    removed: list[dict] = []
    for item, count in right.items():
        extra = count - left[item]
        if extra > 0:
            added.extend(dict(item) for _ in range(extra))
    for item, count in left.items():
        extra = count - right[item]
        if extra > 0:
            removed.extend(dict(item) for _ in range(extra))
    unchanged = sum(min(left[item], right[item]) for item in left)
    return added, removed, unchanged


def review_delta(left: DocumentModel, right: DocumentModel) -> dict:
    """Comment and tracked-change delta. Not a Word accept/reject session."""
    left_comments, left_revisions = _review_rows(left)
    right_comments, right_revisions = _review_rows(right)
    comments_added, comments_removed, comments_same = _bag_delta(
        left_comments, right_comments
    )
    revisions_added, revisions_removed, revisions_same = _bag_delta(
        left_revisions, right_revisions
    )
    return {
        "comments_added": comments_added,
        "comments_removed": comments_removed,
        "comments_unchanged": comments_same,
        "revisions_added": revisions_added,
        "revisions_removed": revisions_removed,
        "revisions_unchanged": revisions_same,
    }


def diff_models(left: DocumentModel, right: DocumentModel) -> dict:
    """Opcode diff over AST node signatures, plus a title comparison."""
    left_nodes = [node_signature(n) for n in left.nodes]
    right_nodes = [node_signature(n) for n in right.nodes]
    left_keys = [f"{k}|{d}" for k, d in left_nodes]
    right_keys = [f"{k}|{d}" for k, d in right_nodes]
    matcher = difflib.SequenceMatcher(a=left_keys, b=right_keys, autojunk=False)
    changes: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "op": tag,
                "path": f"nodes[{i1}:{i2}]",
                "before": [{"kind": k, "detail": d} for k, d in left_nodes[i1:i2]],
                "after": [{"kind": k, "detail": d} for k, d in right_nodes[j1:j2]],
            }
        )
    title_before = (left.title or "").strip()
    title_after = (right.title or "").strip()
    title_changed = title_before != title_after
    review = review_delta(left, right)
    review_changed = any(
        review[name]
        for name in (
            "comments_added",
            "comments_removed",
            "revisions_added",
            "revisions_removed",
        )
    )
    return {
        "equal": not changes and not title_changed and not review_changed,
        "approximateOnly": False,
        "title": {"before": title_before, "after": title_after, "changed": title_changed},
        "left_nodes": len(left_nodes),
        "right_nodes": len(right_nodes),
        "changes": changes,
        "review": review,
    }
