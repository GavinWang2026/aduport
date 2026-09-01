"""Error-code catalog contract (enterprise API stability)."""

from __future__ import annotations

from app.core.exceptions import ERROR_CODES, ExportError, MdBridgeError, UnsupportedFormatError
from app.core.virus_scan import VirusDetectedError


def test_error_catalog_covers_core_codes():
    required = {
        "unsupported_format",
        "empty_file",
        "file_too_large",
        "corrupted_file",
        "invalid_document",
        "export_error",
        "docx_too_large",
        "virus_detected",
        "virus_scan_unavailable",
        "job_not_found",
        "convert_timeout",
        "worker_crash",
        "license_invalid",
    }
    assert required <= set(ERROR_CODES)


def test_exception_classes_use_catalog_codes():
    assert UnsupportedFormatError("x").code == "unsupported_format"
    assert UnsupportedFormatError("x").status_code == ERROR_CODES["unsupported_format"]
    assert VirusDetectedError("x").code == "virus_detected"
    err = MdBridgeError("boom", code="docx_too_large")
    assert err.code == "docx_too_large"
    assert err.status_code == ERROR_CODES["docx_too_large"]
    assert err.to_dict()["code"] == "docx_too_large"


def test_export_error_override_code():
    exc = ExportError("too big", code="docx_too_large")
    assert exc.code == "docx_too_large"
    assert exc.status_code == ERROR_CODES["docx_too_large"]
