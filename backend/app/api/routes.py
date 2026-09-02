"""FastAPI routes for md-bridge."""

from __future__ import annotations

import asyncio
import io
import json
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.config import settings
from app.core.cache import result_cache
from app.core.convert_isolate import convert_isolated
from app.core.converter import (
    MIME_TYPES,
    SUPPORTED_FORMATS,
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
)
from app.core.css_sanitize import sanitize_custom_css, sanitize_font_family
from app.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidDocumentError,
    MdBridgeError,
    UnsupportedFormatError,
)
from app.core.format_detect import detect_source_format
from app.core.jobs import JobStatus, job_store
from app.core.metrics import metrics
from app.core.request_context import current_tenant_id
from app.core.tenants import tenants_configured
from app.core.upload_stream import read_upload_streaming
from app.importers.docling_ready import docling_models_ready
from app.importers.ocr_engine import list_ocr_engines

router = APIRouter(prefix="/api", tags=["convert"])

_SAFE_STEM = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_executor = ThreadPoolExecutor(max_workers=max(1, settings.batch_workers))


def _resolve_source_format(
    source_format: str | None,
    filename: str | None,
    content: bytes | None = None,
) -> str:
    raw = (source_format or "").strip().lower().lstrip(".")
    if raw and raw not in ("auto", ""):
        if raw not in SUPPORTED_SOURCE_FORMATS:
            raise UnsupportedFormatError(f"不支持的源格式: {source_format}")
        return raw
    detected = detect_source_format(filename, content)
    if not detected:
        raise UnsupportedFormatError(
            "无法自动识别源格式，请显式指定 source_format"
            f"（支持: {sorted(SUPPORTED_SOURCE_FORMATS)}）"
        )
    return detected

def _parse_bool(val: str | None) -> bool | None:
    if val is None or val == "":
        return None
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _pdf_form_opts(
    pdf_font_family: str | None,
    pdf_custom_css: str | None,
    pdf_page_size: str | None = None,
    pdf_orientation: str | None = None,
    pdf_header: str | None = None,
    pdf_footer: str | None = None,
    pdf_page_numbers: str | None = None,
    pdf_toc: str | None = None,
    docx_theme: str | None = None,
) -> dict:
    opts: dict = {
        "pdf_font_family": sanitize_font_family(pdf_font_family, settings.pdf_font_family),
        "pdf_custom_css": sanitize_custom_css(pdf_custom_css),
        "pdf_page_size": pdf_page_size,
        "pdf_orientation": pdf_orientation,
        "pdf_header": pdf_header,
        "pdf_footer": pdf_footer,
        "pdf_page_numbers": _parse_bool(pdf_page_numbers),
        "pdf_toc": _parse_bool(pdf_toc),
    }
    if docx_theme is not None and str(docx_theme).strip():
        opts["docx_theme"] = str(docx_theme).strip().lower()
    return opts



@router.get("/health")
async def health() -> dict[str, object]:
    from app.core.object_storage import storage_info
    from app.importers.anydoc_bridge import anydoc_available, anydoc_version

    native_ready = anydoc_available()
    ocr_info = list_ocr_engines()
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "ocr_enabled": settings.ocr_enabled,
        "max_upload_mb": settings.max_upload_mb,
        "auth_required": bool(settings.api_key or settings.basic_auth or settings.api_keys),
        "tenants_configured": tenants_configured(),
        "docling_models_ready": docling_models_ready(),
        "pdf_engine": settings.pdf_engine,
        "max_batch_files": settings.max_batch_files,
        "cache": result_cache.stats(),
        "metrics": metrics.snapshot(),
        "jobs": job_store.stats(),
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "memory_budget_mb": settings.memory_budget_mb,
        "virus_scan": settings.virus_scan,
        "ocr": ocr_info,
        "ocr_ready": bool(ocr_info.get("ready")),
        "license_required": bool(settings.license_required),
        # Vendored anydoc → mdbridge_native._doc
        "anydoc_enabled": bool(settings.anydoc_enabled),
        "anydoc_available": native_ready,
        "anydoc_version": anydoc_version() if native_ready else None,
        "docx_theme": settings.docx_theme,
        "docx_export_comments": bool(settings.docx_export_comments),
        "heading_numbering": bool(settings.heading_numbering),
        "job_result_dir": bool((settings.job_result_dir or "").strip()),
        "object_storage": storage_info(),
        "webhook_enabled": bool(settings.webhook_enabled),
    }


