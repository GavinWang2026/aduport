"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from aduport import __version__
from aduport.api.auth import APIKeyMiddleware
from aduport.api.rate_limit import RateLimitMiddleware
from aduport.api.routes import router
from aduport.api.tools_routes import router as tools_router
from aduport.config import settings
from aduport.core.exceptions import AduPortError

_log = logging.getLogger("aduport")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # v6/T3-#17 / D-18: surface misconfigured DEV_OK at boot (never silent).
    import logging
    import os

    from aduport.core.warmup import start_warmup_background

    _auth_log = logging.getLogger("aduport.auth")
    _boot = logging.getLogger("aduport.boot")
    dev_set = (os.environ.get("ADUPORT_DEV_OK") or "").strip() in ("1", "true", "TRUE", "yes")
    if dev_set and not settings.debug:
        _auth_log.error(
            "startup assert: ADUPORT_DEV_OK is set but debug=False — auth bypass DISABLED"
        )
    elif dev_set and settings.debug:
        _auth_log.warning("startup: ADUPORT_DEV_OK active (debug auth bypass)")

    # PDF export self-check (WeasyPrint + system libs)
    try:
        import weasyprint  # noqa: F401

        _boot.info(
            "PDF export: WeasyPrint ok (%s)",
            getattr(weasyprint, "__version__", "?"),
        )
    except Exception as exc:
        _boot.warning(
            "PDF export: WeasyPrint unavailable (%s) — falling back to pdf_simple. "
            "macOS: brew install pango gdk-pixbuf libffi",
            exc,
        )

    # Multi-worker rate-limit hint
    if int(settings.rate_limit_per_minute or 0) > 0 and not (settings.redis_url or "").strip():
        workers = int(os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS") or "1")
        if workers > 1:
            _boot.warning(
                "rate_limit_per_minute=%s with %s workers but ADUPORT_REDIS_URL unset — "
                "in-memory limiter is per-process only",
                settings.rate_limit_per_minute,
                workers,
            )

    start_warmup_background()
    yield


_docs_on = settings.api_docs_enabled
app = FastAPI(
    title=settings.app_name,
    description="Local document bidirectional converter (PDF/DOCX/Markdown/TXT/HTML/PPTX/EPUB/MOBI)",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)

origins = settings.cors_origin_list()
allow_credentials = settings.cors_allow_credentials and origins != ["*"]

# Starlette: last add_middleware = outermost. Auth/rate-limit must sit inside CORS
# so browser OPTIONS preflight is never answered with 401.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    from aduport.core.upload_limit import oversize_response

    rejected = oversize_response(request)
    if rejected is not None:
        return rejected
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(router)
app.include_router(tools_router)


@app.exception_handler(AduPortError)
async def aduport_error_handler(_request: Request, exc: AduPortError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    from aduport.core.i18n_errors import error_body

    return JSONResponse(
        status_code=422,
        content=error_body(
            "validation_error",
            "请求参数校验失败",
            422,
            details=exc.errors() if settings.debug else None,
        ),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    from aduport.core.i18n_errors import error_body

    _log.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content=error_body(
            "internal_error",
            "服务器内部错误，请检查文件格式或稍后重试",
            500,
        ),
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "AduPort API", "docs": "/docs", "version": __version__}
