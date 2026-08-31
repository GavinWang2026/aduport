"""Domain exceptions for md-bridge."""

from __future__ import annotations


class MdBridgeError(Exception):
    """Base error with a machine-readable code and HTTP status hint."""

    code: str = "conversion_error"
    status_code: int = 422

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.message = message

    def to_dict(self) -> dict[str, str | int]:
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
        }


class UnsupportedFormatError(MdBridgeError):
    code = "unsupported_format"
    status_code = 400


class EmptyFileError(MdBridgeError):
    code = "empty_file"
    status_code = 400


class FileTooLargeError(MdBridgeError):
    code = "file_too_large"
    status_code = 413


class CorruptedFileError(MdBridgeError):
    code = "corrupted_file"
    status_code = 422


class InvalidDocumentError(MdBridgeError):
    code = "invalid_document"
    status_code = 422


class ExportError(MdBridgeError):
    code = "export_error"
    status_code = 500


class MemoryBudgetError(MdBridgeError):
    code = "memory_budget_exceeded"
    status_code = 429