@router.get("/usage")
async def usage(request: Request) -> dict[str, object]:
    """Tenant usage meter for enterprise billing integrations (read-only)."""
    from app.core.request_context import current_tenant_id

    snap = metrics.snapshot()
    tid = current_tenant_id() or "default"
    daily = snap.get("tenant_daily") or {}
    today = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
    today_key = f"{tid}:{today}"
    return {
        "tenant_id": tid,
        "conversions_today": int(daily.get(today_key, 0)),
        "conversions_total": int((snap.get("conversions_by_tenant") or {}).get(tid, 0)),
        "conversions_failed": int((snap.get("conversions_by_tenant") or {}).get(f"{tid}:failed", 0)),
        "metrics": snap,
    }


@router.get("/license")
async def license_status() -> dict[str, object]:
    """Public license / machine fingerprint (for enterprise activation)."""
    from app.core.license import current_license, machine_id

    info = current_license()
    return {
        "required": bool(settings.license_required),
        "machine_id": machine_id(),
        "license": info.to_dict() if info else None,
    }


@router.get("/formats")
async def list_formats() -> dict[str, object]:
    from app.importers.anydoc_bridge import (
        ANYDOC_ONLY_SIBLINGS,
        anydoc_available,
        should_handle,
    )

    export_only = sorted(SUPPORTED_TARGET_FORMATS - SUPPORTED_SOURCE_FORMATS)
    anydoc_only = sorted(ANYDOC_ONLY_SIBLINGS)
    anydoc_led = sorted(
        f for f in SUPPORTED_SOURCE_FORMATS if should_handle(f) and f not in ANYDOC_ONLY_SIBLINGS
    )
    return {
        "formats": sorted(SUPPORTED_FORMATS),
        "source": sorted(SUPPORTED_SOURCE_FORMATS),
        "target": sorted(SUPPORTED_TARGET_FORMATS),
        "export_only": export_only,
        "anydoc_available": anydoc_available(),
        "anydoc_only_formats": anydoc_only,
        "anydoc_led_formats": anydoc_led,
        "notes": {
            "csv": "table-first：仅导出文档中的表格；无表格时回退为单列文本",
            "export_only": (
                "当前无仅导出格式；latex/azw3/kf8 已支持导入（轻量解析）"
                if not export_only
                else "下列格式仅支持导出，不支持作为源格式导入"
            ),
            "latex": "LaTeX 导入覆盖 article 常用宏（section/list/verbatim/math）",
            "azw3_kf8": "AZW3/KF8 走 MOBI 文本抽取路径",
            "anydoc": (
                "anydoc_only_formats 需编译 mdbridge_native（scripts/build_native.sh / Docker INSTALL_NATIVE=1）；"
                "anydoc_led_formats 为 anydoc 优先、原生兜底的共享格式"
            ),
            "docx": (
                "导出主题：Form docx_theme 或 MDBRIDGE_DOCX_THEME=default|academic|minimal；"
                "批注：AST Comment → Word comments（MDBRIDGE_DOCX_EXPORT_COMMENTS）；"
                "标题编号：MDBRIDGE_HEADING_NUMBERING=true"
            ),
            "xlsx": (
                "表格值导出：仅单元格显示值，不含公式/宏；"
                "Table.cells 在 rows 为空时仍可导出；xlsb/ods 经导入后可落到 xlsx"
            ),
            "ocr": (
                "扫描 PDF：MDBRIDGE_OCR_ENABLED=true + 可选依赖 md-bridge[ocr-mineru]/[ocr-paddle]；"
                "GET /api/health 的 ocr.ready / ocr.install_hint 可探测"
            ),
            "jobs": (
                "异步：POST /api/jobs；可选 callback_url Webhook；"
                "元数据 MDBRIDGE_JOB_DB_PATH；结果字节 MDBRIDGE_JOB_RESULT_DIR（TTL 内可重启后下载）"
            ),
        },
    }


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    return Response(content=metrics.prometheus_text(), media_type="text/plain; version=0.0.4")


