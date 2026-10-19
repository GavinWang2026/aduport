"""Shared upload ceilings for every write route.

File readers stop at ``upload_byte_limit``. The HTTP middleware rejects a
``Content-Length`` above ``body_ceiling`` before the handler runs. Batch uses
the larger total, plus a small MIME slack so a legal multipart is not refused
for its boundary bytes.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

from aduport.config import settings


def upload_byte_limit() -> int:
    return int(settings.max_upload_bytes)


def batch_byte_limit() -> int:
    return int(settings.max_batch_total_bytes)


def body_ceiling(path: str) -> int:
    base = batch_byte_limit() if path.rstrip("/").endswith("/convert/batch") else upload_byte_limit()
    slack = min(2 * 1024 * 1024, max(1024, base // 32))
    return base + slack


def oversize_response(request) -> JSONResponse | None:
    """413 when a write to ``/api`` announces a body over the route ceiling."""
    if request.method not in {"POST", "PUT", "PATCH"}:
        return None
    path = request.url.path
    if not path.startswith("/api/"):
        return None
    raw = request.headers.get("content-length")
    if not raw:
        return None
    try:
        announced = int(raw)
    except ValueError:
        return None
    ceiling = body_ceiling(path)
    if announced <= ceiling:
        return None
    from aduport.core.exceptions import FileTooLargeError

    err = FileTooLargeError(
        f"文件过大，限制 {ceiling} bytes（当前 ≥{announced} bytes）"
    )
    return JSONResponse(status_code=err.status_code, content=err.to_dict())
