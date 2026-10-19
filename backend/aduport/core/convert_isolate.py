"""Run ``convert`` in child processes so native aborts do not kill the API.

v6/T2-#12 / D-04: default path is a **long-lived ProcessPool** so workers reuse
the interpreter, font cache and result cache. Per-request ``Popen`` remains as
``ADUPORT_CONVERT_ISOLATE_FORK=true`` (and as a fallback if the pool dies).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.exceptions import ExportError, AduPortError

_log = logging.getLogger("aduport.isolate")
_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_pool_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
_pool_generation = 0


def _skip_isolate_for_ocr_pdf(source_format: str, filename: str) -> bool:
    """Skip isolate only when Paddle is allowed on Darwin (known SIGTRAP → CrashReporter).

    With Paddle disabled (macOS default), keep isolation so long MinerU OCR cannot
    freeze the API event loop / GIL in the parent process.
    """
    if sys.platform != "darwin" or not settings.ocr_enabled:
        return False
    try:
        from aduport.importers.ocr_engine import paddle_ocr_allowed

        if not paddle_ocr_allowed():
            return False
    except Exception:
        return False
    fmt = (source_format or "").lower().strip()
    name = (filename or "").lower()
    return fmt in ("pdf", "auto") or name.endswith(".pdf")


def _pool_size() -> int:
    n = int(getattr(settings, "convert_pool_workers", 0) or 0)
    if n <= 0:
        n = max(4, min(16, os.cpu_count() or 4))
    return max(1, n)


def _convert_job(
    source_bytes: bytes | None,
    source_format: str,
    target_format: str,
    filename: str,
    client: str,
    skip_magic: bool,
    fetch_images: bool | None,
    pdf_opts: dict[str, Any],
    spill_path: str | None = None,
) -> dict[str, Any]:
    """Picklable worker entry (spawn context). Never raises into the parent.

    Large payloads may pass ``spill_path`` instead of pickling ``source_bytes``.
    Side effects (export warnings / metrics / audit) travel back in the result
    dict so the parent can apply them (report P1-7 / P2-11).
    """
    from aduport.core.isolate_effects import capture_worker_effects_finish, capture_worker_effects_start

    state = capture_worker_effects_start()
    try:
        if spill_path:
            payload = Path(spill_path).read_bytes()
        else:
            payload = source_bytes or b""
        out = convert(
            payload,
            source_format,
            target_format,
            filename=filename,
            client=client,
            skip_magic=skip_magic,
            fetch_images=fetch_images,
            **(pdf_opts or {}),
        )
        result: dict[str, Any] = {"ok": True, "data": out}
        result.update(capture_worker_effects_finish(state))
        return result
    except AduPortError as exc:
        result = {
            "ok": False,
            "code": getattr(exc, "code", None) or "conversion_error",
            "message": getattr(exc, "message", None) or str(exc),
        }
        result.update(capture_worker_effects_finish(state))
        return result
    except Exception as exc:  # noqa: BLE001
        result = {
            "ok": False,
            "code": "conversion_error",
            "message": str(exc) or "conversion failed",
        }
        result.update(capture_worker_effects_finish(state))
        return result
    finally:
        if spill_path:
            try:
                Path(spill_path).unlink(missing_ok=True)
            except OSError:
                pass


def _apply_isolate_result(result: dict[str, Any]) -> bytes:
    from aduport.core.isolate_effects import apply_worker_effects

    apply_worker_effects(result)
    if result.get("ok") and isinstance(result.get("data"), (bytes, bytearray)):
        return bytes(result["data"])
    code = str(result.get("code") or "conversion_error")
    msg = str(result.get("message") or "转换失败")
    raise AduPortError(msg, code=code)

def _reset_pool_locked() -> None:
    """Drop the pool and signal running workers so a timed-out convert ends."""
    global _pool, _pool_generation
    old = _pool
    _pool = None
    _pool_generation += 1
    if old is None:
        return
    procs = getattr(old, "_processes", None) or {}
    for proc in list(procs.values()):
        pid = getattr(proc, "pid", None)
        if not pid:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        old.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


def _ping() -> str:
    """Picklable no-op used to force-spawn every worker (spawn is lazy otherwise)."""
    return "ok"


def isolate_pool_status() -> dict[str, object]:
    """Whether the long-lived convert pool is configured and already started."""
    with _pool_lock:
        started = _pool is not None
    return {
        "enabled": bool(settings.convert_isolate),
        "workers": _pool_size(),
        "started": started,
    }


def _prime_pool(pool: ProcessPoolExecutor) -> None:
    """Submit one ping per worker so concurrent converts do not pay spawn tax."""
    n = _pool_size()
    futs = [pool.submit(_ping) for _ in range(n)]
    for fut in futs:
        try:
            fut.result(timeout=max(30, int(settings.convert_timeout_seconds)))
        except Exception as exc:
            # One slow worker must not leave the rest unprimed; the parallel
            # path then pays spawn cost and the speedup collapses.
            _log.warning("isolate pool prime failed: %s", exc)


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    created = False
    with _pool_lock:
        if _pool is not None:
            return _pool
        max_tasks = int(getattr(settings, "convert_isolate_max_tasks", 0) or 0)
        kwargs: dict[str, Any] = {
            "max_workers": _pool_size(),
            "mp_context": get_context("spawn"),
        }
        if max_tasks > 0:
            kwargs["max_tasks_per_child"] = max_tasks
        _pool = ProcessPoolExecutor(**kwargs)
        created = True
        pool = _pool
        _log.info(
            "isolate worker pool started workers=%s max_tasks=%s",
            kwargs["max_workers"],
            max_tasks or "∞",
        )
    if created:
        # Prime outside the lock so concurrent callers can already submit.
        _prime_pool(pool)
    return pool


def ensure_isolate_pool() -> ProcessPoolExecutor:
    """Public hook for warmup / tests: create + prime the long-lived pool."""
    return _get_pool()


def _client_is_trusted(client: str) -> bool:
    """True when ``client`` is listed in ``ADUPORT_CONVERT_TRUSTED_CLIENTS``."""
    if not getattr(settings, "convert_trusted_inprocess", True):
        return False
    raw = (getattr(settings, "convert_trusted_clients", "") or "").strip()
    if not raw or not (client or "").strip():
        return False
    allow = {c.strip() for c in raw.split(",") if c.strip()}
    return client.strip() in allow


def convert_isolated(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    filename: str = "",
    *,
    client: str = "",
    skip_magic: bool = False,
    fetch_images: bool | None = None,
    trusted: bool | None = None,
    **pdf_opts: Any,
) -> bytes:
    """Convert with optional subprocess isolation (``ADUPORT_CONVERT_ISOLATE``).

    Trusted path (v8 §C): when ``trusted=True`` or ``client`` is in
    ``ADUPORT_CONVERT_TRUSTED_CLIENTS``, run in-process so hot caches survive
    and cold-start fork tax is skipped. Untrusted / public uploads stay pooled.
    """
    use_trusted = trusted if trusted is not None else _client_is_trusted(client)
    if use_trusted and settings.convert_isolate:
        _log.info(
            "trusted in-process convert (skip isolate pool): client=%s file=%s",
            client or "-",
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

    if getattr(settings, "convert_isolate_fork", False):
        return _convert_via_popen(
            source_bytes,
            source_format,
            target_format,
            filename,
            client=client,
            skip_magic=skip_magic,
            fetch_images=fetch_images,
            **pdf_opts,
        )

    timeout_s = max(1, int(settings.convert_timeout_seconds))
    spill_path: str | None = None
    spill_fd: int | None = None
    try:
        threshold = max(0, int(getattr(settings, "spill_threshold_mb", 8) or 0)) * 1024 * 1024
        job_bytes: bytes | None = source_bytes
        if threshold > 0 and len(source_bytes) >= threshold:
            # Avoid pickling multi-MB payloads into every worker IPC copy.
            spill_fd, spill_path = tempfile.mkstemp(prefix="aduport-iso-in-", suffix=".bin")
            try:
                os.write(spill_fd, source_bytes)
                os.fchmod(spill_fd, 0o600)
            finally:
                os.close(spill_fd)
                spill_fd = None
            job_bytes = None
        fut = _get_pool().submit(
            _convert_job,
            job_bytes,
            source_format,
            target_format,
            filename,
            client,
            skip_magic,
            fetch_images,
            dict(pdf_opts),
            spill_path,
        )
        result = fut.result(timeout=timeout_s)
    except FutTimeout as exc:
        # P1-6: timed-out worker keeps running in the pool — recycle the pool so
        # subsequent requests are not stuck behind zombie conversions.
        # After recycle, worker can no longer finish reading spill — unlink here.
        _log.error(
            "convert pool timeout; recycling workers: %s",
            filename or source_format,
        )
        with _pool_lock:
            _reset_pool_locked()
        if spill_path:
            try:
                Path(spill_path).unlink(missing_ok=True)
            except OSError:
                pass
            spill_path = None
        raise ExportError(
            f"转换超时（>{settings.convert_timeout_seconds}s）: {filename or source_format}",
            code="convert_timeout",
        ) from exc
    except BrokenProcessPool as exc:
        _log.error("isolate pool broken; resetting and falling back to fork: %s", exc)
        with _pool_lock:
            _reset_pool_locked()
        if spill_path:
            try:
                Path(spill_path).unlink(missing_ok=True)
            except OSError:
                pass
            spill_path = None
        return _convert_via_popen(
            source_bytes,
            source_format,
            target_format,
            filename,
            client=client,
            skip_magic=skip_magic,
            fetch_images=fetch_images,
            **pdf_opts,
        )
    except Exception as exc:
        _log.warning("isolate pool submit failed, falling back to fork: %s", exc)
        if spill_path:
            try:
                Path(spill_path).unlink(missing_ok=True)
            except OSError:
                pass
            spill_path = None
        return _convert_via_popen(
            source_bytes,
            source_format,
            target_format,
            filename,
            client=client,
            skip_magic=skip_magic,
            fetch_images=fetch_images,
            **pdf_opts,
        )
    finally:
        # Success path: worker normally unlinks after read; parent cleans leftovers.
        if spill_path:
            try:
                Path(spill_path).unlink(missing_ok=True)
            except OSError:
                pass

    return _apply_isolate_result(result)


def _convert_via_popen(
    source_bytes: bytes,
    source_format: str,
    target_format: str,
    filename: str,
    *,
    client: str = "",
    skip_magic: bool = False,
    fetch_images: bool | None = None,
    **pdf_opts: Any,
) -> bytes:
    """Legacy per-request fork (D-04 expensive path; kept as escape hatch)."""
    with tempfile.TemporaryDirectory(prefix="aduport-iso-") as tmp:
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
        pp = env.get("PYTHONPATH", "")
        root = str(_BACKEND_ROOT)
        env["PYTHONPATH"] = f"{root}{os.pathsep}{pp}" if pp else root
        env.setdefault("ADUPORT_WARMUP_ON_STARTUP", "false")
        from aduport.importers.ocr_engine import configured_ocr_home

        ocr_home = configured_ocr_home() or (env.get("ADUPORT_OCR_HOME") or "").strip()
        if ocr_home and "MagicMock" not in ocr_home:
            Path(ocr_home).mkdir(parents=True, exist_ok=True)
            env["ADUPORT_OCR_HOME"] = ocr_home
            env["HOME"] = ocr_home
            env.setdefault("XDG_CACHE_HOME", str(Path(ocr_home) / "cache"))
        env.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

        cmd = [
            sys.executable,
            "-m",
            "aduport.core.convert_worker",
            str(req_path),
            str(out_path),
            str(status_path),
        ]
        timeout_s = max(1, int(settings.convert_timeout_seconds))
        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            try:
                stdout, stderr_b = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as exc:
                _log.error("convert worker timeout: %s", filename)
                _kill_process_group(proc)
                raise ExportError(
                    f"转换超时（>{settings.convert_timeout_seconds}s）: {filename or source_format}",
                    code="convert_timeout",
                ) from exc
        except ExportError:
            raise
        except Exception as exc:
            if proc is not None:
                _kill_process_group(proc)
            raise ExportError(
                f"无法启动转换进程: {exc}",
                code="worker_crash",
                status_code=500,
            ) from exc

        rc = int(proc.returncode or 0)
        if status_path.is_file():
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                status = {}
            from aduport.core.isolate_effects import apply_worker_effects

            apply_worker_effects(status)
            if status.get("ok") and out_path.is_file():
                return out_path.read_bytes()
            msg = status.get("message") or "转换失败"
            code = status.get("code") or "conversion_error"
            raise AduPortError(str(msg), code=str(code))

        stderr = (stderr_b or b"").decode("utf-8", errors="replace").strip()
        hint = stderr[-400:] if stderr else f"exit={rc}"
        if rc < 0:
            _log.error(
                "convert worker killed by signal (rc=%s) %s: %s — no in-process fallback",
                rc,
                filename,
                hint,
            )
            raise ExportError(
                f"转换进程被信号终止（rc={rc}）: {filename or source_format}",
                code="worker_crash",
                status_code=500,
            )

        _log.error(
            "convert worker crashed (rc=%s) %s: %s — falling back in-process",
            rc,
            filename,
            hint,
        )
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
        except AduPortError:
            raise
        except Exception as exc:
            _log.error(
                "convert worker in-process fallback failed (rc=%s) %s: %s",
                rc,
                filename,
                exc,
            )
            raise ExportError(
                "转换进程异常退出，进程内回退亦失败",
                code="worker_crash",
                status_code=500,
            ) from exc


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill the whole session (worker + grandchildren) on timeout / abort."""
    try:
        if proc.pid:
            try:
                os.killpg(proc.pid, 15)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            proc.wait(timeout=5)
        except Exception:
            try:
                if proc.pid:
                    os.killpg(proc.pid, 9)
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass
    except Exception:
        pass
