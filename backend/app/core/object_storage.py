"""Optional long-term job result archive (local file:// or S3-compatible)."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings

_log = logging.getLogger("mdbridge.object_storage")


def storage_info() -> dict[str, object]:
    uri = (getattr(settings, "object_storage_uri", "") or "").strip()
    if not uri:
        return {"enabled": False, "backend": None}
    parsed = urlparse(uri)
    backend = parsed.scheme or "path"
    return {
        "enabled": True,
        "backend": backend,
        "uri": uri,
        "retention_days": int(getattr(settings, "job_archive_retention_days", 30) or 30),
    }


def archive_job_object(job_id: str, data: bytes) -> str | None:
    """Copy finished job bytes to archive store; return URI/path string."""
    uri = (getattr(settings, "object_storage_uri", "") or "").strip()
    if not uri or not data:
        return None
    safe = "".join(c for c in job_id if c.isalnum() or c in "-_")
    if not safe:
        return None
    parsed = urlparse(uri)
    if parsed.scheme in ("file", ""):
        root = Path(parsed.path if parsed.scheme == "file" else uri).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{safe}.bin"
        try:
            path.write_bytes(data)
            return f"file://{path}"
        except OSError as exc:
            _log.warning("archive write failed %s: %s", path, exc)
            return None
    if parsed.scheme == "s3":
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError:
            _log.warning("object_storage_uri is s3:// but boto3 is not installed")
            return None
        bucket = parsed.netloc
        prefix = parsed.path.lstrip("/")
        key = f"{prefix}/{safe}.bin" if prefix else f"{safe}.bin"
        try:
            client = boto3.client("s3")
            client.put_object(Bucket=bucket, Key=key, Body=data)
            return f"s3://{bucket}/{key}"
        except Exception as exc:
            _log.warning("s3 archive failed %s: %s", key, exc)
            return None
    _log.warning("unsupported object_storage_uri scheme: %s", parsed.scheme)
    return None
