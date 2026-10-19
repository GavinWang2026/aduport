"""PDF page ops, forms, redaction, signatures, and AST diff — outside convert()."""

from __future__ import annotations

import io
import zipfile
from typing import Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, Response

from aduport.core.ast_diff import diff_models
from aduport.core.converter import _import
from aduport.core.exceptions import AduPortError
from aduport.core.file_sign import FileSignError, sign_bytes, verify_bytes
from aduport.core.pdf_forms import PdfFormError, extract_form_fields, fill_form_fields
from aduport.core.pdf_pages import PdfPageError, extract_pages, merge_pdfs, rotate_pdf, split_pdf
from aduport.core.redact import redact_text, watermark_text

router = APIRouter(prefix="/api", tags=["tools"])


@router.post("/watch/scan")
async def watch_scan(
    input_dir: str = Form(...),
    output_dir: str = Form(...),
    target_format: str = Form(...),
    source_format: str = Form(""),
) -> JSONResponse:
    """One directory scan. Paths must stay under the MCP allowed roots."""
    from aduport.core.ops_surface import watch_once

    try:
        return JSONResponse(watch_once(input_dir, output_dir, target_format, source_format))
    except FileNotFoundError as exc:
        raise AduPortError(str(exc), code="watch_input", status_code=400) from exc
    except (ValueError, PermissionError) as exc:
        raise AduPortError(str(exc), code="watch_path", status_code=400) from exc


@router.post("/fidelity/probe")
async def fidelity_probe(text: str = Form(...)) -> JSONResponse:
    """Round-trip Markdown through office/text targets and report AST equality."""
    from aduport.core.fidelity_probe import probe_markdown

    from aduport.core.upload_limit import upload_byte_limit

    if len(text.encode("utf-8")) > upload_byte_limit():
        raise AduPortError("探针文本过大", code="file_too_large", status_code=413)
    return JSONResponse(probe_markdown(text))


def _read_limited(upload: UploadFile) -> bytes:
    from aduport.core.exceptions import FileTooLargeError
    from aduport.core.upload_limit import upload_byte_limit

    limit = upload_byte_limit()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise FileTooLargeError(
                f"文件过大: {upload.filename or ''}，限制 {limit} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _pdf_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/pdf/merge")
async def pdf_merge(files: list[UploadFile] = File(...)) -> Response:
    if len(files) < 2:
        raise AduPortError("至少上传两个 PDF", code="pdf_merge_input", status_code=400)
    parts = [_read_limited(f) for f in files]
    try:
        merged = merge_pdfs(parts)
    except PdfPageError as exc:
        raise AduPortError(str(exc), code="pdf_page_error", status_code=400) from exc
    return _pdf_response(merged, "merged.pdf")


@router.post("/pdf/extract")
async def pdf_extract(
    file: UploadFile = File(...),
    pages: str = Form(...),
) -> Response:
    data = _read_limited(file)
    try:
        out = extract_pages(data, pages)
    except PdfPageError as exc:
        raise AduPortError(str(exc), code="pdf_page_error", status_code=400) from exc
    return _pdf_response(out, "extract.pdf")


@router.post("/pdf/rotate")
async def pdf_rotate(
    file: UploadFile = File(...),
    degrees: int = Form(90),
    pages: Optional[str] = Form(None),
) -> Response:
    data = _read_limited(file)
    try:
        out = rotate_pdf(data, degrees, pages)
    except PdfPageError as exc:
        raise AduPortError(str(exc), code="pdf_page_error", status_code=400) from exc
    return _pdf_response(out, "rotated.pdf")


@router.post("/pdf/split")
async def pdf_split(file: UploadFile = File(...)) -> Response:
    data = _read_limited(file)
    try:
        pieces = split_pdf(data)
    except PdfPageError as exc:
        raise AduPortError(str(exc), code="pdf_page_error", status_code=400) from exc
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, piece in enumerate(pieces, start=1):
            zf.writestr(f"page-{i}.pdf", piece)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="pages.zip"'},
    )


