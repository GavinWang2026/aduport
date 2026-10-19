"""AduPort — local document bidirectional converter (Python SDK).

This module is the **stable public API surface** for third-party developers.
Prefer importing from here rather than reaching into ``aduport.core.*`` internals::

    from aduport import convert, SUPPORTED_SOURCE_FORMATS, capabilities

    result: bytes = convert(source_bytes, "docx", "md", filename="a.docx")

Installing via pip (package ``aduport``) exposes the same importable ``app``
namespace, so the snippets above work verbatim after ``pip install aduport``.

Stability contract
------------------
- **Stable** (SemVer-protected): ``convert``, ``convert_directory``, the
  format constants (``EXPORT_MODES`` is the rag export mode, not a file
  format; ``TARGET_STRUCTURE_LIMITS`` is empty while our own pdf/csv/txt/rtf
  round-trip keeps headings, tables, and images), ``BatchResult`` / ``BatchItemResult`` (the return contract
  of ``convert_directory``), ``capabilities`` / ``tier`` (fixed key set), and
  every exception class.
- **Stable, additive growth**: ``DocumentModel`` — the class and its existing
  fields are safe to depend on; the AST node-type set grows with format
  support, and additions are considered additive rather than breaking.

Public symbols are exported lazily via :pep:`562` ``__getattr__`` so that a bare
``import aduport`` / ``from aduport import __version__`` stays lightweight and never
pulls in heavyweight dependencies such as weasyprint or python-docx.
"""

from __future__ import annotations

import importlib

__version__ = "0.16.0"

__all__ = [
    "__version__",
    # Core conversion
    "convert",
    "convert_directory",
    # Format constants
    "SUPPORTED_FORMATS",
    "SUPPORTED_SOURCE_FORMATS",
    "SUPPORTED_TARGET_FORMATS",
    "EXPORT_MODES",
    "TARGET_STRUCTURE_LIMITS",
    # Batch results
    "BatchResult",
    "BatchItemResult",
    # Intermediate AST
    "DocumentModel",
    # Capability probe (free / native / enterprise tiering)
    "capabilities",
    "tier",
    # Exceptions
    "AduPortError",
    "UnsupportedFormatError",
    "EmptyFileError",
    "FileTooLargeError",
    "UploadTimeoutError",
    "CorruptedFileError",
    "InvalidDocumentError",
    "OcrUnavailableError",
    "ExportError",
    "MemoryBudgetError",
]

# Lazy export map: public name -> (module, attribute).
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "convert": ("aduport.core.converter", "convert"),
    "convert_directory": ("aduport.core.converter", "convert_directory"),
    "SUPPORTED_FORMATS": ("aduport.core.converter", "SUPPORTED_FORMATS"),
    "SUPPORTED_SOURCE_FORMATS": ("aduport.core.converter", "SUPPORTED_SOURCE_FORMATS"),
    "SUPPORTED_TARGET_FORMATS": ("aduport.core.converter", "SUPPORTED_TARGET_FORMATS"),
    "EXPORT_MODES": ("aduport.core.converter", "EXPORT_MODES"),
    "TARGET_STRUCTURE_LIMITS": ("aduport.core.converter", "TARGET_STRUCTURE_LIMITS"),
    "BatchResult": ("aduport.core.converter", "BatchResult"),
    "BatchItemResult": ("aduport.core.converter", "BatchItemResult"),
    "DocumentModel": ("aduport.core.document_model", "DocumentModel"),
    "capabilities": ("aduport.sdk", "capabilities"),
    "tier": ("aduport.sdk", "tier"),
    "AduPortError": ("aduport.core.exceptions", "AduPortError"),
    "UnsupportedFormatError": ("aduport.core.exceptions", "UnsupportedFormatError"),
    "EmptyFileError": ("aduport.core.exceptions", "EmptyFileError"),
    "FileTooLargeError": ("aduport.core.exceptions", "FileTooLargeError"),
    "UploadTimeoutError": ("aduport.core.exceptions", "UploadTimeoutError"),
    "CorruptedFileError": ("aduport.core.exceptions", "CorruptedFileError"),
    "InvalidDocumentError": ("aduport.core.exceptions", "InvalidDocumentError"),
    "OcrUnavailableError": ("aduport.core.exceptions", "OcrUnavailableError"),
    "ExportError": ("aduport.core.exceptions", "ExportError"),
    "MemoryBudgetError": ("aduport.core.exceptions", "MemoryBudgetError"),
}


def __getattr__(name: str):
    """Lazily import a public symbol and cache it on the module (PEP 562)."""
    try:
        module_name, attr = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(importlib.import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
