"""FastAPI routes for AduPort."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from aduport import __version__
from aduport.config import settings
from aduport.core.cache import result_cache
from aduport.core.convert_isolate import convert_isolated
from aduport.core.converter import (
    EXPORT_MODES,
    MIME_TYPES,
    SUPPORTED_FORMATS,
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
    TARGET_STRUCTURE_LIMITS,
    is_accepted_target,
)
from aduport.core.css_sanitize import sanitize_custom_css, sanitize_font_family
from aduport.core.exceptions import (
    EmptyFileError,
    FileTooLargeError,
    InvalidDocumentError,
    AduPortError,
    UnsupportedFormatError,
)
from aduport.core.fidelity_page_grade import page_grade_public_contract
from aduport.core.format_detect import detect_source_format
from aduport.core.jobs import JobStatus, job_store
from aduport.core.metrics import metrics
from aduport.core.request_context import current_tenant_id
from aduport.core.tenants import tenants_configured
from aduport.core.upload_stream import read_upload_streaming
from aduport.importers.docling_ready import docling_models_ready
from aduport.importers.ocr_engine import list_ocr_engines

_log_route = logging.getLogger("aduport.routes")

_PAGE_SIZES = {"a4", "letter", "legal", "a3", "a5"}
_ORIENTATIONS = {"portrait", "landscape"}
_DOCX_THEMES = {"default", "academic", "minimal"}
_EMBED_BACKENDS = {"hash", "fastembed"}
_PDF_ENGINES = {
    "auto",
    "text",
    "docling",
    "ocr",
    "mineru",
    "paddleocr_vl",
    "weasyprint",
    "simple",
    "pdf_simple",
}
_OCR_ENGINES = {"auto", "mineru", "paddleocr_vl", "docling"}


def _choice(raw: str | None, allowed: set[str], field: str) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    val = str(raw).strip().lower()
    if val not in allowed:
        raise InvalidDocumentError(f"{field} 取值无效: {val}")
    return val


def _bounded_text(raw: str | None, *, limit: int) -> str | None:
    if raw is None:
        return None
    text = str(raw).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        return None
    return text[:limit]


router = APIRouter(prefix="/api", tags=["convert"])

_SAFE_STEM = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def _export_warning_headers(
    warns: list[str],
    body: bytes | None = None,
) -> dict[str, str]:
    """ASCII-only warning headers. Non-latin-1 samples are percent-encoded.

    ``X-AduPort-Export-Degraded`` is the stable signal. The JSON header carries
    the same flag plus the warning texts (``ensure_ascii`` so it stays latin-1).
    """
    from aduport.exporters.export_warnings import warning_marks_degraded

    degraded = any(warning_marks_degraded(w) for w in warns)
    if body and b"aduport-pdf-degraded: true" in body[:512]:
        degraded = True
    if not warns and not degraded:
        return {}
    headers: dict[str, str] = {}
    if degraded:
        headers["X-AduPort-Export-Degraded"] = "true"
    payload = json.dumps(
        {
            "degraded": degraded,
            "warnings": [str(w)[:180] for w in warns[:8]],
        },
        ensure_ascii=True,
    )
    headers["X-AduPort-Export-Warning-Json"] = payload
    if warns:
        headers["X-AduPort-Export-Warnings"] = str(len(warns))
        headers["X-AduPort-Export-Warning-Sample"] = quote(str(warns[0])[:180], safe="")
    return headers


# Concurrency model (audit D-02 / D-03 / D-04):
# The route pool only schedules. It must stay a thread pool: the handlers close
# over request state, and a ProcessPool cannot pickle those closures
# (``Can't get local object 'convert_file.<locals>._convert'``).
# ``convert_isolate`` is honored inside ``convert_isolated()``: True runs the
# long-lived process pool; False converts in this thread.
_AUTO_WORKERS = max(4, min(16, (os.cpu_count() or 4)))
_cvt_workers = settings.convert_pool_workers or _AUTO_WORKERS
_executor = ThreadPoolExecutor(
    max_workers=max(1, int(_cvt_workers)),
    thread_name_prefix="aduport-cvt",
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


def _target_or_raise(target_format: str) -> str:
    from aduport.core.converter import _normalize_format

    tgt = _normalize_format(target_format)
    if not is_accepted_target(tgt):
        raise UnsupportedFormatError(
            f"不支持的目标格式: {target_format}。"
            f"支持: {sorted(SUPPORTED_TARGET_FORMATS)}；导出模式: {sorted(EXPORT_MODES)}"
        )
    return tgt

_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def _parse_bool(val: str | None) -> bool | None:
    """Parse a Form bool. Empty → None; illegal tokens → 422 (not silent false)."""
    if val is None or val == "":
        return None
    token = str(val).strip().lower()
    if token in _BOOL_TRUE:
        return True
    if token in _BOOL_FALSE:
        return False
    raise InvalidDocumentError(
        f"布尔参数取值无效: {val!r}（接受: true/false/1/0/yes/no/on/off）",
        code="validation_error",
    )


# Form keys accepted on convert / jobs / batch. Extra keys → 422 (v7 P1).
_CONVERT_FORM_KEYS = frozenset(
    {
        "file",
        "files",
        "source_format",
        "target_format",
        "pdf_font_family",
        "pdf_custom_css",
        "pdf_page_size",
        "pdf_orientation",
        "pdf_header",
        "pdf_footer",
        "pdf_page_numbers",
        "pdf_toc",
        "docx_theme",
        "rag_max_chars",
        "rag_chunk_overlap",
        "embed",
        "embed_backend",
        "embed_model",
        "vector_persist",
        "vector_collection",
        "pdf_engine",
        "ocr_engine",
        "force_ocr",
        "ocr_max_pages",
        "skip_pdf_sidecar",
        "cache_bypass",
        "docx_auto_toc",
        "latex_plain_comments",
        "csv_table_only",
        "callback_url",
    }
)


async def _reject_unknown_form_fields(request: Request) -> None:
    """Refuse silent Form drops: unknown convert knobs must 422, not 200."""
    form = await request.form()
    unknown = sorted({str(key) for key in form.keys() if str(key) not in _CONVERT_FORM_KEYS})
    if unknown:
        raise InvalidDocumentError(
            f"未知转换参数: {', '.join(unknown)}",
            code="validation_error",
        )


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
    rag_max_chars: str | None = None,
    rag_chunk_overlap: str | None = None,
    embed: str | None = None,
    embed_backend: str | None = None,
    embed_model: str | None = None,
    vector_persist: str | None = None,
    vector_collection: str | None = None,
    pdf_engine: str | None = None,
    ocr_engine: str | None = None,
    force_ocr: str | None = None,
    ocr_max_pages: str | None = None,
    skip_pdf_sidecar: str | None = None,
    cache_bypass: str | None = None,
    docx_auto_toc: str | None = None,
    latex_plain_comments: str | None = None,
    csv_table_only: str | None = None,
) -> dict:
    opts: dict = {
        "pdf_font_family": sanitize_font_family(pdf_font_family, settings.pdf_font_family),
        "pdf_custom_css": sanitize_custom_css(pdf_custom_css),
        "pdf_page_size": _choice(pdf_page_size, _PAGE_SIZES, "pdf_page_size"),
        "pdf_orientation": _choice(pdf_orientation, _ORIENTATIONS, "pdf_orientation"),
        "pdf_header": _bounded_text(pdf_header, limit=500),
        "pdf_footer": _bounded_text(pdf_footer, limit=500),
        "pdf_page_numbers": _parse_bool(pdf_page_numbers),
        "pdf_toc": _parse_bool(pdf_toc),
    }
    theme = _choice(docx_theme, _DOCX_THEMES, "docx_theme")
    if theme:
        opts["docx_theme"] = theme
    if rag_max_chars is not None and str(rag_max_chars).strip():
        try:
            opts["rag_max_chars"] = max(200, min(20000, int(rag_max_chars)))
        except (TypeError, ValueError) as exc:
            raise InvalidDocumentError(
                f"rag_max_chars 取值无效: {rag_max_chars!r}（须为整数）",
                code="validation_error",
            ) from exc
    if rag_chunk_overlap is not None and str(rag_chunk_overlap).strip():
        try:
            opts["rag_chunk_overlap"] = max(0, min(4000, int(rag_chunk_overlap)))
        except (TypeError, ValueError) as exc:
            raise InvalidDocumentError(
                f"rag_chunk_overlap 取值无效: {rag_chunk_overlap!r}（须为整数）",
                code="validation_error",
            ) from exc
    emb = _parse_bool(embed)
    if emb is not None:
        opts["embed"] = emb
    backend = _choice(embed_backend, _EMBED_BACKENDS, "embed_backend")
    if backend:
        opts["embed_backend"] = backend
    if embed_model is not None and str(embed_model).strip():
        model = _bounded_text(embed_model, limit=120) or ""
        if model and not re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,120}", model):
            raise InvalidDocumentError("embed_model 含非法字符")
        if model:
            opts["embed_model"] = model
    vp = _parse_bool(vector_persist)
    if vp is not None:
        opts["vector_persist"] = vp
    if vector_collection is not None and str(vector_collection).strip():
        coll = str(vector_collection).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", coll):
            raise InvalidDocumentError("vector_collection 须为 1–64 位字母数字、下划线或连字符")
        opts["vector_collection"] = coll
    engine = _choice(pdf_engine, _PDF_ENGINES, "pdf_engine")
    if engine:
        opts["pdf_engine"] = engine
    ocr = _choice(ocr_engine, _OCR_ENGINES, "ocr_engine")
    if ocr:
        opts["ocr_engine"] = ocr
    fo = _parse_bool(force_ocr)
    if fo is not None:
        opts["force_ocr"] = fo
    if ocr_max_pages is not None and str(ocr_max_pages).strip():
        try:
            opts["ocr_max_pages"] = max(0, min(5000, int(ocr_max_pages)))
        except (TypeError, ValueError) as exc:
            raise InvalidDocumentError(
                f"ocr_max_pages 取值无效: {ocr_max_pages!r}（须为整数）",
                code="validation_error",
            ) from exc
    ss = _parse_bool(skip_pdf_sidecar)
    if ss is not None:
        opts["skip_pdf_sidecar"] = ss
    cb = _parse_bool(cache_bypass)
    if cb is not None:
        opts["cache_bypass"] = cb
    toc = _parse_bool(docx_auto_toc)
    if toc is not None:
        opts["docx_auto_toc"] = toc
    plain = _parse_bool(latex_plain_comments)
    if plain is not None:
        opts["latex_plain_comments"] = plain
    cto = _parse_bool(csv_table_only)
    if cto is not None:
        opts["csv_table_only"] = cto
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
    """Readiness: optional native wheel gate (T38 / ADUPORT_NATIVE_REQUIRED)."""
    from aduport.importers.anydoc_bridge import anydoc_available, anydoc_version

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
async def health_status() -> dict[str, object]:
    """Lighter ops snapshot: metrics without job-store internals (P3-5)."""
    payload = await _health_detail_payload()
    payload.pop("jobs", None)
    return payload


@router.get("/health/detail")
async def health_detail() -> dict[str, object]:
    """Full ops snapshot including job queue stats."""
    return await _health_detail_payload()


def _session_depth_info() -> dict[str, object]:
    """Multi-worker session depth: HMAC secret + Redis revoke/seats (v12 residual)."""
    from aduport.api.auth import session_hmac_configured

    hmac_ready = session_hmac_configured()
    redis_on = bool((settings.redis_url or "").strip())
    hints: list[str] = []
    if not hmac_ready:
        hints.append("设 ADUPORT_SESSION_SECRET（≥16）以便跨 worker HMAC 会话（无硬编码回退）")
    if not redis_on:
        hints.append("设 ADUPORT_REDIS_URL 以便 revoke/席位跨进程")
    if not bool(getattr(settings, "cookie_secure", True)):
        hints.append("ADUPORT_COOKIE_SECURE=0：仅本地 HTTP 演示；生产勿关")
    return {
        "cookie_secure": bool(getattr(settings, "cookie_secure", True)),
        "hmac_session": hmac_ready,
        "revoke_backend": "redis" if redis_on else "memory",
        "multi_worker_ready": hmac_ready and redis_on,
        "install_hint": "；".join(hints) if hints else None,
    }


def _isolate_health() -> dict[str, object]:
    """Pool liveness without starting workers. A health check must not spawn."""
    from aduport.core.convert_isolate import isolate_pool_status

    return isolate_pool_status()


async def _health_detail_payload() -> dict[str, object]:
    """Ops / status snapshot (requires auth when require_auth is on)."""
    from aduport.core.embed import embed_ready
    from aduport.core.object_storage import storage_info
    from aduport.core.secrets import secret_plain
    from aduport.core.vector_store import store_info
    from aduport.importers.anydoc_bridge import anydoc_available, anydoc_version

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
        "ocr": {
            **(ocr_info if isinstance(ocr_info, dict) else {"detail": ocr_info}),
            "max_pages": int(getattr(settings, "ocr_max_pages", 0) or 0),
            "allow_paddle": str(getattr(settings, "ocr_allow_paddle", "auto") or "auto"),
            "min_chars": int(getattr(settings, "ocr_min_chars", 0) or 0),
            "timeout_seconds": float(getattr(settings, "ocr_timeout_seconds", 300) or 300),
            "reocr": {
                "force_ocr": True,
                "skip_pdf_sidecar": True,
                "cache_bypass": True,
                "job_reprocess": True,
                "layout_feedback": True,
            },
        },
        "rag": {
            "schema": "aduport.rag_elements.v1",
            "schema_version": "1.1",
            "schema_version_embed": "1.2",
            "max_chars": int(getattr(settings, "rag_max_chars", 1200) or 1200),
            "chunk_overlap": int(getattr(settings, "rag_chunk_overlap", 0) or 0),
            "embed_default": bool(getattr(settings, "embed_default", False)),
            "embedding": embed_ready(),
            "vector_store": store_info(),
        },
        "max_upload_mb": settings.max_upload_mb,
        "auth_required": bool(
            settings.require_auth
            or secret_plain(settings.api_key)
            or secret_plain(settings.basic_auth)
            or secret_plain(settings.api_keys)
        ),
        "tenants_configured": tenants_configured(),
        "docling_models_ready": docling_models_ready(),
        "pdf_engine": settings.pdf_engine,
        "isolate": _isolate_health(),
        "pdf_export": {
            "weasyprint_available": weasy_ok,
            "weasyprint_detail": weasy_detail,
            "effective": "weasyprint" if weasy_ok else "pdf_simple",
            "config_pdf_engine": settings.pdf_engine,
            "pdf_ast_sidecar": bool(settings.pdf_ast_sidecar),
            "install_hint": (
                None
                if weasy_ok
                else "macOS: brew install pango gdk-pixbuf libffi；或用 Docker 企业镜像"
            ),
        },
        "xlsx_ast_sheet": bool(settings.xlsx_ast_sheet),
        "max_batch_files": settings.max_batch_files,
        "cache": result_cache.stats(),
        "metrics": safe_metrics,
        "jobs": job_store.stats(),
        "rate_limit_per_minute": settings.rate_limit_per_minute,
        "rate_limit": {
            "per_minute": int(settings.rate_limit_per_minute),
            "backend": "redis" if (settings.redis_url or "").strip() else "memory",
            "install_hint": (
                None
                if (settings.redis_url or "").strip()
                else "多 worker 部署请设 ADUPORT_REDIS_URL，否则内存限流不跨进程"
            ),
        },
        "session": _session_depth_info(),
        "memory_budget_mb": settings.memory_budget_mb,
        "virus_scan": settings.virus_scan,
        "capabilities": {
            "virus_scan": {
                "enabled": bool(settings.virus_scan),
                "install_hint": "ADUPORT_VIRUS_SCAN=1，并配置 clamd",
            },
            "ocr": {
                "enabled": bool(settings.ocr_enabled),
                "ready": bool(isinstance(ocr_info, dict) and ocr_info.get("ready")),
                "notice": (
                    None
                    if isinstance(ocr_info, dict) and ocr_info.get("ready")
                    else (
                        "未启用 OCR，仅嵌入图片"
                        if not settings.ocr_enabled
                        else "OCR 已开启但无可用引擎，图片仅嵌入"
                    )
                ),
                "install_hint": "ADUPORT_OCR_ENABLED=1 + pip install 'aduport[ocr]'",
            },
            "license": {
                "enabled": bool(settings.license_required),
                "install_hint": "ADUPORT_LICENSE_REQUIRED=1 与高强度 LICENSE_SECRET",
            },
            "embed": {
                "enabled": bool(getattr(settings, "embed_default", False)),
                "install_hint": "转换时 embed=true，或 ADUPORT_EMBED_DEFAULT=1",
            },
            "libreoffice": {
                "enabled": bool(settings.libreoffice_enabled),
                "install_hint": "ADUPORT_LIBREOFFICE_ENABLED=1（不打进默认镜像）",
            },
            "anydoc_fallback": {
                "enabled": bool(settings.anydoc_fallback),
                "install_hint": "ADUPORT_ANYDOC_FALLBACK",
            },
            "pdf_ast_sidecar": {
                "enabled": bool(settings.pdf_ast_sidecar),
                "install_hint": "ADUPORT_PDF_AST_SIDECAR=1（不计入排版保真）",
            },
            "xlsx_ast_sheet": {
                "enabled": bool(settings.xlsx_ast_sheet),
                "install_hint": "ADUPORT_XLSX_AST_SHEET=1（不计入单元格保真）",
            },
            "native_office": {
                "enabled": native_ready,
                "install_hint": (
                    "预编译: bash scripts/install_native_wheel.sh --release；"
                    "或 Docker / bash scripts/build_native.sh（doc/ppt/xls/xlsb）"
                ),
            },
        },
        "ocr_ready": bool(isinstance(ocr_info, dict) and ocr_info.get("ready")),
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
    from aduport.core.request_context import current_tenant_id

    snap = metrics.snapshot()
    tid = current_tenant_id() or "default"
    daily = snap.get("tenant_daily") or {}
    today = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
    today_key = f"{tid}:{today}"
    by_tenant = snap.get("conversions_by_tenant") or {}
    return {
        "tenant_id": tid,
        "conversions_today": int(daily.get(today_key, 0)),
        "conversions_total": int(by_tenant.get(tid, 0)),
        "conversions_failed": int(by_tenant.get(f"{tid}:failed", 0)),
    }


@router.get("/tenants")
async def list_tenants() -> dict[str, object]:
    """Read-only tenant registry. Response never includes API keys."""
    from aduport.core.tenants import public_tenants

    return {"tenants": public_tenants()}


@router.post("/auth/session")
async def create_auth_session(request: Request) -> JSONResponse:
    """Bind X-API-Key into an HttpOnly session cookie (enterprise FE hardening, P1-4)."""
    from aduport.api.auth import SESSION_COOKIE, _match_api_key

    provided = (
        request.headers.get("X-API-Key")
        or request.headers.get("x-api-key")
        or ""
    ).strip()
    if not provided:
        raise AduPortError("缺少 X-API-Key", code="unauthorized", status_code=401)
    if tenants_configured() > 0 and _match_api_key(provided) is None:
        raise AduPortError("无效的 API Key", code="unauthorized", status_code=401)
    from aduport.api.auth import issue_session, session_ttl_seconds

    resp = JSONResponse(content={"ok": True, "session": "cookie"})
    # Signed tenant-bound cookie (multi-worker) or opaque handle — never raw API key.
    # Secure defaults on (ADUPORT_COOKIE_SECURE=1); set 0 for plain-HTTP local demos.
    resp.set_cookie(
        SESSION_COOKIE,
        issue_session(provided),
        httponly=True,
        samesite="strict",
        secure=bool(getattr(settings, "cookie_secure", True)),
        max_age=max(60, session_ttl_seconds()),
        path="/",
    )
    return resp


@router.delete("/auth/session")
async def clear_auth_session(request: Request) -> JSONResponse:
    from aduport.api.auth import SESSION_COOKIE, revoke_session

    revoke_session(request.cookies.get(SESSION_COOKIE, "") or "")
    resp = JSONResponse(content={"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/license")
async def license_status() -> dict[str, object]:
    """Public license status — machine_id omitted to limit fingerprinting (T23)."""
    from aduport.core.license import current_license

    info = current_license()
    return {
        "required": bool(settings.license_required),
        "license": info.public_dict() if info else None,
    }


@router.get("/formats")
async def list_formats() -> dict[str, object]:
    from aduport.importers.anydoc_bridge import (
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
        "export_modes": sorted(EXPORT_MODES),
        "structure_limits": {
            fmt: list(nodes) for fmt, nodes in TARGET_STRUCTURE_LIMITS.items()
        },
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
            "msg": "Outlook .msg via OLE CFB / MAPI strings (no olefile dep)",
            "ipynb": "Jupyter notebook → Markdown AST",
            "zip": "safe_zip multi-member convert (nested zip skipped)",
            "png_jpg": "image embed only unless an OCR engine is actually available",
            "rss_atom": "RSS 2.0 / Atom → Markdown AST (defusedxml)",
        },
        "page_grade": page_grade_public_contract(),
        "fidelity_tiers": {
            # Honest tiers (v15/v6): "full" = layout/semantic deep paths that ship
            # structure (tables/headings/images) on the default import path.
            # csv/txt keep markers, not native layout — demoted to text_markers (v6).
            # pdf/epub were wrongly listed as full — demoted (text/CSS/image gaps).
            "full": sorted(
                [
                    "md",
                    "html",
                    "json",
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
                    "ipynb",
                    "rss",
                    "atom",
                ]
            ),
            "structured_partial": sorted(["ods", "odp", "zip", "pdf", "epub"]),
            # Targets that preserve headings/tables as text markers, not layout.
            "text_markers": sorted(["csv", "txt"]),
            "text_level": sorted(
                [
                    "azw3",
                    "doc",
                    "kf8",
                    "mobi",
                    "msg",
                    "pot",
                    "pps",
                    "ppt",
                    "xls",
                    "xlsb",
                    "png",
                    "jpg",
                ]
            ),
            "export_shallow": sorted(["azw3", "kf8"]),
        },
        # External claim policy (v6 §06): what we will / will not say in marketing.
        "claim_policy": {
            "layout_targets": sorted(
                [
                    "md",
                    "html",
                    "json",
                    "latex",
                    "docx",
                    "pdf",
                    "pptx",
                    "epub",
                    "mobi",
                    "azw3",
                    "kf8",
                    "odt",
                    "ods",
                    "odp",
                    "xlsx",
                    "rtf",
                ]
            ),
            "text_markers_only_targets": sorted(["csv", "txt"]),
            "image_carry_only_sources": sorted(["jpg", "png"]),
            "content_success_excludes_sources": sorted(["jpg", "png"]),
            "note": (
                "18 个目标里 16 个可谈排版链路，csv/txt 只承诺文字与结构标记。"
                "jpg/png 作源不计入内容级成功率（无文本层，只有图片搬运）。"
                "矩阵合成样本的 100% 不能当排版保真对外承诺。"
            ),
        },
        "notes": {
            "csv": (
                "只承诺文字与结构标记，不承诺排版保真。"
                "表格写成网格。标题、引用、代码、列表和段落写在 _CONTENT 段，按 Markdown 读回；"
                "Excel 打开会看到 Markdown 源码列（段首有说明行）。"
                "纯表格导出：转换参数 csv_table_only=true。"
                "图片只写 [Image: 说明]，不内嵌字节。"
            ),
            "export_only": (
                "当前无仅导出格式；latex/azw3/kf8 已支持导入（轻量解析）"
                if not export_only
                else "下列格式仅支持导出，不支持作为源格式导入"
            ),
            "msg": "Outlook .msg：OLE/MAPI 主题/收发件人/正文；附件 by-value 展开（嵌套 .msg 跳过）",
            "ipynb": "Jupyter：markdown/code/raw cell → AST；输出流不抽取",
            "zip": "ZIP：safe_zip 限额内逐成员转换；嵌套 zip 跳过；最多 64 成员",
            "png_jpg": (
                "图片搬运，不是图片转文字；作源时不计入内容级成功率。"
                "图像嵌入 AST。"
                "未启用 OCR 或引擎不可用时，正文与导出警告都写明"
                "「未启用 OCR，仅嵌入图片」或「OCR 不可用，仅嵌入图片」。"
                "ADUPORT_OCR_ENABLED 且引擎可用时才经 PDF 包装提取文字。"
            ),
            "export_modes": (
                "rag 不是文件格式，是 aduport.rag_elements 导出模式，不计入目标格式枚举。"
                "转换接口仍接受 rag / elements / rag_elements / rag-json。"
            ),
            "iwork": (
                "pages / numbers / keynote 已摘牌，导入直接 UnsupportedFormatError。"
                "解析代码留在 iwork_importer，不修明文 IWA 正则："
                "真实 iWork '13+ 正文在 Snappy 压缩的 IWA 中。"
            ),
            "rss": "RSS 2.0 / Atom：频道与条目 → Markdown；HTML 正文轻量剥标签",
            "atom": "与 rss 同源导入器；扩展名/嗅探区分",
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
            "txt": (
                "只承诺文字与结构标记，不承诺排版保真。"
                "按 Markdown 读回。表格是 GFM。图片只写 [Image: 说明]，不内嵌字节，读回不是图片节点。"
            ),
            "claim_policy": (
                "对外宣称：16 个目标可谈排版链路；csv/txt 只谈文字与标记；"
                "jpg/png 作源不计内容成功率。合成矩阵 100% ≠ 排版保真。"
            ),
            "rtf": (
                "标题用字号，表格用 \\trowd，图片用 \\pict\\pngblip 或 \\jpegblip，导入还原成节点。"
            ),
            "structure_limits": (
                "当前为空。pdf / rtf 的自有导出再导入会保留标题、表格和图片。"
                "csv / txt 保留标题和表格的文字，图片是占位符。"
                "别人做的 PDF、没有 _CONTENT 的 CSV、以及纯散文 txt，仍可能只有段落。"
            ),
            "pdf": (
                "fidelity_tiers 现为 structured_partial（非 full）。"
                "自有 PDF 再导入保留标题、表格和图片（WeasyPrint 嵌入光栅；"
                "pdf_simple 用 ADUPORT-IMG 文本块）。"
                "默认 auto：本地 docling 模型可用时优先走 docling 结构导入"
                "（ADUPORT_PDF_AUTO_PREFER_DOCLING=true）；"
                "否则文本层足够时走 flat-text，metadata.fidelity_warnings 提示降级。"
                "扫描 OCR 默认关（ADUPORT_OCR_ENABLED）。"
                "导出 WeasyPrint 优先，否则 pdf_simple（显式 export_warnings）；"
                "默认不嵌入 %%ADUPORT-AST sidecar（ADUPORT_PDF_AST_SIDECAR=0）；"
                "同格式 identity 直通不计入 conversions_total"
            ),
            "epub": (
                "fidelity_tiers 现为 structured_partial（非 full）。"
                "spine HTML→AST；图片从 EPUB zip 解析嵌入；"
                "CSS 主题提取至 metadata.epub_css（manifest + inline，有上限）；"
                "全书复杂主题仍可能 partial。"
            ),
            "anydoc": (
                "anydoc 为 vendored 加速层（scripts/build_native.sh）；"
                "缺省时绝大多数源格式仍有 Python 内置路径（OLE 系为降级文本）。"
                "例外：真 xlsb 无 anydoc 时硬失败（见 notes.xlsb）"
            ),
            "docx": (
                "导入默认 python-docx 内置路径，docling 可选增强；"
                "行内保真深度路径依赖 vendored anydoc（关闭后 AST 覆盖下降，见 ANYDOC_RISK）；"
                "导出主题：Form docx_theme 或 ADUPORT_DOCX_THEME=default|academic|minimal；"
                "批注：AST Comment → Word comments.xml（ADUPORT_DOCX_EXPORT_COMMENTS）；"
                "脚注：AST Footnote → word/footnotes.xml；"
                "图表：DrawingML 文本级活（非可编辑原生图表，ACCEPTED）；"
                "标题编号：ADUPORT_HEADING_NUMBERING=true；"
                "MathBlock→OMML（frac/sqrt/sum/int/delim/希腊字母/matrix/cases）；"
                "SmartArt：DrawingML 版式组图 + diagrams 四件套；"
                "OLE：embeddings/*.bin 二进制嵌入往返"
            ),
            "xlsx": (
                "表格值导出：数值/日期类型推断 + mergeCells/列宽；"
                f"工作表上限 ADUPORT_XLSX_MAX_SHEETS（默认 {settings.xlsx_max_sheets}）；"
                "默认不写 `_AST` meta sheet（ADUPORT_XLSX_AST_SHEET=0）— 表体/Content 级；"
                "开启后 Chart/Comment/HtmlBlock 等可走 sidecar 往返，不得计入单元格保真；"
                "Chart AST → DrawingML（xl/charts + drawing on sheet1）；"
                "Table.cells 在 rows 为空时仍可导出；xlsb/ods 经导入后可落到 xlsx"
            ),
            "ocr": (
                "扫描 PDF（T37）：默认关闭。"
                "启用：ADUPORT_OCR_ENABLED=true + pip install 'aduport[ocr-mineru]' 或 [ocr-paddle]；"
                "能力边界见本接口 notes + /api/health/detail.ocr；"
                "文本层不足时错误体含 ocr_required=true 与 install 提示；"
                "数字 PDF 不依赖 OCR（pypdfium2 文本层内置）。未装 OCR 时扫描件不会静默冒充成功 OCR。"
            ),
            "jobs": (
                "异步：POST /api/jobs 返回 access_token（仅创建）；"
                "GET /api/jobs/{id} 与 /result 需 X-Job-Token 或 ?token=（ADUPORT_JOB_TOKEN_REQUIRED，默认开）；"
                "可选 callback_url Webhook；元数据 ADUPORT_JOB_DB_PATH；结果 ADUPORT_JOB_RESULT_DIR"
            ),
        },
    }


@router.get("/metrics")
async def prometheus_metrics() -> Response:
    # Omit per-tenant series so a scrape cannot enumerate other tenants.
    return Response(
        content=metrics.prometheus_text(include_tenants=False),
        media_type="text/plain; version=0.0.4",
    )


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
    rag_max_chars: Optional[str] = Form(None),
    rag_chunk_overlap: Optional[str] = Form(None),
    embed: Optional[str] = Form(None),
    embed_backend: Optional[str] = Form(None),
    embed_model: Optional[str] = Form(None),
    vector_persist: Optional[str] = Form(None),
    vector_collection: Optional[str] = Form(None),
    pdf_engine: Optional[str] = Form(None),
    ocr_engine: Optional[str] = Form(None),
    force_ocr: Optional[str] = Form(None),
    ocr_max_pages: Optional[str] = Form(None),
    skip_pdf_sidecar: Optional[str] = Form(None),
    cache_bypass: Optional[str] = Form(None),
    docx_auto_toc: Optional[str] = Form(None),
    latex_plain_comments: Optional[str] = Form(None),
    csv_table_only: Optional[str] = Form(None),
    callback_url: Optional[str] = Form(None),
) -> JSONResponse:
    """Enqueue an async conversion job; poll GET /api/jobs/{id} then download result."""
    await _reject_unknown_form_fields(request)
    spill = await read_upload_streaming(file, prefix="aduport-job-up-")
    try:
        filename = file.filename or "input"
        client = request.client.host if request.client else ""
        tid = current_tenant_id()
        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
            rag_max_chars, rag_chunk_overlap,
            embed, embed_backend, embed_model, vector_persist, vector_collection,
            pdf_engine, ocr_engine, force_ocr, ocr_max_pages, skip_pdf_sidecar,
            cache_bypass, docx_auto_toc, latex_plain_comments, csv_table_only,
        )
        loop = asyncio.get_running_loop()

        def _enqueue():
            content = spill.read()
            src = _resolve_source_format(source_format, filename, content)
            tgt = _target_or_raise(target_format)
            out_name = _safe_output_filename(
                filename if file.filename else f"input.{src}", tgt
            )
            try:
                from aduport.core.webhooks import validate_callback_url

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
async def list_jobs(request: Request) -> dict[str, object]:
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    jobs = await loop.run_in_executor(
        None, lambda: job_store.list_recent(tenant_id=tid)
    )
    # Anonymous mode: only jobs created from this client, so job ids/filenames
    # of other anonymous callers are not enumerable.
    if not (tid or "").strip():
        host = request.client.host if request.client else ""
        jobs = [j for j in jobs if (getattr(j, "client_tag", "") or "") == host and host]
    return {"jobs": [j.to_dict() for j in jobs]}


def _job_token_from_request(request: Request) -> str:
    header = (
        request.headers.get("X-Job-Token")
        or request.headers.get("x-job-token")
        or ""
    )
    if header:
        return header
    if settings.job_token_query:
        return request.query_params.get("token") or ""
    return ""


def _require_job_token(job: object, request: Request) -> None:
    if not getattr(settings, "job_token_required", True):
        return
    token = _job_token_from_request(request)
    if not getattr(job, "token_ok", lambda _t: True)(token):
        raise AduPortError(
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
        raise AduPortError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    _require_job_token(job, request)
    return job.to_dict()


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request) -> StreamingResponse:
    """SSE progress stream for async jobs (§6.4)."""
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    job = await loop.run_in_executor(
        None, lambda: job_store.get(job_id, tenant_id=tid)
    )
    if not job:
        raise AduPortError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    _require_job_token(job, request)

    async def event_stream():
        terminal = {JobStatus.done.value, JobStatus.error.value}
        while True:
            if await request.is_disconnected():
                break
            current = await loop.run_in_executor(
                None, lambda: job_store.get(job_id, tenant_id=tid, enforce_tenant=True)
            )
            if not current:
                payload = json.dumps({"code": "job_not_found"}, ensure_ascii=False)
                yield f"event: error\ndata: {payload}\n\n"
                break
            payload = json.dumps(current.to_dict(), ensure_ascii=False)
            yield f"event: progress\ndata: {payload}\n\n"
            if current.status.value in terminal:
                break
            await asyncio.sleep(0.5)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.post("/jobs/{job_id}/resume")
async def resume_job(job_id: str, request: Request) -> dict[str, object]:
    """Resume an interrupted/failed job when input blob still exists (§6.4)."""
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    # Auth first against current snapshot/token
    existing = await loop.run_in_executor(
        None, lambda: job_store.get(job_id, tenant_id=tid)
    )
    if existing:
        _require_job_token(existing, request)
    else:
        # Allow resume from disk snapshot: token via header still required if enforced
        from aduport.core.job_persist import load_job_snapshot

        snap = await loop.run_in_executor(None, lambda: load_job_snapshot(job_id))
        if not snap:
            raise AduPortError(
                f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404
            )
        # Reconstruct minimal job for token check
        temp = await loop.run_in_executor(
            None, lambda: job_store.get(job_id, tenant_id=tid, enforce_tenant=False)
        )
        if temp:
            _require_job_token(temp, request)

    def _do():
        return job_store.resume(job_id, tenant_id=tid, client=request.client.host if request.client else "")

    job = await loop.run_in_executor(None, _do)
    return job.to_dict(include_token=True)


@router.post("/jobs/{job_id}/reprocess")
async def reprocess_job(
    job_id: str,
    request: Request,
    force_ocr: Optional[str] = Form(None),
    pdf_engine: Optional[str] = Form(None),
    ocr_engine: Optional[str] = Form(None),
    ocr_max_pages: Optional[str] = Form(None),
    skip_pdf_sidecar: Optional[str] = Form(None),
    cache_bypass: Optional[str] = Form(None),
    embed: Optional[str] = Form(None),
    embed_backend: Optional[str] = Form(None),
    vector_persist: Optional[str] = Form(None),
    rag_max_chars: Optional[str] = Form(None),
    rag_chunk_overlap: Optional[str] = Form(None),
) -> dict[str, object]:
    """Re-run a job with optional OCR/RAG overrides (input must still be retained)."""
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    existing = await loop.run_in_executor(
        None, lambda: job_store.get(job_id, tenant_id=tid)
    )
    if existing:
        _require_job_token(existing, request)
    else:
        from aduport.core.job_persist import load_job_snapshot

        snap = await loop.run_in_executor(None, lambda: load_job_snapshot(job_id))
        if not snap:
            raise AduPortError(
                f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404
            )
        temp = await loop.run_in_executor(
            None, lambda: job_store.get(job_id, tenant_id=tid, enforce_tenant=False)
        )
        if not temp or not job_store._tenant_allows(getattr(temp, "tenant_id", None), tid):
            raise AduPortError(
                f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404
            )
        _require_job_token(temp, request)

    opts_update = _pdf_form_opts(
        None,
        None,
        docx_theme=None,
        rag_max_chars=rag_max_chars,
        rag_chunk_overlap=rag_chunk_overlap,
        embed=embed,
        embed_backend=embed_backend,
        vector_persist=vector_persist,
        pdf_engine=pdf_engine,
        ocr_engine=ocr_engine,
        force_ocr=force_ocr,
        ocr_max_pages=ocr_max_pages,
        skip_pdf_sidecar=skip_pdf_sidecar,
        cache_bypass=cache_bypass,
    )
    # Drop empty PDF layout keys from partial form
    opts_update = {
        k: v
        for k, v in opts_update.items()
        if v is not None
        and k
        not in (
            "pdf_font_family",
            "pdf_custom_css",
            "pdf_page_size",
            "pdf_orientation",
            "pdf_header",
            "pdf_footer",
            "pdf_page_numbers",
            "pdf_toc",
        )
    }

    def _do():
        return job_store.reprocess(
            job_id,
            opts_update=opts_update,
            tenant_id=tid,
            client=request.client.host if request.client else "",
        )

    job = await loop.run_in_executor(None, _do)
    return job.to_dict(include_token=True)


@router.post("/rag/layout_feedback")
async def rag_layout_feedback(request: Request) -> dict[str, object]:
    """Patch RAG payload spans from client corrections (no engine retrain)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise InvalidDocumentError("需要 JSON body") from exc
    if not isinstance(body, dict):
        raise InvalidDocumentError("JSON 须为对象")
    payload = body.get("payload") or body.get("rag") or body
    feedback = body.get("feedback") or body.get("corrections") or []
    if not isinstance(payload, dict) or "elements" not in payload:
        raise InvalidDocumentError("缺少 rag payload.elements")
    if not isinstance(feedback, list):
        raise InvalidDocumentError("feedback 须为数组")
    from aduport.importers.layout_feedback import apply_layout_feedback_to_rag

    out = apply_layout_feedback_to_rag(dict(payload), feedback)
    return out


