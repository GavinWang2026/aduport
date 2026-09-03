"""FastAPI routes for md-bridge."""

from __future__ import annotations

import asyncio
import io
import json
import os
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
# Concurrency model (audit D-02 / D-03 / D-04):
# - Default convert_isolate=True → ThreadPool only *schedules*; each task calls
#   convert_isolated() which runs in a long-lived ProcessPool (real multi-core,
#   worker-local result cache). GIL is not on the convert hot path.
# - convert_isolate=False → ProcessPoolExecutor runs convert in child processes.
_AUTO_WORKERS = max(4, min(16, (os.cpu_count() or 4)))
_cvt_workers = settings.convert_pool_workers or _AUTO_WORKERS
if settings.convert_isolate:
    _executor = ThreadPoolExecutor(
        max_workers=max(1, int(_cvt_workers)),
        thread_name_prefix="mdbridge-cvt",
    )
else:
    from concurrent.futures import ProcessPoolExecutor

    try:
        _executor = ProcessPoolExecutor(max_workers=max(1, int(_cvt_workers)))
    except Exception:
        _executor = ThreadPoolExecutor(
            max_workers=max(1, int(_cvt_workers)),
            thread_name_prefix="mdbridge-cvt",
        )


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
    """Liveness probe — no tenant metrics / storage URIs (T23)."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
    }


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness: optional native wheel gate (T38 / MDBRIDGE_NATIVE_REQUIRED)."""
    from app.importers.anydoc_bridge import anydoc_available, anydoc_version

    native_ready = anydoc_available()
    body: dict[str, object] = {
        "status": "ok" if (native_ready or not settings.native_required) else "not_ready",
        "service": settings.app_name,
        "version": __version__,
        "anydoc_available": native_ready,
        "native_required": bool(settings.native_required),
        "anydoc_version": anydoc_version() if native_ready else None,
    }
    if settings.native_required and not native_ready:
        return JSONResponse(status_code=503, content=body)
    return JSONResponse(status_code=200, content=body)


