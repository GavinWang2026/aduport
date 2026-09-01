"""md-bridge native document parser (vendored anydoc, abi3).

Re-exports the compiled :mod:`mdbridge_native._doc` extension under a stable
Python API. This is the *local-only* fork of firecrawl/anydoc: the upstream
``ocr="hosted"`` branch that POSTed document bytes to ``api.firecrawl.dev`` has
been removed, so md-bridge never transmits document content off the machine.
All parsing happens in pure Rust with no network calls.
"""

from typing import Literal

from mdbridge_native._doc import (
    Asset,
    Block,
    Cell,
    CellSlot,
    ConvertError,
    Document,
    EncryptedError,
    ImageSource,
    Inline,
    LinkTarget,
    List,
    ListItem,
    MalformedError,
    MissingPartError,
    NeedsOcrError,
    Note,
    ResourceLimitError,
    Style,
    Table,
    UnsupportedError,
    format_from_bytes,
    format_from_extension,
    format_from_path,
    to_document,
    to_markdown,
    to_markdown_bytes,
)

Format = Literal[
    "doc", "docx", "odt", "pdf", "ppt", "pptx", "rtf", "epub", "xlsx", "ods", "odp", "csv"
]
"""Input format, named after the extension that identifies it. Container
variants that share a parser (`.docm`, `.xlsm`, `.ppsx`, ...) map onto these
via `format_from_bytes` or `format_from_extension`."""

__all__ = [
    "Asset",
    "Block",
    "Cell",
    "CellSlot",
    "ConvertError",
    "Document",
    "EncryptedError",
    "Format",
    "ImageSource",
    "Inline",
    "LinkTarget",
    "List",
    "ListItem",
    "MalformedError",
    "MissingPartError",
    "NeedsOcrError",
    "Note",
    "ResourceLimitError",
    "Style",
    "Table",
    "UnsupportedError",
    "format_from_bytes",
    "format_from_extension",
    "format_from_path",
    "to_document",
    "to_markdown",
    "to_markdown_bytes",
]