@router.post("/vectors/search")
async def vectors_search(request: Request) -> dict[str, object]:
    """Search the local JSONL vector store (requires prior embed+persist)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise InvalidDocumentError("需要 JSON body") from exc
    if not isinstance(body, dict):
        raise InvalidDocumentError("JSON 须为对象")
    query = str(body.get("query") or "").strip()
    if not query:
        raise InvalidDocumentError("query 不能为空")
    try:
        top_k = int(body.get("top_k") or 5)
    except (TypeError, ValueError) as exc:
        raise InvalidDocumentError("top_k 须为整数") from exc
    if top_k < 1 or top_k > 50:
        raise InvalidDocumentError("top_k 须在 1–50")
    backend = body.get("embed_backend")
    if backend is not None and str(backend).strip():
        backend = _choice(str(backend), _EMBED_BACKENDS, "embed_backend")
    from aduport.core.vector_store import search as vector_search

    tid = current_tenant_id() or "default"
    return vector_search(
        query,
        collection=str(body.get("collection") or settings.vector_collection or "default"),
        top_k=top_k,
        backend=backend,
        model=body.get("embed_model"),
        tenant_id=tid,
    )


@router.get("/jobs/{job_id}/result")
async def get_job_result(job_id: str, request: Request) -> Response:
    loop = asyncio.get_running_loop()
    tid = current_tenant_id()
    job, result = await loop.run_in_executor(
        None, lambda: job_store.read_result_safe(job_id, tenant_id=tid)
    )
    if not job:
        raise AduPortError(f"任务不存在或已过期: {job_id}", code="job_not_found", status_code=404)
    _require_job_token(job, request)
    from aduport.core.i18n_errors import error_body

    if job.status == JobStatus.error:
        return JSONResponse(
            status_code=422,
            content=error_body(
                job.code or "conversion_error",
                job.error or "转换失败",
                422,
            ),
        )
    if job.status != JobStatus.done or result is None:
        return JSONResponse(
            status_code=409,
            content=error_body(
                "job_not_ready",
                f"任务尚未完成（状态: {job.status.value}）",
                409,
                progress=job.progress,
            ),
        )
    media_type = MIME_TYPES.get(job.target_format, "application/octet-stream")
    name = job.result_filename or f"converted.{job.target_format}"
    headers = {"Content-Disposition": _content_disposition(name)}
    headers.update(
        _export_warning_headers(
            getattr(job, "export_warnings", None) or [],
            body=result,
        )
    )
    return Response(
        content=result,
        media_type=media_type,
        headers=headers,
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
    rag_max_chars: Optional[str] = Form(None),
    rag_chunk_overlap: Optional[str] = Form(None),
    embed: Optional[str] = Form(None),
    embed_backend: Optional[str] = Form(None),
    embed_model: Optional[str] = Form(None),
    vector_persist: Optional[str] = Form(None),
    vector_collection: Optional[str] = Form(None),
    pdf_engine: Optional[str] = Form(None),
    ocr_engine: Optional[str] = Form(None),
    force_ocr: Optional[str] = Form(None),
    ocr_max_pages: Optional[str] = Form(None),
    skip_pdf_sidecar: Optional[str] = Form(None),
    cache_bypass: Optional[str] = Form(None),
    docx_auto_toc: Optional[str] = Form(None),
    latex_plain_comments: Optional[str] = Form(None),
    csv_table_only: Optional[str] = Form(None),
) -> Response:
    await _reject_unknown_form_fields(request)
    spill = await read_upload_streaming(file, prefix="aduport-cvt-up-")
    try:
        filename = file.filename or "input"
        client = request.client.host if request.client else ""
        pdf_opts = _pdf_form_opts(
            pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
            pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
            rag_max_chars, rag_chunk_overlap,
            embed, embed_backend, embed_model, vector_persist, vector_collection,
            pdf_engine, ocr_engine, force_ocr, ocr_max_pages, skip_pdf_sidecar,
            cache_bypass, docx_auto_toc, latex_plain_comments, csv_table_only,
        )
        loop = asyncio.get_running_loop()

        def _convert() -> tuple[bytes, str, list[str]]:
            content = spill.read()
            src = _resolve_source_format(source_format, filename, content)
            tgt = _target_or_raise(target_format)
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
            from aduport.exporters.export_warnings import get_last_export_warnings

            return out, tgt, get_last_export_warnings()

        result, tgt, warns = await loop.run_in_executor(_executor, _convert)

        out_name = _safe_output_filename(file.filename, tgt)
        media_type = MIME_TYPES.get(tgt, "application/octet-stream")
        headers = {"Content-Disposition": _content_disposition(out_name)}
        if tgt == "pdf" and b"aduport-pdf-degraded: true" in result[:512]:
            headers["X-AduPort-PDF-Engine"] = "pdf_simple"
            headers["X-AduPort-PDF-Degraded"] = "true"
        headers.update(_export_warning_headers(warns, body=result))
        if warns:
            for warning in warns:
                if str(warning).startswith("source-encoding:"):
                    headers["X-AduPort-Encoding"] = str(warning).split(":", 1)[1].strip()
                    break
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
    rag_max_chars: Optional[str] = Form(None),
    rag_chunk_overlap: Optional[str] = Form(None),
    embed: Optional[str] = Form(None),
    embed_backend: Optional[str] = Form(None),
    embed_model: Optional[str] = Form(None),
    vector_persist: Optional[str] = Form(None),
    vector_collection: Optional[str] = Form(None),
    pdf_engine: Optional[str] = Form(None),
    ocr_engine: Optional[str] = Form(None),
    force_ocr: Optional[str] = Form(None),
    ocr_max_pages: Optional[str] = Form(None),
    skip_pdf_sidecar: Optional[str] = Form(None),
    cache_bypass: Optional[str] = Form(None),
    docx_auto_toc: Optional[str] = Form(None),
    latex_plain_comments: Optional[str] = Form(None),
    csv_table_only: Optional[str] = Form(None),
) -> Response:
    """
    Batch convert multiple uploaded files (bounded concurrency).

    Successful conversions are returned as a ZIP archive.
    Partial failures recorded in errors.json inside the ZIP.
    When source_format is omitted or "auto", each file is detected independently.
    """
    await _reject_unknown_form_fields(request)
    tgt = _target_or_raise(target_format)
    if not files:
        raise EmptyFileError("未上传任何文件")
    if len(files) > settings.max_batch_files:
        raise InvalidDocumentError(
            f"批量文件数超限：最多 {settings.max_batch_files} 个，当前 {len(files)}"
        )

    pdf_opts = _pdf_form_opts(
        pdf_font_family, pdf_custom_css, pdf_page_size, pdf_orientation,
        pdf_header, pdf_footer, pdf_page_numbers, pdf_toc, docx_theme,
        rag_max_chars, rag_chunk_overlap,
        embed, embed_backend, embed_model, vector_persist, vector_collection,
        pdf_engine, ocr_engine, force_ocr, ocr_max_pages, skip_pdf_sidecar,
        cache_bypass, docx_auto_toc, latex_plain_comments, csv_table_only,
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
            upload, prefix="aduport-batch-", max_bytes=remaining
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

    async def _one(
        name: str, content: bytes
    ) -> tuple[str, bytes | None, dict | None, list[str]]:
        from aduport.core.i18n_errors import translate_message
        from aduport.core.upload_limit import upload_byte_limit

        async with sem:
            try:
                if not content:
                    raise EmptyFileError(f"文件为空: {name}")
                if len(content) > upload_byte_limit():
                    raise FileTooLargeError(
                        f"文件过大: {name}，限制 {settings.max_upload_mb} MB"
                    )
                src = _resolve_source_format(source_format, name, content)

                def _run() -> tuple[bytes, list[str]]:
                    converted = convert_isolated(
                        content,
                        src,
                        tgt,
                        filename=name,
                        client=client,
                        **pdf_opts,
                    )
                    from aduport.exporters.export_warnings import get_last_export_warnings

                    return converted, list(get_last_export_warnings())

                converted, warns = await loop.run_in_executor(_executor, _run)
                return _safe_output_filename(name, tgt), converted, None, warns
            except AduPortError as exc:
                return name, None, {
                    "file": name,
                    "code": exc.code,
                    "message": exc.message,
                    "message_en": translate_message(exc.message, exc.code),
                }, []
            except Exception:
                _log_route.exception("batch convert failed for %s", name)
                note = "转换失败，请检查文件格式"
                return name, None, {
                    "file": name,
                    "code": "conversion_error",
                    "message": note,
                    "message_en": translate_message(note, "conversion_error"),
                }, []

    results = await asyncio.gather(*[_one(n, c) for n, c in payloads])

    successes: list[tuple[str, bytes]] = []
    failures: list[dict[str, str]] = []
    batch_warnings: list[str] = []
    for out_name, data, err, warns in results:
        if err:
            failures.append(err)
        elif data is not None:
            successes.append((out_name, data))
            batch_warnings.extend(f"{out_name}: {item}" for item in warns)

    if not successes:
        from aduport.core.i18n_errors import error_body

        return JSONResponse(
            status_code=422,
            content=error_body(
                "batch_failed",
                "批量转换全部失败",
                422,
                total=len(files),
                succeeded=0,
                failed=len(failures),
                items=failures,
            ),
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

    headers = {
        "Content-Disposition": _content_disposition("converted_batch.zip"),
        "X-Batch-Total": str(len(files)),
        "X-Batch-Succeeded": str(len(successes)),
        "X-Batch-Failed": str(len(failures)),
    }
    headers.update(_export_warning_headers(batch_warnings))
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers=headers,
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
