"""Domain exceptions for AduPort."""

from __future__ import annotations

# Machine-readable catalog (enterprise API contract). Keep codes stable.
ERROR_CODES: dict[str, int] = {
    "unsupported_format": 400,
    "empty_file": 400,
    "file_too_large": 413,
    "corrupted_file": 422,
    "invalid_document": 422,
    "ocr_unavailable": 503,
    "needs_ocr": 422,
    "export_error": 500,
    "memory_budget_exceeded": 429,
    "docx_too_large": 422,
    "virus_detected": 422,
    "virus_scan_unavailable": 503,
    "job_not_found": 404,
    "job_queue_full": 429,
    "job_still_active": 409,
    "job_not_resumable": 409,
    "job_input_missing": 410,
    "job_not_ready": 409,
    "job_token_invalid": 403,
    "job_resume_failed": 500,
    "job_reprocess_failed": 500,
    "job_schedule_failed": 503,
    "job_timeout": 504,
    "job_interrupted": 409,
    "convert_timeout": 504,
    "upload_timeout": 408,
    "worker_crash": 500,
    "conversion_error": 422,
    "batch_failed": 422,
    "unsafe_document": 422,
    "cancelled": 499,
    "unauthorized": 401,
    "rate_limited": 429,
    "daily_quota_exceeded": 429,
    "license_invalid": 403,
    "license_secret_weak": 403,
    "license_secret_missing": 403,
    "license_seats_exceeded": 403,
    "validation_error": 422,
    "internal_error": 500,
    "import_failure_budget": 422,
    "iwork_empty": 422,
    "ipynb_too_large": 422,
    "ipynb_too_deep": 422,
    "panic": 500,
    "unsupported": 422,
    "unknown": 422,
    "sign_error": 400,
    "watch_input": 400,
    "watch_path": 400,
    "pdf_merge_input": 400,
    "pdf_page_error": 400,
    "pdf_form_error": 400,
    "pdf_form_values": 400,
    "redact_docx": 400,
    "redact_pdf": 400,
    "redact_not_text": 400,
    "redact_pattern": 400,
    "diff_format": 400,
    "high_failure_rate": 503,
    "high_latency_p95": 503,
    "stuck_convert_threads": 503,
}


class AduPortError(Exception):
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
        from aduport.core.i18n_errors import translate_message

        payload: dict[str, str | int] = {
            "error": True,
            "code": self.code,
            "message": self.message,
            "status_code": self.status_code,
        }
        english = translate_message(self.message, self.code)
        if english:
            payload["message_en"] = english
        return payload


# Back-compat alias for SDKs / scripts that still catch the old name.
MdBridgeError = AduPortError


class UnsupportedFormatError(AduPortError):
    code = "unsupported_format"
    status_code = 400


class EmptyFileError(AduPortError):
    code = "empty_file"
    status_code = 400


class FileTooLargeError(AduPortError):
    code = "file_too_large"
    status_code = 413


class UploadTimeoutError(AduPortError):
    """Slowloris / stalled body — D-24."""

    code = "upload_timeout"
    status_code = 408


class UnsafeDocumentError(AduPortError):
    """Blocked external entity or other active content. Not a generic corrupt file."""

    code = "unsafe_document"
    status_code = 422


class CorruptedFileError(AduPortError):
    code = "corrupted_file"
    status_code = 422


class InvalidDocumentError(AduPortError):
    code = "invalid_document"
    status_code = 422


class OcrUnavailableError(AduPortError):
    """Scanned PDF needs OCR but no engine/models are installed."""

    code = "ocr_unavailable"
    status_code = 503

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        install_hint: str | None = None,
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)
        self.ocr_required = True
        self.install_hint = install_hint or (
            "pip install 'aduport[ocr-mineru]' 或 'aduport[ocr-paddle]'；"
            "设置 ADUPORT_OCR_ENABLED=true；详见 /api/formats notes.ocr 与 "
            "/api/health/detail.ocr"
        )

    def to_dict(self) -> dict[str, str | int | bool]:
        d = super().to_dict()
        d["ocr_required"] = True
        d["install"] = self.install_hint
        return d


class ExportError(AduPortError):
    code = "export_error"
    status_code = 500


class MemoryBudgetError(AduPortError):
    code = "memory_budget_exceeded"
    status_code = 429
