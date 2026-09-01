"""Run ``convert`` in a child process so native aborts do not kill the API."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.converter import convert
from app.core.exceptions import ExportError, MdBridgeError

_log = logging.getLogger("mdbridge.isolate")
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _skip_isolate_for_ocr_pdf(source_format: str, filename: str) -> bool:
    """Skip isolate only when Paddle is allowed on Darwin (known SIGTRAP → CrashReporter).

    With Paddle disabled (macOS default), keep isolation so long MinerU OCR cannot
    freeze the API event loop / GIL in the parent process.
    """
    if sys.platform != "darwin" or not settings.ocr_enabled:
        return False
    # Import lazily to avoid circular imports at module load
    try:
        from app.importers.ocr_engine import paddle_ocr_allowed

        if not paddle_ocr_allowed():
            return False
    except Exception:
        return False
    fmt = (source_format or "").lower().strip()
    name = (filename or "").lower()
    return fmt in ("pdf", "auto") or name.endswith(".pdf")


def convert_isolated(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    filename: str = "",
    *,
    client: str = "",
    skip_magic: bool = False,
    fetch_images: bool | None = None,
    **pdf_opts: Any,
) -> bytes:
    """Convert with optional subprocess isolation (``MDBRIDGE_CONVERT_ISOLATE``)."""
    if not settings.convert_isolate or _skip_isolate_for_ocr_pdf(source_format, filename):
        if settings.convert_isolate and _skip_isolate_for_ocr_pdf(source_format, filename):
            _log.info(
                "skip convert isolate on Darwin OCR/PDF (avoid CrashReporter): %s",
                filename or source_format,
            )
        return convert(
            source_bytes,
            source_format,
            target_format,
            filename=filename,
            client=client,
            skip_magic=skip_magic,
            fetch_images=fetch_images,
            **pdf_opts,
        )

    with tempfile.TemporaryDirectory(prefix="mdbridge-iso-") as tmp:
        td = Path(tmp)
        input_path = td / "input.bin"
        out_path = td / "output.bin"
        req_path = td / "req.json"
        status_path = td / "status.json"
        input_path.write_bytes(source_bytes)
        req = {
            "input_path": str(input_path),
            "source_format": source_format,
            "target_format": target_format,
            "filename": filename,
            "client": client,
            "skip_magic": skip_magic,
            "fetch_images": fetch_images,
            **{k: v for k, v in pdf_opts.items()},
        }
        req_path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        # Ensure worker can import ``app`` when launched from any cwd
        pp = env.get("PYTHONPATH", "")
        root = str(_BACKEND_ROOT)
        env["PYTHONPATH"] = f"{root}{os.pathsep}{pp}" if pp else root
        # Avoid nested warmup storms in short-lived workers
        env.setdefault("MDBRIDGE_WARMUP_ON_STARTUP", "false")
        # Propagate OCR cache home into worker (PaddleX writes under HOME)
        ocr_home = (settings.ocr_home or env.get("MDBRIDGE_OCR_HOME") or "").strip()
        if ocr_home:
            Path(ocr_home).mkdir(parents=True, exist_ok=True)
            env["MDBRIDGE_OCR_HOME"] = ocr_home
            env["HOME"] = ocr_home
            env.setdefault("XDG_CACHE_HOME", str(Path(ocr_home) / "cache"))
        env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        cmd = [
            sys.executable,
            "-m",
            "app.core.convert_worker",
            str(req_path),
            str(out_path),
            str(status_path),
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=root,
                env=env,
                capture_output=True,
                timeout=max(30, int(settings.convert_timeout_seconds)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            _log.error("convert worker timeout: %s", filename)
            raise ExportError(
                f"转换超时（>{settings.convert_timeout_seconds}s）: {filename or source_format}",
                code="convert_timeout",
            ) from exc

        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status = {}
            if status.get("ok") and out_path.is_file():
                return out_path.read_bytes()
            msg = status.get("message") or "转换失败"
            code = status.get("code") or "conversion_error"
            raise MdBridgeError(str(msg), code=str(code))

        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        hint = stderr[-400:] if stderr else f"exit={proc.returncode}"
        _log.error(
            "convert worker crashed (rc=%s) %s: %s — falling back in-process",
            proc.returncode,
            filename,
            hint,
        )
        # Native crashes (SIGTRAP/OOM from Paddle/torch) kill the worker but
        # should not leave the API job with a generic failure when in-process
        # conversion can still succeed (or raise a typed error).
        try:
            return convert(
                source_bytes,
                source_format,
                target_format,
                filename=filename,
                client=client,
                skip_magic=skip_magic,
                fetch_images=fetch_images,
                **pdf_opts,
            )
        except MdBridgeError:
            raise
        except Exception as exc:
            raise ExportError(
                f"转换进程异常退出（rc={proc.returncode}），进程内回退亦失败: "
                f"{exc}；详情: {hint}",
                code="worker_crash",
                status_code=500,
            ) from exc
