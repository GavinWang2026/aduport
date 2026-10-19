"""Fast DOCX body builders via raw OXML (avoids python-docx per-paragraph style cost)."""

from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _ensure_numbering(doc) -> tuple[int, int]:
    """Ensure abstract numbering for bullets (0) and decimals (1); return (bullet_id, number_id)."""
    numbering = doc.part.numbering_part.numbering_definitions._numbering
    # The default template does not use numId 1/2 as bullet/decimal.
    # Pick ids by the abstract numFmt so ordered lists stay ordered.
    existing = numbering.xpath("./w:num")
    if existing:
        found = _numbering_ids_by_format(numbering)
        if found is not None:
            return found
        return 1, 2

    def _abstract(abstract_id: int, kind: str) -> None:
        abs_n = OxmlElement("w:abstractNum")
        abs_n.set(qn("w:abstractNumId"), str(abstract_id))
        multi = OxmlElement("w:multiLevelType")
        multi.set(qn("w:val"), "hybridMultilevel")
        abs_n.append(multi)
        for ilvl in range(9):
            lvl = OxmlElement("w:lvl")
            lvl.set(qn("w:ilvl"), str(ilvl))
            start = OxmlElement("w:start")
            start.set(qn("w:val"), "1")
            lvl.append(start)
            num_fmt = OxmlElement("w:numFmt")
            num_fmt.set(qn("w:val"), "bullet" if kind == "bullet" else "decimal")
            lvl.append(num_fmt)
            lvl_text = OxmlElement("w:lvlText")
            if kind == "bullet":
                lvl_text.set(qn("w:val"), "•")
            else:
                lvl_text.set(qn("w:val"), f"%{ilvl + 1}.")
            lvl.append(lvl_text)
            abs_n.append(lvl)
        numbering.append(abs_n)

    def _num(num_id: int, abstract_id: int) -> None:
        num = OxmlElement("w:num")
        num.set(qn("w:numId"), str(num_id))
        abs_ref = OxmlElement("w:abstractNumId")
        abs_ref.set(qn("w:val"), str(abstract_id))
        num.append(abs_ref)
        numbering.append(num)

    _abstract(0, "bullet")
    _abstract(1, "decimal")
    _num(1, 0)
    _num(2, 1)
    return 1, 2


def _numbering_ids_by_format(numbering) -> tuple[int, int] | None:
    formats: dict[str, str] = {}
    for abstract in numbering.findall(qn("w:abstractNum")):
        abstract_id = abstract.get(qn("w:abstractNumId"))
        if abstract_id is None:
            continue
        for lvl in abstract.findall(qn("w:lvl")):
            if (lvl.get(qn("w:ilvl")) or "0") != "0":
                continue
            fmt = lvl.find(qn("w:numFmt"))
            formats[abstract_id] = (fmt.get(qn("w:val")) if fmt is not None else "") or ""
            break
    bullet_id = number_id = None
    for num in numbering.findall(qn("w:num")):
        raw_id = num.get(qn("w:numId"))
        ref = num.find(qn("w:abstractNumId"))
        if not raw_id or not raw_id.isdigit() or ref is None:
            continue
        kind = formats.get(ref.get(qn("w:val")) or "", "")
        if kind == "bullet" and bullet_id is None:
            bullet_id = int(raw_id)
        elif kind == "decimal" and number_id is None:
            number_id = int(raw_id)
    if bullet_id is None or number_id is None:
        return None
    return bullet_id, number_id


class FastBody:
    """
    Buffer plain/heading/bullet paragraphs, then flush with O(1) appends.

    Keeps ``w:sectPr`` intact for python-docx APIs (tables/images/sections).
    Flush before any ``doc.add_*`` that needs sections.
    """

    def __init__(self, body, doc=None) -> None:
        self.body = body
        self.doc = doc
        self._buf: list = []
        self._bullet_num_id = 1
        self._number_num_id = 2
        if doc is not None:
            try:
                self._bullet_num_id, self._number_num_id = _ensure_numbering(doc)
            except Exception:
                pass

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

    def bullet(
        self,
        text: str,
        *,
        level: int = 0,
        checked: bool | None = None,
        ordered: bool = False,
    ) -> None:
        """Emit a true Word list paragraph with ``w:numPr`` (not a glyph prefix)."""
        if checked is True:
            prefix = "[x] "
        elif checked is False:
            prefix = "[ ] "
        else:
            prefix = ""
        ilvl = max(0, min(int(level), 8))
        num_id = self._number_num_id if ordered else self._bullet_num_id
        p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        numPr = OxmlElement("w:numPr")
        ilvl_el = OxmlElement("w:ilvl")
        ilvl_el.set(qn("w:val"), str(ilvl))
        numId_el = OxmlElement("w:numId")
        numId_el.set(qn("w:val"), str(num_id))
        numPr.append(ilvl_el)
        numPr.append(numId_el)
        pPr.append(numPr)
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
