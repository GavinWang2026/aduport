"""Fast DOCX body builders via raw OXML (avoids python-docx per-paragraph style cost)."""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


class FastBody:
    """
    Buffer plain/heading/bullet paragraphs, then flush with O(1) appends.

    Keeps ``w:sectPr`` intact for python-docx APIs (tables/images/sections).
    Flush before any ``doc.add_*`` that needs sections.
    """

    def __init__(self, body) -> None:
        self.body = body
        self._buf: list = []

    def flush(self) -> None:
        if not self._buf:
            return
        sect = self.body.find(qn("w:sectPr"))
        if sect is not None:
            self.body.remove(sect)
        for el in self._buf:
            self.body.append(el)
        self._buf.clear()
        if sect is not None:
            self.body.append(sect)

    def finish(self) -> None:
        self.flush()

    def plain(self, text: str) -> None:
        self._buf.append(self._para(text))

    def heading(self, text: str, level: int) -> None:
        level = max(1, min(int(level), 9))
        p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        pStyle = OxmlElement("w:pStyle")
        pStyle.set(qn("w:val"), f"Heading{level}")
        pPr.append(pStyle)
        p.append(pPr)
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.text = text.replace("\n", " ")
        r.append(t)
        p.append(r)
        self._buf.append(p)

    def bullet(self, text: str, *, level: int = 0, checked: bool | None = None) -> None:
        if checked is True:
            prefix = "☑ "
        elif checked is False:
            prefix = "☐ "
        else:
            prefix = "• "
        indent = max(0, min(level, 8)) * 360
        p = OxmlElement("w:p")
        if indent:
            pPr = OxmlElement("w:pPr")
            ind = OxmlElement("w:ind")
            ind.set(qn("w:left"), str(indent))
            pPr.append(ind)
            p.append(pPr)
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = (prefix + text).replace("\n", " ")
        r.append(t)
        p.append(r)
        self._buf.append(p)

    @staticmethod
    def _para(text: str):
        p = OxmlElement("w:p")
        r = OxmlElement("w:r")
        t = OxmlElement("w:t")
        if text.startswith(" ") or text.endswith(" "):
            t.set(qn("xml:space"), "preserve")
        t.text = text.replace("\n", " ")
        r.append(t)
        p.append(r)
        return p
