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

    start_warmup_background()
    yield


app = FastAPI(
    title=settings.app_name,
    description="Local document bidirectional converter (PDF/DOCX/Markdown/TXT/HTML/PPTX/EPUB/MOBI)",
    version=__version__,
    lifespan=lifespan,
)

origins = settings.cors_origin_list()
allow_credentials = settings.cors_allow_credentials and origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(APIKeyMiddleware)

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
