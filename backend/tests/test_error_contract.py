"""Error-code catalog contract (enterprise API stability)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from aduport.core.exceptions import (
    ERROR_CODES,
    ExportError,
    FileTooLargeError,
    AduPortError,
    UnsupportedFormatError,
)
from aduport.core.i18n_errors import translate_message
from aduport.core.virus_scan import VirusDetectedError

_CJK = re.compile(r"[\u4e00-\u9fff]")


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
    err = AduPortError("boom", code="docx_too_large")
    assert err.code == "docx_too_large"
    assert err.status_code == ERROR_CODES["docx_too_large"]
    assert err.to_dict()["code"] == "docx_too_large"


def test_export_error_override_code():
    exc = ExportError("too big", code="docx_too_large")
    assert exc.code == "docx_too_large"
    assert exc.status_code == ERROR_CODES["docx_too_large"]


def test_every_raised_code_is_catalogued():
    """Each ``code=`` / ``"code":`` literal under the app is in both catalogs."""
    import re
    from pathlib import Path

    from aduport.core.i18n_errors import ENGLISH

    root = Path(__file__).resolve().parents[1]
    files = list((root / "aduport").rglob("*.py"))
    files.append(root / "main.py")
    patterns = (
        re.compile(r"""\bcode\s*=\s*["']([a-z][a-z0-9_]*)["']"""),
        re.compile(r"""["']code["']\s*:\s*["']([a-z][a-z0-9_]*)["']"""),
        re.compile(r"""["']code["']\s*\]\s*=\s*["']([a-z][a-z0-9_]*)["']"""),
    )
    found: set[str] = set()
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            found.update(pattern.findall(text))
    missing_status = sorted(found - set(ERROR_CODES))
    missing_en = sorted(found - set(ENGLISH))
    assert missing_status == [], missing_status
    assert missing_en == [], missing_en
    assert set(ERROR_CODES) == set(ENGLISH)


def test_message_en_is_the_chinese_sentence():
    err = FileTooLargeError("文件过大: report.docx (12 bytes)，限制 100 MB")
    body = err.to_dict()
    assert body["message"].startswith("文件过大")
    assert body["message_en"] == "The file is too large: report.docx (12 bytes), limit 100 MB"


def _flatten_message(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("X")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _flatten_message(node.left)
        right = _flatten_message(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "strip":
        return _flatten_message(node.func.value)
    if isinstance(node, (ast.BoolOp, ast.IfExp, ast.Name)):
        return "X"
    return None


def test_every_raised_chinese_message_translates():
    """Each raised error sentence has an English ``message_en`` with the same details."""
    root = Path(__file__).resolve().parents[1]
    files = list((root / "aduport").rglob("*.py"))
    files.append(root / "main.py")
    seen: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        blobs: list[ast.AST] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                name = getattr(node.exc.func, "id", None) or getattr(node.exc.func, "attr", "")
                if name and "Error" in name and node.exc.args:
                    blobs.append(node.exc.args[0])
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if isinstance(key, ast.Constant) and key.value == "message":
                        blobs.append(value)
        for blob in blobs:
            text = _flatten_message(blob)
            if not text or not _CJK.search(text) or text in seen:
                continue
            seen.add(text)
            english = translate_message(text)
            assert not _CJK.search(english), text
            if "X" in text:
                assert "X" in english, text
    assert len(seen) > 100
