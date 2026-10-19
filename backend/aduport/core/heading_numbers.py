"""Apply hierarchical heading numbers (1 / 1.1 / 1.1.1) onto Heading nodes."""

from __future__ import annotations

from aduport.core.document_model import DocumentModel, Heading, ensure_rich_text


def apply_heading_numbers(model: DocumentModel) -> DocumentModel:
    """Return a shallow-copied model with numbered heading plain text."""
    out = model.model_copy(deep=True)
    counters = [0] * 7  # index by level 1..6
    for i, node in enumerate(out.nodes):
        if not isinstance(node, Heading):
            continue
        level = max(1, min(node.level, 6))
        counters[level] += 1
        for lv in range(level + 1, 7):
            counters[lv] = 0
        parts = [str(counters[lv]) for lv in range(1, level + 1) if counters[lv] > 0]
        num = ".".join(parts)
        rich = ensure_rich_text(node.text)
        new_text = f"{num} {rich.text}".strip()
        # shift marks
        shift = len(num) + 1
        marks = []
        for m in rich.marks:
            marks.append(m.model_copy(update={"start": m.start + shift, "end": m.end + shift}))
        out.nodes[i] = Heading(level=level, text=rich.model_copy(update={"text": new_text, "marks": marks}))
    return out
