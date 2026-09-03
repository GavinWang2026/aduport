"""Async helpers: chunked upload read into SpillBuffer (true streaming ingress)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

from fastapi import UploadFile

from app.config import settings
from app.core.exceptions import EmptyFileError, FileTooLargeError, UploadTimeoutError
from app.core.spill import SpillBuffer


async def read_upload_streaming(
    upload: UploadFile,
    *,
    chunk_size: int = 1024 * 1024,
    prefix: str = "mdbridge-up-",
    max_bytes: int | None = None,
) -> SpillBuffer:
    """
    Read UploadFile in chunks. Once size crosses spill threshold, switch to a
    temp file so peak RAM stays near chunk_size + threshold (not full file).

    ``max_bytes`` caps this member (D-28 remaining batch budget). D-24: each
    chunk and the whole body are bounded by ``upload_timeout_seconds``.
    """
    limit = settings.max_upload_bytes if max_bytes is None else min(
        settings.max_upload_bytes, max_bytes
    )
    threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
    mem = bytearray()
    path: str | None = None
    fd: int | None = None
    total = 0
    timeout_s = float(getattr(settings, "upload_timeout_seconds", 60.0) or 60.0)
    deadline = time.monotonic() + max(1.0, timeout_s)

    try:
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise UploadTimeoutError("上传超时（慢速 body）")
            try:
                chunk = await asyncio.wait_for(
                    upload.read(chunk_size),
                    timeout=remain,
                )
            except TimeoutError as exc:
                raise UploadTimeoutError("上传超时（慢速 body）") from exc
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise FileTooLargeError(
                    f"文件过大，限制 {limit} bytes（当前 ≥{total} bytes）"
                )

            if path is None and threshold > 0 and total >= threshold:
                fd, path = tempfile.mkstemp(prefix=prefix, suffix=".bin")
                if mem:
                    os.write(fd, mem)
                    mem.clear()
                os.write(fd, chunk)
                os.fchmod(fd, 0o600)
            elif path is not None and fd is not None:
                os.write(fd, chunk)
            else:
                mem.extend(chunk)
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            fd = None
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    if total == 0:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise EmptyFileError("上传文件为空")

    if path:
        return SpillBuffer.from_path(path, total)

    return SpillBuffer(bytes(mem), prefix=prefix)
