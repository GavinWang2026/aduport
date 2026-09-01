"""Domain exceptions for md-bridge."""

from __future__ import annotations

# Machine-readable catalog (enterprise API contract). Keep codes stable.
ERROR_CODES: dict[str, int] = {
    "unsupported_format": 400,
    "empty_file": 400,
    "file_too_large": 413,
    "corrupted_file": 422,
    "invalid_document": 422,
    "export_error": 500,
    "memory_budget_exceeded": 429,
    "docx_too_large": 422,
    "virus_detected": 422,
    "virus_scan_unavailable": 503,
    "job_not_found": 404,
    "convert_timeout": 504,
    "worker_crash": 500,
    "conversion_error": 422,
    "cancelled": 499,
    "license_invalid": 403,
    "license_secret_weak": 403,
}


class MdBridgeError(Exception):
    """Base error with a machine-readable code and HTTP status hint."""

    code: str = "conversion_error"
    status_code: int = 422

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        elif code is not None and code in ERROR_CODES:
            self.status_code = ERROR_CODES[code]
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
