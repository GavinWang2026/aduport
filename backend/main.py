"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.auth import APIKeyMiddleware
from app.api.rate_limit import RateLimitMiddleware
from app.api.routes import router
from app.config import settings
from app.core.exceptions import MdBridgeError


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from app.core.warmup import start_warmup_background

    # v6/T3-#17 / D-18: surface misconfigured DEV_OK at boot (never silent).
    import logging
    import os

    _auth_log = logging.getLogger("mdbridge.auth")
    dev_set = (os.environ.get("MDBRIDGE_DEV_OK") or "").strip() in ("1", "true", "TRUE", "yes")
    if dev_set and not settings.debug:
        _auth_log.error(
            "startup assert: MDBRIDGE_DEV_OK is set but debug=False — auth bypass DISABLED"
        )
    elif dev_set and settings.debug:
        _auth_log.warning("startup: MDBRIDGE_DEV_OK active (debug auth bypass)")

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
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(router)


@app.exception_handler(MdBridgeError)
async def mdbridge_error_handler(_request: Request, exc: MdBridgeError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": True,
            "code": "validation_error",
            "message": "请求参数校验失败",
            "status_code": 422,
            "details": exc.errors() if settings.debug else None,
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    message = str(exc) if settings.debug else "服务器内部错误，请检查文件格式或稍后重试"
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "code": "internal_error",
            "message": message,
            "status_code": 500,
        },
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": "md-bridge API", "docs": "/docs", "version": __version__}
