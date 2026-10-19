"""English messages for stable error codes.

Chinese text stays in ``AduPortError.message``. API payloads include
``message_en`` as a sentence translation of that text, not only the per-code gloss.
"""

from __future__ import annotations

import re

from aduport.core.i18n_phrases import PHRASES

ENGLISH: dict[str, str] = {
    "unsupported_format": "No importer is registered for this source format, or the extension does not match the declared format.",
    "empty_file": "The file is empty.",
    "file_too_large": "The file exceeds the upload limit.",
    "corrupted_file": "The file could not be parsed.",
    "invalid_document": "The file contents do not match the declared format.",
    "unsafe_document": "The document contains an unsafe external reference.",
    "ocr_unavailable": "This document needs OCR, and no OCR engine is available.",
    "export_error": "Export failed.",
    "memory_budget_exceeded": "The conversion exceeded the memory budget.",
    "job_queue_full": "The job queue is full. Retry shortly.",
    "job_still_active": "The job is still running.",
    "job_not_resumable": "Only a failed job that still has its input can be resumed.",
    "job_input_missing": "The job input has expired.",
    "docx_too_large": "The document has too many nodes to export as DOCX.",
    "virus_detected": "The file was rejected by the virus scanner.",
    "virus_scan_unavailable": "The virus scanner is unavailable.",
    "job_not_found": "The job does not exist or has expired.",
    "convert_timeout": "Conversion timed out.",
    "upload_timeout": "The upload stalled.",
    "worker_crash": "The conversion worker crashed.",
    "conversion_error": "Conversion failed.",
    "cancelled": "The conversion was cancelled.",
    "license_invalid": "The license is not valid.",
    "license_secret_weak": "The license secret is too weak.",
    "license_secret_missing": "The license secret is not configured.",
    "license_seats_exceeded": "The license seat limit has been reached.",
    "unauthorized": "Authentication is missing or invalid.",
    "rate_limited": "Too many requests. Retry shortly.",
    "daily_quota_exceeded": "The daily conversion quota has been reached.",
    "needs_ocr": "This document has no usable text layer and needs OCR.",
    "sign_error": "Signing or signature verification failed.",
    "job_not_ready": "The job is not finished yet.",
    "job_token_invalid": "The job access token is missing or invalid.",
    "job_resume_failed": "The job could not be resumed.",
    "job_reprocess_failed": "The job could not be reprocessed.",
    "job_schedule_failed": "The conversion pool is shut down and could not accept the job.",
    "job_timeout": "The job exceeded its deadline.",
    "job_interrupted": "The job was interrupted by a process restart.",
    "batch_failed": "Every file in the batch failed.",
    "validation_error": "The request failed validation.",
    "internal_error": "An unexpected server error occurred.",
    "import_failure_budget": "Import stopped after too many recoverable errors.",
    "iwork_empty": "No text could be extracted from the iWork document.",
    "ipynb_too_large": "The notebook exceeds the size limit.",
    "ipynb_too_deep": "The notebook JSON is nested too deeply.",
    "panic": "The native converter panicked.",
    "unsupported": "The native converter declined this format.",
    "unknown": "The native converter failed.",
    "watch_input": "The watch input path is invalid.",
    "watch_path": "The watch path is invalid.",
    "pdf_merge_input": "At least two PDFs are required to merge.",
    "pdf_page_error": "The PDF page operation failed.",
    "pdf_form_error": "The PDF form operation failed.",
    "pdf_form_values": "Form values must be a JSON object.",
    "redact_docx": "DOCX redaction failed.",
    "redact_pdf": "PDF redaction failed.",
    "redact_not_text": "Redaction requires a text format.",
    "redact_pattern": "The redaction pattern is invalid.",
    "diff_format": "These formats cannot be diffed.",
    "high_failure_rate": "The recent conversion failure rate is high.",
    "high_latency_p95": "Recent conversion latency is high.",
    "stuck_convert_threads": "A convert thread is still alive after the job deadline.",
}


def english_message(code: str | None) -> str:
    if not code:
        return ""
    return ENGLISH.get(code, "")


_CJK = re.compile(r"[\u4e00-\u9fff]")
_SORTED_PHRASES = tuple(sorted(PHRASES, key=lambda item: len(item[0]), reverse=True))
_PUNCT = (
    ("，", ", "),
    ("。", ". "),
    ("：", ": "),
    ("；", "; "),
    ("、", ", "),
    ("（", " ("),
    ("）", ")"),
    ("「", '"'),
    ("」", '"'),
    ("『", '"'),
    ("』", '"'),
    ("“", '"'),
    ("”", '"'),
    ("？", "?"),
    ("！", "!"),
    ("《", "<"),
    ("》", ">"),
    ("—", "-"),
    ("～", "~"),
    ("·", "-"),
)


def translate_message(message: str | None, code: str | None = None) -> str:
    """Translate one Chinese error sentence. English text is returned unchanged."""
    text = str(message or "").strip()
    if not text:
        return english_message(code)
    if not _CJK.search(text):
        return text
    for zh, en in _SORTED_PHRASES:
        if zh and zh in text:
            text = text.replace(zh, en)
    for src, dst in _PUNCT:
        text = text.replace(src, dst)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\s+([,.;:)])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    return text.strip()


def error_body(
    code: str,
    message: str,
    status_code: int,
    **extra: object,
) -> dict[str, object]:
    """API error object with the Chinese sentence and its English translation."""
    body: dict[str, object] = {
        "error": True,
        "code": code,
        "message": message,
        "message_en": translate_message(message, code),
        "status_code": status_code,
    }
    body.update(extra)
    return body
