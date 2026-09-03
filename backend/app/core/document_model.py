"""Unified intermediate document model (AST) for md-bridge.

All importers produce a ``DocumentModel``; all exporters consume one.
See ``docs/document-model.md`` and ``examples/sdk_demo.py`` (``demo_document_model``).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class InlineMarkType(str, Enum):
    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"
    LINK = "link"
    STRIKETHROUGH = "strikethrough"


class ListMarker(str, Enum):
    """Ordered-list marker kind, as reported by anydoc's document model."""

    BULLET = "bullet"
    DECIMAL = "decimal"
    LOWER_ALPHA = "lower_alpha"
    UPPER_ALPHA = "upper_alpha"
    LOWER_ROMAN = "lower_roman"
    UPPER_ROMAN = "upper_roman"


class InlineMark(BaseModel):
    type: InlineMarkType
    start: int
    end: int
    href: str | None = None


class RichText(BaseModel):
    text: str = ""
    marks: list[InlineMark] = Field(default_factory=list)


class Heading(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: RichText | str
    anchor: str | None = None

    def plain_text(self) -> str:
        if isinstance(self.text, RichText):
            return self.text.text
        return self.text


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: RichText | str

    def plain_text(self) -> str:
        if isinstance(self.text, RichText):
            return self.text.text
        return self.text


class CodeBlock(BaseModel):
    type: Literal["code_block"] = "code_block"
    lang: str = ""
    content: str = ""


class ListItem(BaseModel):
    type: Literal["list_item"] = "list_item"
    text: RichText | str
    ordered: bool = False
    level: int = 0
    # None = ordinary list item; True/False = GFM task list checkbox state
    checked: bool | None = None
    # Extra paragraphs / fenced code within a multi-paragraph list item (V-E).
    children: list[Paragraph | CodeBlock] = Field(default_factory=list)
    # anydoc fidelity extensions: marker kind, ordinal, and source label.
    marker: "ListMarker | None" = None
    number: int | None = None
    marker_label: str | None = None

    def plain_text(self) -> str:
        if isinstance(self.text, RichText):
            return self.text.text
        return self.text


class Table(BaseModel):
    type: Literal["table"] = "table"
    rows: list[list[str]] = Field(default_factory=list)
    # Span-aware cell grid from anydoc (row_span / col_span aware).
    cells: list[list["TableCell"]] = Field(default_factory=list)
    header_rows: int = 0
    kind: str = "data"
    # Spreadsheet sheet / table caption (xlsx round-trip)
    name: str = ""
    # xlsx vertical depth (v8 §B): OOXML col width / row height round-trip
    column_widths: list[float] = Field(default_factory=list)
    row_heights: list[float] = Field(default_factory=list)


class TableCell(BaseModel):
    """One grid cell, carrying span metadata from anydoc's table model."""

    text: str = ""
    row_span: int = 1
    col_span: int = 1
    covered: bool = False
    origin_row: int = 0
    origin_col: int = 0
    # Spreadsheet semantics (values + formula text; pivot/chart as metadata kind).
    formula: str = ""
    cell_kind: Literal["value", "formula", "pivot", "chart"] = "value"


class Image(BaseModel):
    type: Literal["image"] = "image"
    data: bytes
    alt_text: str = ""
    mime_type: str = "image/png"
    url: str | None = None
    origin_part: str | None = None
    # Floating layout hint (export to HTML/PDF CSS; not full Word anchoring).
    wrap: Literal["inline", "left", "right"] = "inline"


class Blockquote(BaseModel):
    type: Literal["blockquote"] = "blockquote"
    paragraphs: list[RichText | str] = Field(default_factory=list)


class HorizontalRule(BaseModel):
    type: Literal["horizontal_rule"] = "horizontal_rule"


class MathBlock(BaseModel):
    type: Literal["math_block"] = "math_block"
    content: str = ""
    display: bool = True


class Footnote(BaseModel):
    type: Literal["footnote"] = "footnote"
    id: str
    text: str = ""


class HtmlBlock(BaseModel):
    """Sanitized HTML fragment preserved for MD round-trip; PDF/DOCX render as text."""

    type: Literal["html_block"] = "html_block"
    content: str = ""


class Comment(BaseModel):
    """Review comment / annotation attached to the document (exported as DOCX comment or end section)."""

    type: Literal["comment"] = "comment"
    author: str = "reviewer"
    text: str = ""
    # Optional anchor text this comment refers to
    anchor: str = ""


class Revision(BaseModel):
    """Track-changes trace (insert/delete) from Word OOXML w:ins / w:del."""

    type: Literal["revision"] = "revision"
    change: Literal["insert", "delete"] = "insert"
    author: str = ""
    text: str = ""
    anchor: str = ""


class ContentControl(BaseModel):
    """Word structured document tag (w:sdt) — dropdown, date, plain/rich text."""

    type: Literal["content_control"] = "content_control"
    tag: str = ""
    alias: str = ""
    control_type: str = ""
    text: str = ""


class PivotTable(BaseModel):
    """Spreadsheet pivot table object (xl/pivotTables/*.xml)."""

    type: Literal["pivot_table"] = "pivot_table"
    name: str = ""
    cache_id: str = ""
    source_range: str = ""
    location: str = ""
    row_fields: list[str] = Field(default_factory=list)
    col_fields: list[str] = Field(default_factory=list)
    data_fields: list[str] = Field(default_factory=list)


class TextBox(BaseModel):
    """Floating text frame (PPTX shape / DOCX txbx)."""

    type: Literal["text_box"] = "text_box"
    text: str = ""
    source: str = ""  # e.g. pptx:slide2, docx:body
    x_mm: float | None = None
    y_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None


# --- Priority AST extensions (corp round-trip ~80% coverage targets) ---


class Section(BaseModel):
    """Document section boundary (maps toward OOXML w:sectPr)."""

    type: Literal["section"] = "section"
    title: str = ""
    # Free-form sectPr-ish props until full OOXML mapping lands
    props: dict[str, Any] = Field(default_factory=dict)

    def plain_text(self) -> str:
        return self.title or "[Section]"


class PageSetup(BaseModel):
    """Page size / orientation / margins for the current section."""

    type: Literal["page_setup"] = "page_setup"
    page_size: str = "A4"
    orientation: Literal["portrait", "landscape"] = "portrait"
    margins_mm: dict[str, float] = Field(default_factory=dict)

    def plain_text(self) -> str:
        return f"[PageSetup {self.page_size} {self.orientation}]"


class HeaderFooter(BaseModel):
    """Header or footer content block."""

    type: Literal["header_footer"] = "header_footer"
    kind: Literal["header", "footer"] = "header"
    text: str = ""

    def plain_text(self) -> str:
        return f"[{self.kind}] {self.text}".strip()


class FieldCode(BaseModel):
    """Word-like field code (PAGE, TOC, REF, …)."""

    type: Literal["field"] = "field"
    field_type: str = ""
    instruction: str = ""
    result_text: str = ""

    def plain_text(self) -> str:
        return self.result_text or self.instruction or f"[Field {self.field_type}]"


class Bookmark(BaseModel):
    """Named bookmark / destination for cross-references."""

    type: Literal["bookmark"] = "bookmark"
    name: str = ""
    anchor: str = ""

    def plain_text(self) -> str:
        return f"[Bookmark {self.name or self.anchor}]"


class RunStyle(BaseModel):
    """Named character/paragraph style definition (catalog entry)."""

    type: Literal["run_style"] = "run_style"
    style_id: str = ""
    name: str = ""
    props: dict[str, Any] = Field(default_factory=dict)

    def plain_text(self) -> str:
        return f"[Style {self.name or self.style_id}]"


class Numbering(BaseModel):
    """Numbering definition (abstract/numId levels) for true list restart."""

    type: Literal["numbering"] = "numbering"
    num_id: str = ""
    abstract_num_id: str = ""
    levels: list[dict[str, Any]] = Field(default_factory=list)

    def plain_text(self) -> str:
        return f"[Numbering {self.num_id or self.abstract_num_id}]"


class Chart(BaseModel):
    """Chart / plot placeholder (spreadsheet or presentation chart object)."""

    type: Literal["chart"] = "chart"
    title: str = ""
    chart_type: str = "bar"  # bar | line | pie | scatter | area | other
    categories: list[str] = Field(default_factory=list)
    series: list[dict[str, Any]] = Field(default_factory=list)
    # Axis titles (xlsx DrawingML catAx/valAx) — v8 §B vertical depth
    x_axis_title: str = ""
    y_axis_title: str = ""
    # Optional embedded preview image
    preview_mime: str = ""
    preview_data: bytes = b""

    def plain_text(self) -> str:
        label = self.title or self.chart_type or "chart"
        n = len(self.series)
        return f"[Chart:{self.chart_type}] {label}" + (f" ({n} series)" if n else "")


class SpeakerNote(BaseModel):
    """PPTX speaker notes (notesSlide) attached to a slide index (1-based content)."""

    type: Literal["speaker_note"] = "speaker_note"
    text: str = ""
    slide_index: int = 1

    def plain_text(self) -> str:
        return self.text or f"[SpeakerNote slide {self.slide_index}]"


class OleObject(BaseModel):
    """Embedded OLE object (progId + optional binary payload)."""

    type: Literal["ole_object"] = "ole_object"
    prog_id: str = ""
    label: str = ""
    relationship_id: str = ""
    # Raw embedding bytes (word/embeddings/oleObjectN.bin); empty → synthesize from label
    data: bytes = b""
    mime_type: str = "application/vnd.openxmlformats-officedocument.oleObject"

    def plain_text(self) -> str:
        return f"[OLE:{self.prog_id or 'object'}] {self.label}".strip()


class SmartArt(BaseModel):
    """DOCX/PPTX SmartArt diagram — text points (+ layout hint for visual rendering)."""

    type: Literal["smartart"] = "smartart"
    # process | list | cycle | hierarchy | other
    layout: str = "process"
    title: str = ""
    items: list[str] = Field(default_factory=list)

    def plain_text(self) -> str:
        label = self.title or self.layout or "diagram"
        joined = " | ".join(self.items[:20])
        return f"[SmartArt:{label}] {joined}".strip()


DocumentNode = Annotated[
    Union[
        Heading,
        Paragraph,
        ListItem,
        Table,
        Image,
        CodeBlock,
        Blockquote,
        HorizontalRule,
        MathBlock,
        Footnote,
        HtmlBlock,
        Comment,
        Revision,
        ContentControl,
        PivotTable,
        TextBox,
        Section,
        PageSetup,
        HeaderFooter,
        FieldCode,
        Bookmark,
        RunStyle,
        Numbering,
        Chart,
        SpeakerNote,
        SmartArt,
        OleObject,
    ],
    Field(discriminator="type"),
]


class DocumentModel(BaseModel):
    title: str = ""
    nodes: list[DocumentNode] = Field(default_factory=list)
    # Free-form conversion metadata (e.g. importer="anydoc", detected_title).
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


def rich_text_from_string(text: str) -> RichText:
    return RichText(text=text)


def ensure_rich_text(value: RichText | str) -> RichText:
    if isinstance(value, RichText):
        return value
    return RichText(text=value)