@router.post("/jobs")
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    source_format: Optional[str] = Form(None),
    target_format: str = Form(...),
    pdf_font_family: Optional[str] = Form(None),
    pdf_custom_css: Optional[str] = Form(None),
    pdf_page_size: Optional[str] = Form(None),
    pdf_orientation: Optional[str] = Form(None),
    pdf_header: Optional[str] = Form(None),
    pdf_footer: Optional[str] = Form(None),
    pdf_page_numbers: Optional[str] = Form(None),
    pdf_toc: Optional[str] = Form(None),
    docx_theme: Optional[str] = Form(None),
    callback_url: Optional[str] = Form(None),
) -> JSONResponse:
    """Enqueue an async conversion job; poll GET /api/jobs/{id} then download result."""
    spill = await read_upload_streaming(file, prefix="mdbridge-job-up-")
    try:
        content = spill.read()
        filename = file.filename or "input"
        src = _resolve_source_format(source_format, filename, content)
        tgt = target_format.lower().lstrip(".")
        if tgt not in SUPPORTED_TARGET_FORMATS:
            raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")

        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        )
        client = request.client.host if request.client else ""
        if not file.filename:
            filename = f"input.{src}"
        out_name = _safe_output_filename(filename, tgt)

        try:
            from app.core.webhooks import validate_callback_url

            cb = validate_callback_url(callback_url)
        except ValueError as exc:
            raise InvalidDocumentError(str(exc)) from exc

        job = job_store.submit(
            content=content,
            filename=filename,
            source_format=src,
            target_format=tgt,
            client=client,
            result_filename=out_name,
            tenant_id=current_tenant_id(),
            callback_url=cb,
            **pdf_opts,
        )
        return JSONResponse(status_code=202, content=job.to_dict())
    finally:
        spill.release()



@router.get("/jobs")
async def list_jobs() -> dict[str, object]:
    return {
        "jobs": [
            j.to_dict()
            for j in job_store.list_recent(tenant_id=current_tenant_id())
        ]
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, object]:
    job = job_store.get(job_id, tenant_id=current_tenant_id())
    if not job:
        raise MdBridgeError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    return job.to_dict()


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str) -> Response:
    job = job_store.get(job_id, tenant_id=current_tenant_id())
    if not job:
        raise MdBridgeError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    if job.status == JobStatus.error:
        return JSONResponse(
            status_code=422,
            content={
                "error": True,
                "code": job.code or "conversion_error",
                "message": job.error or "转换失败",
                "status_code": 422,
            },
        )
    result = job.read_result()
    if job.status != JobStatus.done or result is None:
        return JSONResponse(
            status_code=409,
            content={
                "error": True,
                "code": "job_not_ready",
                "message": f"任务尚未完成（状态: {job.status.value}）",
                "status_code": 409,
                "progress": job.progress,
            },
        )
    media_type = MIME_TYPES.get(job.target_format, "application/octet-stream")
    name = job.result_filename or f"converted.{job.target_format}"
    return Response(
        content=result,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(name)},
    )


@router.post("/convert")
async def convert_file(
    request: Request,
    file: UploadFile = File(...),
    source_format: Optional[str] = Form(None),
    target_format: str = Form(...),
    pdf_font_family: Optional[str] = Form(None),
    pdf_custom_css: Optional[str] = Form(None),
    pdf_page_size: Optional[str] = Form(None),
    pdf_orientation: Optional[str] = Form(None),
    pdf_header: Optional[str] = Form(None),
    pdf_footer: Optional[str] = Form(None),
    pdf_page_numbers: Optional[str] = Form(None),
    pdf_toc: Optional[str] = Form(None),
    docx_theme: Optional[str] = Form(None),
) -> Response:
    spill = await read_upload_streaming(file, prefix="mdbridge-cvt-up-")
    try:
        content = spill.read()
        filename = file.filename or "input"
        src = _resolve_source_format(source_format, filename, content)
        tgt = target_format.lower().lstrip(".")
        if tgt not in SUPPORTED_TARGET_FORMATS:
            raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")

        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        )
        client = request.client.host if request.client else ""

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _executor,
            lambda: convert_isolated(
                content,
                src,
                tgt,
                filename=file.filename or f"input.{src}",
                client=client,
                **pdf_opts,
            ),
        )

        out_name = _safe_output_filename(file.filename, tgt)
        media_type = MIME_TYPES.get(tgt, "application/octet-stream")
        return Response(
            content=result,
            media_type=media_type,
            headers={"Content-Disposition": _content_disposition(out_name)},
        )
    finally:
        spill.release()



