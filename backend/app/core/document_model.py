"""Unified intermediate document model (AST) for md-bridge."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class InlineMarkType(str, Enum):
    BOLD = "bold"
    ITALIC = "italic"
    CODE = "code"
    LINK = "link"
    STRIKETHROUGH = "strikethrough"


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


class ListItem(BaseModel):
    type: Literal["list_item"] = "list_item"
    text: RichText | str
    ordered: bool = False
    level: int = 0
    # None = ordinary list item; True/False = GFM task list checkbox state
    checked: bool | None = None
    # Extra paragraphs / nested content within a multi-paragraph list item
    children: list[Paragraph] = Field(default_factory=list)

    def plain_text(self) -> str:
        if isinstance(self.text, RichText):
            return self.text.text
        return self.text


class Table(BaseModel):
    type: Literal["table"] = "table"
    rows: list[list[str]]


class Image(BaseModel):
    type: Literal["image"] = "image"
    data: bytes
    alt_text: str = ""
    mime_type: str = "image/png"


class CodeBlock(BaseModel):
    type: Literal["code_block"] = "code_block"
    lang: str = ""
    content: str = ""


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
    ],
    Field(discriminator="type"),
]


class DocumentModel(BaseModel):
    title: str = ""
    nodes: list[DocumentNode] = Field(default_factory=list)
    # Non-body metadata (e.g. docx_theme, docx_header) — export-optional
    metadata: dict[str, str] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}


def rich_text_from_string(text: str) -> RichText:
    return RichText(text=text)


def ensure_rich_text(value: RichText | str) -> RichText:
    if isinstance(value, RichText):
        return value
    return RichText(text=value)