@router.get("/health/status")
@router.get("/health/detail")
async def health_detail() -> dict[str, object]:
    """Ops / status snapshot (requires auth when require_auth is on). Alias: /health/status."""
    from app.core.object_storage import storage_info
    from app.importers.anydoc_bridge import anydoc_available, anydoc_version

    native_ready = anydoc_available()
    ocr_info = list_ocr_engines()
    storage = storage_info()
    # Redact URI from public-ish detail payloads; keep capability flags only
    if isinstance(storage, dict) and "uri" in storage:
        storage = {**storage, "uri": bool(storage.get("uri"))}
    snap = metrics.snapshot()
    # Drop per-tenant breakdowns from aggregate health (use /usage for own tenant)
    safe_metrics = {
        k: v
        for k, v in snap.items()
        if k
        not in (
            "conversions_by_tenant",
            "tenant_daily",
        )
    }
    weasy_ok = False
    weasy_detail = ""
    try:
        import weasyprint  # noqa: F401

        weasy_ok = True
        weasy_detail = getattr(weasyprint, "__version__", "ok")
    except Exception as exc:
        weasy_detail = f"{type(exc).__name__}: {exc}"[:160]
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
        "ocr_enabled": settings.ocr_enabled,
        "max_upload_mb": settings.max_upload_mb,
        "auth_required": bool(
            settings.require_auth
            or settings.api_key
            or settings.basic_auth
            or settings.api_keys
        ),
        "tenants_configured": tenants_configured(),
        "docling_models_ready": docling_models_ready(),
        "pdf_engine": settings.pdf_engine,
        "pdf_export": {
            "weasyprint_available": weasy_ok,
            "weasyprint_detail": weasy_detail,
            "effective": "weasyprint" if weasy_ok else "pdf_simple",
            "config_pdf_engine": settings.pdf_engine,
            "pdf_ast_sidecar": bool(settings.pdf_ast_sidecar),
        },
        "xlsx_ast_sheet": bool(settings.xlsx_ast_sheet),
        "max_batch_files": settings.max_batch_files,
        "cache": result_cache.stats(),
        "metrics": safe_metrics,
        "jobs": job_store.stats(),
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "memory_budget_mb": settings.memory_budget_mb,
        "virus_scan": settings.virus_scan,
        "ocr": ocr_info,
        "ocr_ready": bool(ocr_info.get("ready")),
        "license_required": bool(settings.license_required),
        "anydoc_enabled": bool(settings.anydoc_enabled),
        "anydoc_available": native_ready,
        "anydoc_version": anydoc_version() if native_ready else None,
        "docx_theme": settings.docx_theme,
        "docx_export_comments": bool(settings.docx_export_comments),
        "heading_numbering": bool(settings.heading_numbering),
        "job_result_dir": bool((settings.job_result_dir or "").strip()),
        "object_storage": storage,
        "webhook_enabled": bool(settings.webhook_enabled),
        "builtin_importers": True,
        "breadth_mode": "builtin_first",
        "xlsb_requires_anydoc": True,
        "convert_pool_workers": max(
            1, int(settings.convert_pool_workers or settings.batch_workers)
        ),
        "xlsx_max_sheets": int(settings.xlsx_max_sheets),
        "stream_convert": bool(settings.stream_convert),
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
    """Public license status — machine_id omitted to limit fingerprinting (T23)."""
    from app.core.license import current_license

    info = current_license()
    return {
        "required": bool(settings.license_required),
        "license": info.public_dict() if info else None,
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
        "anydoc_accelerated_formats": anydoc_only,
        "anydoc_led_formats": anydoc_led,
        "builtin_coverage": {
            "docx": "python-docx (+ optional docling)",
            "pptx": "slide XML (+ optional docling)",
            "odt_ods_odp": "ODF content.xml (import+export)",
            "xlsx_xlsm": "OOXML spreadsheetml",
            "xls_ppt_ole": "BIFF8 grid + PPT atoms (CFB); scrape fallback if needed",
            "xlsb": "ZIP workbook.bin sniff; true xlsb via anydoc when enabled",
            "pdf": "pypdfium2 text layer; weak layer → ocr_required (+ optional OCR)",
            "rtf_epub_mobi": "native parsers",
            "azw3_kf8": "MOBI text-level path (not full KF8 layout)",
        },
        "fidelity_tiers": {
            # full = layout/semantic deep paths
            # structured_partial = ODF table/slide with spans/images but not odt-class full
            "full": sorted(
                [
                    "md",
                    "txt",
                    "html",
                    "json",
                    "csv",
                    "latex",
                    "docx",
                    "docm",
                    "pptx",
                    "pptm",
                    "ppsx",
                    "ppsm",
                    "xlsx",
                    "xlsm",
                    "odt",
                    "rtf",
                    "epub",
                    "pdf",
                ]
            ),
            "structured_partial": sorted(["ods", "odp"]),
            "text_level": sorted(
                ["doc", "ppt", "pps", "pot", "xls", "xlsb", "mobi", "azw3", "kf8"]
            ),
            "export_shallow": sorted(["azw3", "kf8"]),
        },
        "notes": {
            "csv": "table-first：仅导出文档中的表格；无表格时回退为单列文本",
            "export_only": (
                "当前无仅导出格式；latex/azw3/kf8 已支持导入（轻量解析）"
                if not export_only
                else "下列格式仅支持导出，不支持作为源格式导入"
            ),
            "latex": "LaTeX 导入覆盖 article 常用宏（section/list/verbatim/math）；导出含 \\includegraphics + latex_assets",
            "azw3_kf8": "AZW3/KF8 为文本级 MOBI 抽取/导出（非完整 KF8 排版保真）；UI 已标「文本级」",
            "ole_legacy": (
                "doc/ppt/pps/pot/xls：OLE CFB 内置路径（BIFF8/PPT 结构化或文本刮取），"
                "metadata.degraded=true；无对称导出到遗留 OLE（ACCEPTED）。"
                "xlsb：扩展名 + xl/workbook.bin 真检测；"
                "真二进制 xlsb 硬依赖 anydoc（无 anydoc → InvalidDocumentError）"
            ),
            "xlsb": (
                "真 xlsb（ZIP + xl/workbook.bin）须启用 vendored anydoc；"
                "误标为 xlsx 的伪 xlsb 可走内置 OOXML 降级"
            ),
            "pdf": (
                "数字 PDF：pypdfium2 文本层；弱/空文本层返回 ocr_required + install 提示。"
                "扫描 OCR 默认关（MDBRIDGE_OCR_ENABLED）；"
                "导出 WeasyPrint 优先，否则 pdf_simple；"
                "默认不嵌入 %%MDBRIDGE-AST sidecar（MDBRIDGE_PDF_AST_SIDECAR=0）；"
                "sidecar 仅调试/AST 往返，非排版保真；"
                "同格式 identity 直通不计入 conversions_total"
            ),
            "anydoc": (
                "anydoc 为 vendored 加速层（scripts/build_native.sh）；"
                "缺省时绝大多数源格式仍有 Python 内置路径（OLE 系为降级文本）。"
                "例外：真 xlsb 无 anydoc 时硬失败（见 notes.xlsb）"
            ),
            "docx": (
                "导入默认 python-docx 内置路径，docling 可选增强；"
                "行内保真深度路径依赖 vendored anydoc（关闭后 AST 覆盖下降，见 ANYDOC_RISK）；"
                "导出主题：Form docx_theme 或 MDBRIDGE_DOCX_THEME=default|academic|minimal；"
                "批注：AST Comment → Word comments.xml（MDBRIDGE_DOCX_EXPORT_COMMENTS）；"
                "脚注：AST Footnote → word/footnotes.xml；"
                "图表：DrawingML 文本级活（非可编辑原生图表，ACCEPTED）；"
                "标题编号：MDBRIDGE_HEADING_NUMBERING=true；"
                "MathBlock→OMML（frac/sqrt/sum/int/delim/希腊字母/matrix/cases）；"
                "SmartArt：DrawingML 版式组图 + diagrams 四件套；"
                "OLE：embeddings/*.bin 二进制嵌入往返"
            ),
            "xlsx": (
                "表格值导出：数值/日期类型推断 + mergeCells/列宽；"
                f"工作表上限 MDBRIDGE_XLSX_MAX_SHEETS（默认 {settings.xlsx_max_sheets}）；"
                "默认不写 `_AST` meta sheet（MDBRIDGE_XLSX_AST_SHEET=0）— 表体/Content 级；"
                "开启后 Chart/Comment/HtmlBlock 等可走 sidecar 往返，不得计入单元格保真；"
                "Chart AST → DrawingML（xl/charts + drawing on sheet1）；"
                "Table.cells 在 rows 为空时仍可导出；xlsb/ods 经导入后可落到 xlsx"
            ),
            "ocr": (
                "扫描 PDF（T37）：默认关闭。"
                "启用：MDBRIDGE_OCR_ENABLED=true + pip install 'md-bridge[ocr-mineru]' 或 [ocr-paddle]；"
                "能力边界见本接口 notes + /api/health/detail.ocr；"
                "文本层不足时错误体含 ocr_required=true 与 install 提示；"
                "数字 PDF 不依赖 OCR（pypdfium2 文本层内置）。未装 OCR 时扫描件不会静默冒充成功 OCR。"
            ),
            "jobs": (
                "异步：POST /api/jobs 返回 access_token（仅创建）；"
                "GET /api/jobs/{id} 与 /result 需 X-Job-Token 或 ?token=（MDBRIDGE_JOB_TOKEN_REQUIRED，默认开）；"
                "可选 callback_url Webhook；元数据 MDBRIDGE_JOB_DB_PATH；结果 MDBRIDGE_JOB_RESULT_DIR"
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
        filename = file.filename or "input"
        client = request.client.host if request.client else ""
        tid = current_tenant_id()
        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        )
        loop = asyncio.get_running_loop()

        def _enqueue():
            content = spill.read()
            src = _resolve_source_format(source_format, filename, content)
            tgt = target_format.lower().lstrip(".")
            if tgt not in SUPPORTED_TARGET_FORMATS:
                raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")
            out_name = _safe_output_filename(
                filename if file.filename else f"input.{src}", tgt
            )
            try:
                from app.core.webhooks import validate_callback_url

                cb = validate_callback_url(callback_url)
            except ValueError as exc:
                raise InvalidDocumentError(str(exc)) from exc
            return job_store.submit(
                content=content,
                filename=filename if file.filename else f"input.{src}",
                source_format=src,
                target_format=tgt,
                client=client,
                result_filename=out_name,
                tenant_id=tid,
                callback_url=cb,
                **pdf_opts,
            )

        job = await loop.run_in_executor(_executor, _enqueue)
        return JSONResponse(status_code=202, content=job.to_dict(include_token=True))
    finally:
        spill.release()



@router.get("/jobs")
async def list_jobs() -> dict[str, object]:
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    jobs = await loop.run_in_executor(
        None, lambda: job_store.list_recent(tenant_id=tid)
    )
    return {"jobs": [j.to_dict() for j in jobs]}


def _job_token_from_request(request: Request) -> str:
    return (
        request.headers.get("X-Job-Token")
        or request.headers.get("x-job-token")
        or request.query_params.get("token")
        or ""
    )


def _require_job_token(job: object, request: Request) -> None:
    if not getattr(settings, "job_token_required", True):
        return
    token = _job_token_from_request(request)
    if not getattr(job, "token_ok", lambda _t: True)(token):
        raise MdBridgeError(
            "缺少或无效的任务令牌（X-Job-Token 或 ?token=）",
            code="job_token_invalid",
            status_code=403,
        )


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, object]:
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    job = await loop.run_in_executor(
        None, lambda: job_store.get(job_id, tenant_id=tid)
    )
    if not job:
        raise MdBridgeError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    _require_job_token(job, request)
    return job.to_dict()


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str, request: Request) -> Response:
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    job, result = await loop.run_in_executor(
        None, lambda: job_store.read_result_safe(job_id, tenant_id=tid)
    )
    if not job:
        raise MdBridgeError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    _require_job_token(job, request)
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
        filename = file.filename or "input"
        client = request.client.host if request.client else ""
        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        )
        loop = asyncio.get_running_loop()

        def _convert() -> tuple[bytes, str]:
            content = spill.read()
            src = _resolve_source_format(source_format, filename, content)
            tgt = target_format.lower().lstrip(".")
            if tgt not in SUPPORTED_TARGET_FORMATS:
                raise UnsupportedFormatError(f"不支持的目标格式: {target_format}")
            charge = job_store.reserve_bytes(len(content))
            try:
                out = convert_isolated(
                    content,
                    src,
                    tgt,
                    filename=file.filename or f"input.{src}",
                    client=client,
                    **pdf_opts,
                )
            finally:
                job_store.release_bytes(charge)
            return out, tgt

        result, tgt = await loop.run_in_executor(_executor, _convert)

        out_name = _safe_output_filename(file.filename, tgt)
        media_type = MIME_TYPES.get(tgt, "application/octet-stream")
        headers = {"Content-Disposition": _content_disposition(out_name)}
        if tgt == "pdf" and b"mdbridge-pdf-degraded: true" in result[:512]:
            headers["X-MdBridge-PDF-Engine"] = "pdf_simple"
            headers["X-MdBridge-PDF-Degraded"] = "true"
        try:
            from app.exporters.export_warnings import get_last_export_warnings

            warns = get_last_export_warnings()
            if warns:
                headers["X-MdBridge-Export-Warnings"] = str(len(warns))
                # Cap header size; full list lives in JSON metadata when target=json
                headers["X-MdBridge-Export-Warning-Sample"] = warns[0][:180]
        except Exception:
            pass
        return Response(
            content=result,
            media_type=media_type,
            headers=headers,
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
    remaining = settings.max_batch_total_bytes
    # D-28: reject oversized multipart before reading bodies (slack for MIME overhead).
    cl_hdr = request.headers.get("content-length")
    if cl_hdr:
        try:
            if int(cl_hdr) > remaining + (2 * 1024 * 1024):
                raise FileTooLargeError(
                    f"批量总大小超限：最多 {settings.max_batch_total_mb} MB"
                )
        except ValueError:
            pass
    for upload in files:
        name = upload.filename or "input"
        part_size = getattr(upload, "size", None)
        if isinstance(part_size, int) and part_size > remaining:
            raise FileTooLargeError(
                f"批量总大小超限：最多 {settings.max_batch_total_mb} MB"
            )
        up_spill = await read_upload_streaming(
            upload, prefix="mdbridge-batch-", max_bytes=remaining
        )
        try:
            content = up_spill.read()
        finally:
            up_spill.release()
        remaining -= len(content)
        if remaining < 0:
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

    def _build_zip() -> bytes:
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
        return buf.getvalue()

    zip_bytes = await loop.run_in_executor(_executor, _build_zip)

    return Response(
        content=zip_bytes,
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
