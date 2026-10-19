"""Drop a list marker that was copied into the item text.

PowerPoint placeholders are already bullets. Writers that also store ``- ``
in the run make ``ppsm→md`` render ``- \\- ECHO`` and HTML ``<li>- ECHO</li>``.
Only a leading marker is removed. A hyphen inside the words stays.
"""

from __future__ import annotations

import re

from aduport.core.document_model import RichText

_UNORDERED = re.compile(r"^(?:\\)?(?:[-*+]\s+|[•·▪◦‣]\s*)")
_ORDERED = re.compile(r"^(?:\\)?\d{1,3}[.)]\s+")


def strip_redundant_list_prefix(text: str, *, ordered: bool) -> str:
    raw = text or ""
    pattern = _ORDERED if ordered else _UNORDERED
    stripped = pattern.sub("", raw, count=1)
    if not stripped.strip() or stripped == raw:
        return raw
    return stripped


def rich_without_redundant_list_prefix(rich: RichText, *, ordered: bool) -> RichText:
    new = strip_redundant_list_prefix(rich.text or "", ordered=ordered)
    if new == (rich.text or ""):
        return rich
    if not (rich.text or "").endswith(new):
        return rich
    cut = len(rich.text) - len(new)
    marks = []
    for mark in rich.marks or []:
        end = mark.end - cut
        if end <= 0:
            continue
        marks.append(mark.model_copy(update={"start": max(0, mark.start - cut), "end": end}))
    return rich.model_copy(update={"text": new, "marks": marks})