@router.post("/convert/batch")
async def convert_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    source_format: Optional[str] = Form(None),
    target_format: str = Form(...),
    pdf_font_family: Optional[str] = Form(None),
    pdf_custom_css: Optional[str] = Form(None),
    pdf_page_size: Optional[str] = Form(None),
    pdf_orientation: Optional[str] = Form(None),
    pdf_header: Optional[str] = Form(None),
    pdf_footer: Optional[str] = Form(None),
    pdf_page_numbers: Optional[str] = Form(None),
    pdf_toc: Optional[str] = Form(None),
    docx_theme: Optional[str] = Form(None),
) -> Response:
    """
    Batch convert multiple uploaded files (bounded concurrency).

    Successful conversions are returned as a ZIP archive.
    Partial failures recorded in errors.json inside the ZIP.
    When source_format is omitted or "auto", each file is detected independently.
    """
    tgt = target_format.lower().lstrip(".")
    if tgt not in SUPPORTED_TARGET_FORMATS:
        raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")
    if not files:
        raise EmptyFileError("未上传任何文件")
    if len(files) > settings.max_batch_files:
        raise InvalidDocumentError(
            f"批量文件数超限：最多 {settings.max_batch_files} 个，当前 {len(files)}"
        )

    pdf_opts = _pdf_form_opts(
        pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
        pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
    )

    payloads: list[tuple[str, bytes]] = []
    total_size = 0
    for upload in files:
        name = upload.filename or "input"
        up_spill = await read_upload_streaming(upload, prefix="mdbridge-batch-")
        try:
            content = up_spill.read()
        finally:
            up_spill.release()
        total_size += len(content)
        if total_size > settings.max_batch_total_bytes:
            raise FileTooLargeError(
                f"批量总大小超限：最多 {settings.max_batch_total_mb} MB"
            )
        payloads.append((name, content))

    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(max(1, settings.batch_workers))
    client = request.client.host if request.client else ""

    async def _one(name: str, content: bytes) -> tuple[str, bytes | None, dict | None]:
        async with sem:
            try:
                if not content:
                    raise EmptyFileError(f"文件为空: {name}")
                if len(content) > settings.max_upload_bytes:
                    raise FileTooLargeError(
                        f"文件过大: {name}，限制 {settings.max_upload_mb} MB"
                    )
                src = _resolve_source_format(source_format, name, content)
                converted = await loop.run_in_executor(
                    _executor,
                    lambda: convert_isolated(
                        content,
                        src,
                        tgt,
                        filename=name,
                        client=client,
                        **pdf_opts,
                    ),
                )
                return _safe_output_filename(name, tgt), converted, None
            except MdBridgeError as exc:
                return name, None, {"file": name, "code": exc.code, "message": exc.message}
            except Exception as exc:
                msg = str(exc) if settings.debug else "转换失败，请检查文件格式"
                return name, None, {"file": name, "code": "conversion_error", "message": msg}

    results = await asyncio.gather(*[_one(n, c) for n, c in payloads])

    successes: list[tuple[str, bytes]] = []
    failures: list[dict[str, str]] = []
    for out_name, data, err in results:
        if err:
            failures.append(err)
        elif data is not None:
            successes.append((out_name, data))

    if not successes:
        return JSONResponse(
            status_code=422,
            content={
                "error": True,
                "code": "batch_failed",
                "message": "批量转换全部失败",
                "status_code": 422,
                "total": len(files),
                "succeeded": 0,
                "failed": len(failures),
                "items": failures,
            },
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        used_names: set[str] = set()
        for out_name, data in successes:
            safe = _unique_zip_name(out_name, used_names)
            zf.writestr(safe, data)
        if failures:
            zf.writestr(
                "errors.json",
                json.dumps(
                    {
                        "total": len(files),
                        "succeeded": len(successes),
                        "failed": len(failures),
                        "items": failures,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition("converted_batch.zip"),
            "X-Batch-Total": str(len(files)),
            "X-Batch-Succeeded": str(len(successes)),
            "X-Batch-Failed": str(len(failures)),
        },
    )


def _safe_output_filename(original: str | None, target_format: str) -> str:
    """Basename-only, strip path traversal, produce safe download / ZIP entry name."""
    raw = original or f"converted.{target_format}"
    # Drop any directory components (ZIP Slip / path traversal)
    base = raw.replace("\\", "/").split("/")[-1]
    base = base.lstrip(".")  # avoid hidden / empty weirdness
    if "." in base:
        stem = base.rsplit(".", 1)[0]
    else:
        stem = base
    stem = _SAFE_STEM.sub("_", stem).strip("._") or "converted"
    # Cap length
    stem = stem[:120]
    return f"{stem}.{target_format}"


def _unique_zip_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    stem, _, ext = name.rpartition(".")
    n = 2
    while True:
        candidate = f"{stem}_{n}.{ext}" if ext else f"{name}_{n}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        n += 1


def _ascii_fallback(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "converted"
    return f"{safe}.{ext}" if ext else safe


def _content_disposition(filename: str) -> str:
    fallback = _ascii_fallback(filename)
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{encoded}"