@router.post("/redact")
async def redact_document(
    file: UploadFile = File(...),
    watermark: str = Form(""),
    pattern: Optional[str] = Form(None),
    replacement: str = Form("[REDACTED]"),
) -> Response:
    raw = _read_limited(file)
    name = (file.filename or "").lower()
    from aduport.core.redact import redact_docx_bytes, redact_pdf_bytes

    if name.endswith(".docx"):
        try:
            out, count = redact_docx_bytes(raw, [(pattern, replacement)] if pattern else None)
        except ValueError as exc:
            raise AduPortError(str(exc), code="redact_docx", status_code=400) from exc
        return Response(
            content=out,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": 'attachment; filename="redacted.docx"',
                "X-AduPort-Redact-Count": str(count),
                "X-AduPort-Redact-Layer": "docx",
            },
        )
    if name.endswith(".pdf") or raw.startswith(b"%PDF"):
        try:
            out, count, partial = redact_pdf_bytes(raw)
        except ValueError as exc:
            raise AduPortError(str(exc), code="redact_pdf", status_code=400) from exc
        from aduport.api.routes import _export_warning_headers

        headers = {
            "Content-Disposition": 'attachment; filename="redacted.pdf"',
            "X-AduPort-Redact-Count": str(count),
            "X-AduPort-Redact-Layer": "pdf",
            "X-AduPort-Redact-Partial": "true" if partial else "false",
        }
        if partial:
            headers.update(
                _export_warning_headers(
                    ["degraded: PDF redaction left compressed streams untouched"]
                )
            )
        return Response(
            content=out,
            media_type="application/pdf",
            headers=headers,
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AduPortError(
            "脱敏 MVP 只接受 UTF-8 文本（md/txt/html）",
            code="redact_not_text",
            status_code=400,
        ) from exc
    extra = [(pattern, replacement)] if pattern else None
    try:
        redacted, count = redact_text(text, extra)
    except ValueError as exc:
        raise AduPortError(str(exc), code="redact_pattern", status_code=400) from exc
    if watermark.strip():
        redacted = watermark_text(redacted, watermark)
    return JSONResponse(
        {
            "replacements": count,
            "watermark": watermark.strip(),
            "text": redacted,
        }
    )


@router.post("/diff")
async def diff_documents(
    left: UploadFile = File(...),
    right: UploadFile = File(...),
    left_format: Optional[str] = Form(None),
    right_format: Optional[str] = Form(None),
) -> JSONResponse:
    from aduport.core.format_detect import detect_source_format

    left_bytes = _read_limited(left)
    right_bytes = _read_limited(right)
    left_fmt = (left_format or detect_source_format(left.filename or "") or "").lower()
    right_fmt = (right_format or detect_source_format(right.filename or "") or "").lower()
    if not left_fmt or not right_fmt:
        raise AduPortError(
            "无法识别格式，请传 left_format / right_format",
            code="diff_format",
            status_code=400,
        )
    left_model = _import(left_bytes, left_fmt, left.filename or f"left.{left_fmt}")
    right_model = _import(right_bytes, right_fmt, right.filename or f"right.{right_fmt}")
    return JSONResponse(diff_models(left_model, right_model))


@router.post("/pdf/form/extract")
async def pdf_form_extract(file: UploadFile = File(...)) -> JSONResponse:
    data = _read_limited(file)
    try:
        return JSONResponse(extract_form_fields(data))
    except PdfFormError as exc:
        raise AduPortError(str(exc), code="pdf_form_error", status_code=400) from exc


@router.post("/pdf/form/fill")
async def pdf_form_fill(
    file: UploadFile = File(...),
    values: str = Form(...),
) -> Response:
    import json

    data = _read_limited(file)
    try:
        parsed = json.loads(values)
    except json.JSONDecodeError as exc:
        raise AduPortError("values 须为 JSON 对象", code="pdf_form_values", status_code=400) from exc
    if not isinstance(parsed, dict):
        raise AduPortError("values 须为 JSON 对象", code="pdf_form_values", status_code=400)
    cleaned = {str(key): "" if val is None else str(val) for key, val in parsed.items()}
    try:
        filled = fill_form_fields(data, cleaned)
    except PdfFormError as exc:
        raise AduPortError(str(exc), code="pdf_form_error", status_code=400) from exc
    return _pdf_response(filled, "filled.pdf")


@router.post("/sign")
async def sign_document(
    file: UploadFile = File(...),
    key: str = Form(...),
    key_id: str = Form(""),
) -> JSONResponse:
    data = _read_limited(file)
    try:
        payload = sign_bytes(data, key, key_id)
    except FileSignError as exc:
        raise AduPortError(str(exc), code="sign_error", status_code=400) from exc
    return JSONResponse(payload)


@router.post("/verify")
async def verify_document(
    file: UploadFile = File(...),
    key: str = Form(...),
    mac: str = Form(...),
) -> JSONResponse:
    data = _read_limited(file)
    try:
        valid = verify_bytes(data, key, mac)
    except FileSignError as exc:
        raise AduPortError(str(exc), code="sign_error", status_code=400) from exc
    return JSONResponse({"valid": valid, "alg": "HMAC-SHA256"})
