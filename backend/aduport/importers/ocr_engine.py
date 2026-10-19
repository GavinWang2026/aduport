"""Pluggable OCR engines for scanned PDF → Markdown → DocumentModel.

Engines are optional extras (not required for digital PDF / docling path):

- mineru: balanced default for scan → structure (CLI or ``mineru`` package)
- paddleocr_vl: high-accuracy / fallback for hard scans
- docling: existing office stack (tesseract-backed when OCR on)

Routing (when ``ADUPORT_OCR_ENABLED=true`` and text layer is weak):

- ocr_mode=balanced → MinerU → fallback (default paddleocr_vl) → docling
- ocr_mode=accurate → PaddleOCR-VL → MinerU → docling
- ocr_engine=mineru|paddleocr_vl|docling → force that engine
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Callable

from aduport.config import settings
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError, OcrUnavailableError
from aduport.importers.import_guard import swallow_import_error

_log = logging.getLogger("aduport.ocr")
_pool_lock = threading.Lock()


def _ocr_workers() -> int:
    return max(2, int(getattr(settings, "convert_pool_workers", 2) or 2))


def _new_ocr_pool() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=_ocr_workers(), thread_name_prefix="ocr-cvt")


_pool = _new_ocr_pool()


def _resolve_mineru_bin() -> str:
    """Validate mineru binary path from settings (deployment-surface hardening)."""
    raw = (settings.mineru_bin or "mineru").strip() or "mineru"
    if any(c in raw for c in ";|&`$<>\n\r"):
        raise OcrUnavailableError(
            "ADUPORT_MINERU_BIN 含非法字符",
            install_hint="设置可执行文件名或绝对路径，例如 mineru 或 /usr/local/bin/mineru",
        )
    p = Path(raw)
    if p.is_absolute() or "/" in raw or "\\" in raw:
        if not p.is_file() or not os.access(p, os.X_OK):
            raise OcrUnavailableError(
                f"MinerU 二进制不存在或不可执行: {raw}",
                install_hint="pip install 'aduport[ocr-mineru]' 或修正 ADUPORT_MINERU_BIN",
            )
        return str(p.resolve())
    found = shutil.which(raw)
    if not found:
        raise OcrUnavailableError(
            f"未找到 MinerU 命令: {raw}",
            install_hint="pip install 'aduport[ocr-mineru]' 或将 mineru 加入 PATH",
        )
    return found


def _recycle_ocr_pool() -> None:
    """Drop a wedged worker from the queue. Python threads cannot be killed;
    the old pool is shut down without waiting so later jobs are not serialized
    behind a single timed-out OCR call. MinerU CLI already uses subprocess
    timeout, which does kill the child.
    """
    global _pool
    with _pool_lock:
        old = _pool
        _pool = _new_ocr_pool()
    old.shutdown(wait=False, cancel_futures=True)


class OcrEngine(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        ...

    def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
        md, layout = self.pdf_to_markdown_with_layout(source_bytes, filename)
        md = (md or "").strip()
        if len(md) < 8:
            raise CorruptedFileError(f"OCR 引擎 {self.name} 未产出有效文本: {filename}")
        from aduport.importers.md_importer import import_md
        from aduport.importers.ocr_layout import apply_ocr_layout_to_model

        model = import_md(md.encode("utf-8"))
        if layout:
            apply_ocr_layout_to_model(model, engine=self.name, blocks=layout)
        model.metadata.setdefault("importer", f"ocr:{self.name}")
        return model

    def pdf_to_markdown_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        """Return Markdown plus optional normalized layout blocks."""
        return self.pdf_to_markdown(source_bytes, filename), []


def _run_with_timeout(fn: Callable[[], str], timeout: float, label: str) -> str:
    """Run OCR work with a wall-clock timeout.

    Timed-out workers cannot be force-killed (Python threads), so the pool is
    recycled to stop starving later jobs. MinerU CLI already uses
    ``subprocess.run(..., timeout=)`` which does kill the child. Full
    ``ADUPORT_CONVERT_ISOLATE`` also wraps the convert (incl. OCR) in a
    killable process.
    """
    fut = _pool.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout as exc:
        _recycle_ocr_pool()
        raise CorruptedFileError(f"OCR 超时（>{timeout:.0f}s）: {label}") from exc


# A distribution name on disk is not a working engine. The probe below runs
# outside the request thread and is cached so doctor / health do not repeat it.
_PROBE_TIMEOUT_SECONDS = 6.0
_probe_cache: dict[str, bool] = {}
_probe_cache_lock = threading.Lock()

_MINERU_PROBE = r"""
import shutil, subprocess, sys
name = sys.argv[1] if len(sys.argv) > 1 else "mineru"
path = shutil.which(name)
if path:
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, timeout=3)
        if proc.returncode == 0 and (proc.stdout or proc.stderr):
            raise SystemExit(0)
    except Exception:
        pass
import mineru.cli.common as common
if not callable(getattr(common, "do_parse", None)):
    raise SystemExit(1)
import mineru.backend.pipeline.pipeline_analyze  # noqa: F401
raise SystemExit(0)
"""

_PADDLE_PROBE = r"""
ok = False
try:
    import paddleocr
    ok = callable(getattr(paddleocr, "PaddleOCRVL", None))
except Exception:
    ok = False
if not ok:
    import paddlex
    ok = callable(getattr(paddlex, "create_pipeline", None))
raise SystemExit(0 if ok else 1)
"""


def clear_ocr_probe_cache() -> None:
    with _probe_cache_lock:
        _probe_cache.clear()


def _run_probe(script: str, args: list[str] | None = None) -> bool:
    """Run a short-lived interpreter probe. Timeout or a non-zero exit is not ready."""
    import subprocess
    import sys

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script, *(args or [])],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        _log.info("ocr probe failed: %s", exc)
        return False
    if proc.returncode != 0:
        err = (proc.stderr or b"")[-300:].decode("utf-8", "replace").strip()
        _log.info("ocr probe exit %s: %s", proc.returncode, err)
        return False
    return True


def _cached_probe(key: str, script: str, args: list[str] | None = None) -> bool:
    with _probe_cache_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    ok = _run_probe(script, args)
    with _probe_cache_lock:
        _probe_cache[key] = ok
    return ok


def _module_spec_exists(name: str) -> bool:
    """Negative short-circuit only. A present spec is not treated as usable."""
    try:
        import importlib.util

        return importlib.util.find_spec(name) is not None
    except Exception as exc:
        return swallow_import_error(exc, where="ocr_engine.py:spec", default=False)


class MinerUEngine(OcrEngine):
    name = "mineru"

    def available(self) -> bool:
        bin_name = (settings.mineru_bin or "mineru").strip() or "mineru"
        has_cli = shutil.which(bin_name) is not None
        has_pkg = _module_spec_exists("mineru")
        if not has_cli and not has_pkg:
            return False
        return _cached_probe("mineru:" + bin_name, _MINERU_PROBE, [bin_name])

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self.pdf_to_markdown_with_layout(source_bytes, filename)
        return md

    def pdf_to_markdown_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        if not self.available():
            raise InvalidDocumentError(
                "未安装 MinerU：pip install 'aduport[ocr-mineru]' 或安装 mineru CLI"
            )
        timeout = max(30.0, float(settings.ocr_timeout_seconds))

        def _run() -> tuple[str, list[dict]]:
            return self._convert_with_layout(source_bytes, filename)

        fut = _pool.submit(_run)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout as exc:
            _recycle_ocr_pool()
            raise CorruptedFileError(f"OCR 超时（>{timeout:.0f}s）: mineru:{filename}") from exc

    def _convert(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self._convert_with_layout(source_bytes, filename)
        return md

    def _convert_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        # Prefer CLI: ProcessPool spawn is unreliable under some entrypoints (e.g. -c).
        cli_err: Exception | None = None
        try:
            return self._via_cli_with_layout(source_bytes, filename)
        except CorruptedFileError as exc:
            cli_err = exc
            _log.info("mineru CLI failed (%s), trying python API", exc)
        except Exception as exc:
            cli_err = exc
            _log.info("mineru CLI unavailable (%s), trying python API", exc)
        try:
            return self._via_python_with_layout(source_bytes, filename)
        except Exception as py_exc:
            if cli_err is not None:
                raise CorruptedFileError(
                    f"mineru 失败: CLI={cli_err}; python={py_exc}"
                ) from py_exc
            raise

    def _via_python(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self._via_python_with_layout(source_bytes, filename)
        return md

    def _via_python_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        from aduport.importers.ocr_layout import collect_mineru_layout_blocks

        # Soft-probe common MinerU entrypoints across versions.
        with tempfile.TemporaryDirectory(prefix="aduport-mineru-") as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / (Path(filename).name or "input.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            pdf_path.write_bytes(source_bytes)

            # Newer orchestration (best-effort)
            try:
                from mineru.cli.common import do_parse  # type: ignore

                do_parse(
                    output_dir=str(out_dir),
                    pdf_file_names=[pdf_path.stem],
                    pdf_bytes_list=[source_bytes],
                    p_lang_list=["ch"],
                    backend=settings.mineru_backend or "pipeline",
                    parse_method="ocr",
                    formula_enable=bool(settings.mineru_formula),
                    table_enable=bool(settings.mineru_table),
                )
                md = _read_first_markdown(out_dir)
                return md, collect_mineru_layout_blocks(out_dir)
            except Exception as exc:
                swallow_import_error(exc, where='ocr_engine.py:133')

            raise RuntimeError("mineru python API not compatible")

    def _via_cli(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self._via_cli_with_layout(source_bytes, filename)
        return md

    def _via_cli_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        import subprocess

        from aduport.importers.ocr_layout import collect_mineru_layout_blocks

        bin_name = _resolve_mineru_bin()
        with tempfile.TemporaryDirectory(prefix="aduport-mineru-cli-") as tmp:
            tmp_path = Path(tmp)
            pdf_path = tmp_path / (Path(filename).name or "input.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            pdf_path.write_bytes(source_bytes)
            backend = (settings.mineru_backend or "pipeline").strip() or "pipeline"
            cmd = [
                bin_name,
                "-p",
                str(pdf_path),
                "-o",
                str(out_dir),
                "-b",
                backend,
                "-f",
                "true" if settings.mineru_formula else "false",
                "-t",
                "true" if settings.mineru_table else "false",
            ]
            env = _ocr_env()
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(30.0, float(settings.ocr_timeout_seconds)),
                check=False,
                env=env,
            )
            if proc.returncode != 0:
                raise CorruptedFileError(
                    f"mineru CLI 失败 (code={proc.returncode}): "
                    f"{(proc.stderr or proc.stdout or '')[:400]}"
                )
            return _read_first_markdown(out_dir), collect_mineru_layout_blocks(out_dir)


class PaddleOcrVlEngine(OcrEngine):
    name = "paddleocr_vl"
    _pipeline: object | None = None

    def available(self) -> bool:
        if not _paddle_allowed():
            return False
        if not (_module_spec_exists("paddleocr") or _module_spec_exists("paddlex")):
            return False
        return _cached_probe("paddleocr_vl", _PADDLE_PROBE)

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self.pdf_to_markdown_with_layout(source_bytes, filename)
        return md

    def pdf_to_markdown_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        if not self.available():
            raise InvalidDocumentError(
                "未安装 PaddleOCR-VL：pip install 'aduport[ocr-paddle]' "
                "（需 GPU 环境与 PaddlePaddle）"
            )
        timeout = max(30.0, float(settings.ocr_timeout_seconds))

        def _run() -> tuple[str, list[dict]]:
            return self._convert_with_layout(source_bytes, filename)

        fut = _pool.submit(_run)
        try:
            return fut.result(timeout=timeout)
        except FuturesTimeout as exc:
            _recycle_ocr_pool()
            raise CorruptedFileError(
                f"OCR 超时（>{timeout:.0f}s）: paddleocr_vl:{filename}"
            ) from exc

    def _get_pipeline(self) -> object:
        if PaddleOcrVlEngine._pipeline is not None:
            return PaddleOcrVlEngine._pipeline
        _apply_ocr_home()
        # Path A: PaddleOCR VL class (PaddleOCR 3.x+)
        try:
            from paddleocr import PaddleOCRVL  # type: ignore

            PaddleOcrVlEngine._pipeline = PaddleOCRVL()
            return PaddleOcrVlEngine._pipeline
        except Exception as exc:
            _log.debug("PaddleOCRVL init failed: %s", exc)
        # Path B: PaddleX document parsing pipeline (only if A never initialized PDX)
        try:
            from paddlex import create_pipeline  # type: ignore

            PaddleOcrVlEngine._pipeline = create_pipeline(pipeline="PaddleOCR-VL")
            return PaddleOcrVlEngine._pipeline
        except Exception as exc:
            raise CorruptedFileError(f"PaddleOCR-VL 初始化失败: {exc}") from exc

    def _convert(self, source_bytes: bytes, filename: str) -> str:
        md, _ = self._convert_with_layout(source_bytes, filename)
        return md

    def _convert_with_layout(
        self, source_bytes: bytes, filename: str
    ) -> tuple[str, list[dict]]:
        from aduport.importers.ocr_layout import extract_paddle_layout_blocks

        with tempfile.TemporaryDirectory(prefix="aduport-paddle-") as tmp:
            pdf_path = Path(tmp) / (Path(filename).name or "input.pdf")
            pdf_path.write_bytes(source_bytes)
            try:
                pipeline = self._get_pipeline()
                result = pipeline.predict(str(pdf_path))  # type: ignore[attr-defined]
                md = _paddle_result_to_markdown(result)
                if not md.strip():
                    raise CorruptedFileError(
                        f"PaddleOCR-VL 未产出有效 Markdown: {filename}"
                    )
                return md, extract_paddle_layout_blocks(result)
            except CorruptedFileError:
                raise
            except Exception as exc:
                raise CorruptedFileError(
                    f"PaddleOCR-VL 调用失败: {filename} — {exc}"
                ) from exc


def _paddle_allowed() -> bool:
    """Honour ADUPORT_OCR_ALLOW_PADDLE (auto=off on macOS)."""
    import sys

    flag = (settings.ocr_allow_paddle or "auto").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    # auto
    return sys.platform != "darwin"


class DoclingOcrEngine(OcrEngine):
    """Thin wrapper: reuse existing docling PDF path (may use tesseract)."""

    name = "docling"

    def available(self) -> bool:
        try:
            from aduport.importers.docling_ready import docling_models_ready

            return docling_models_ready()
        except Exception as exc:
            return swallow_import_error(exc, where='ocr_engine.py:259', default=False)

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        # Not used — import_pdf goes through docling DocumentModel directly.
        raise NotImplementedError

    def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
        from aduport.importers.pdf_importer import import_pdf_via_docling

        return import_pdf_via_docling(source_bytes, filename)


def _read_first_markdown(root: Path) -> str:
    mds = sorted(root.rglob("*.md"))
    if not mds:
        raise CorruptedFileError(f"OCR 输出目录无 Markdown: {root}")
    # Prefer non-empty largest file (often full doc)
    best = max(mds, key=lambda p: p.stat().st_size)
    text = best.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise CorruptedFileError(f"OCR Markdown 为空: {best}")
    return text


_PADDLE_MD_KEYS = ("markdown_texts", "markdown", "md", "text", "res", "content")


def _paddle_result_to_markdown(result: object) -> str:
    """Best-effort flatten of heterogeneous PaddleOCR-VL return types."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    chunks: list[str] = []
    # Prefer rich result objects (dict subclasses with .markdown) before plain-dict keys
    if not isinstance(result, (list, tuple)) and type(result) is not dict:
        extracted = _paddle_item_to_markdown(result)
        if extracted.strip():
            return extracted.strip()
    if type(result) is dict:
        for key in _PADDLE_MD_KEYS:
            if key in result and result[key]:
                return _paddle_result_to_markdown(result[key])
        return ""
    if isinstance(result, (list, tuple)):
        for item in result:
            extracted = _paddle_item_to_markdown(item)
            if extracted.strip():
                chunks.append(extracted.strip())
        return "\n\n".join(chunks)
    return ""


def _paddle_item_to_markdown(item: object) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    # Attrs first — PaddleOCRVLResult is a dict subclass; .markdown holds the text
    for attr in _PADDLE_MD_KEYS:
        if hasattr(item, attr):
            try:
                val = getattr(item, attr)
            except Exception as exc:
                swallow_import_error(exc, where='ocr_engine.py:323')
                continue
            if callable(val):
                continue
            if val:
                # Avoid re-entering item path on same object
                if val is item:
                    continue
                if type(val) is dict or isinstance(val, str):
                    return _paddle_result_to_markdown(val)
                if isinstance(val, (list, tuple)):
                    return _paddle_result_to_markdown(val)
                nested = _paddle_item_to_markdown(val)
                if nested.strip():
                    return nested
    if type(item) is dict:
        return _paddle_result_to_markdown(item)
    if hasattr(item, "get"):
        try:
            for key in _PADDLE_MD_KEYS:
                val = item.get(key)  # type: ignore[attr-defined]
                if val and val is not item:
                    out = _paddle_result_to_markdown(val)
                    if out.strip():
                        return out
        except Exception as exc:
            swallow_import_error(exc, where='ocr_engine.py:348')
    return ""


_ocr_env_lock = threading.Lock()
_ocr_env_applied = False


def configured_ocr_home() -> str:
    """Real directory string only. MagicMock settings must not become a path."""
    raw = getattr(settings, "ocr_home", "")
    if not isinstance(raw, str):
        return ""
    home = raw.strip()
    if not home or "MagicMock" in home:
        return ""
    return home


def _apply_ocr_home() -> None:
    """Point PaddleX / ModelScope caches at a writable home once per process."""
    global _ocr_env_applied
    import os

    with _ocr_env_lock:
        if _ocr_env_applied:
            return
        home = configured_ocr_home()
        if home:
            Path(home).mkdir(parents=True, exist_ok=True)
            os.environ.setdefault("HOME", home)
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        _ocr_env_applied = True


def _ocr_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    home = configured_ocr_home()
    if home:
        Path(home).mkdir(parents=True, exist_ok=True)
        env["HOME"] = home
        env.setdefault("XDG_CACHE_HOME", str(Path(home) / "cache"))
    return env


_ENGINES: dict[str, OcrEngine] = {
    "mineru": MinerUEngine(),
    "paddleocr_vl": PaddleOcrVlEngine(),
    "docling": DoclingOcrEngine(),
}


def list_ocr_engines() -> dict[str, object]:
    available = {name: eng.available() for name, eng in _ENGINES.items()}
    any_ready = any(available.values())
    enabled = bool(settings.ocr_enabled)
    ready = enabled and any_ready
    if not enabled:
        install_hint = (
            "OCR 未开启：设置 ADUPORT_OCR_ENABLED=true，并安装 "
            "pip install 'aduport[ocr-mineru]' 或 'aduport[ocr-paddle]'"
        )
    elif not any_ready:
        install_hint = (
            "OCR 已开启但无可用引擎：安装可选依赖 "
            "pip install 'aduport[ocr-mineru]'（balanced）或 "
            "'aduport[ocr-paddle]'（accurate）；Docker 使用 full/ocr 镜像"
        )
    else:
        install_hint = "OCR 就绪；扫描 PDF 将按 ocr_mode/ocr_engine 自动选用引擎"
    return {
        "enabled": enabled,
        "ready": ready,
        "feature": "scanned_pdf_to_structured_markdown",
        "engine": (settings.ocr_engine or "auto").lower(),
        "mode": (settings.ocr_mode or "balanced").lower(),
        "fallback": (settings.ocr_fallback or "paddleocr_vl").lower(),
        "available": available,
        "hint": (
            "扫描件：ADUPORT_OCR_ENABLED=true；"
            "balanced→MinerU，accurate→PaddleOCR-VL；"
            "可选依赖 aduport[ocr-mineru] / [ocr-paddle]"
        ),
        "install_hint": install_hint,
    }


def resolve_ocr_chain(*, force_engine: str | None = None) -> list[str]:
    """Ordered engine names to try for the current settings."""
    forced = (force_engine or settings.ocr_engine or "auto").lower().strip()
    mode = (settings.ocr_mode or "balanced").lower().strip()
    fallback = (settings.ocr_fallback or "paddleocr_vl").lower().strip()

    if forced in _ENGINES:
        chain = [forced]
        if fallback in _ENGINES and fallback != forced:
            chain.append(fallback)
        if "docling" not in chain:
            chain.append("docling")
        return chain

    if mode == "accurate":
        primary = ["paddleocr_vl", "mineru", "docling"]
    else:
        primary = ["mineru", "paddleocr_vl", "docling"]

    # Honour explicit fallback by ensuring it is second if different
    if fallback in _ENGINES and fallback != primary[0]:
        rest = [e for e in primary if e != fallback]
        primary = [primary[0], fallback] + [e for e in rest if e != primary[0]]
        # dedupe preserve order
        seen: set[str] = set()
        out: list[str] = []
        for e in primary:
            if e not in seen:
                seen.add(e)
                out.append(e)
        return out
    return primary


def _node_text_len(node: object) -> int:
    plain = getattr(node, "plain_text", None)
    if callable(plain):
        try:
            return len(plain() or "")
        except Exception as exc:
            swallow_import_error(exc, where='ocr_engine.py:458')
    raw = getattr(node, "text", None)
    if raw is None:
        return 0
    if isinstance(raw, str):
        return len(raw)
    inner = getattr(raw, "text", None)
    if isinstance(inner, str):
        return len(inner)
    try:
        return len(str(raw))
    except Exception as exc:
        return swallow_import_error(exc, where='ocr_engine.py:470', default=0)


def _maybe_truncate_pdf_pages(source_bytes: bytes) -> tuple[bytes, int]:
    """Cap pages fed to OCR engines (``ocr_max_pages``; 0 = unlimited).

    Also soft-caps by ``memory_budget_mb`` estimate (≈6 MiB/page raster).
    Returns ``(bytes, truncated_to_n)`` where truncated_to_n is 0 if unchanged.
    """
    cap = int(getattr(settings, "ocr_max_pages", 0) or 0)
    try:
        from aduport.core.convert_context import get_import_opts

        override = get_import_opts().get("ocr_max_pages")
        if override is not None:
            cap = int(override)
    except Exception:
        pass
    try:
        from io import BytesIO

        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(BytesIO(source_bytes))
        n = len(reader.pages)
        # Rough raster memory ceiling: letter @150dpi RGB ≈ 6 MiB/page (v14 P3-7).
        budget = int(getattr(settings, "memory_budget_mb", 0) or 0) * 1024 * 1024
        if budget > 0:
            est = n * 6 * 1024 * 1024
            if est > budget:
                soft_cap = max(1, budget // (6 * 1024 * 1024))
                if soft_cap < n:
                    _log.warning(
                        "OCR memory estimate %s MiB > budget; soft-cap pages %s → %s",
                        est // (1024 * 1024),
                        n,
                        soft_cap,
                    )
                    cap = soft_cap if cap <= 0 else min(cap, soft_cap)
        if cap <= 0 or n <= cap:
            return source_bytes, 0
        writer = PdfWriter()
        for i in range(cap):
            writer.add_page(reader.pages[i])
        buf = BytesIO()
        writer.write(buf)
        _log.info("OCR truncated PDF pages %s → %s", n, cap)
        return buf.getvalue(), cap
    except Exception as exc:
        swallow_import_error(exc, where="ocr_engine.py:truncate_pages")
        return source_bytes, 0


def import_pdf_with_ocr(
    source_bytes: bytes,
    filename: str,
    *,
    force_engine: str | None = None,
) -> DocumentModel:
    """Try OCR engine chain; raise last error if all fail."""
    chain = resolve_ocr_chain(force_engine=force_engine)
    min_chars = max(0, int(settings.ocr_min_chars))
    errors: list[str] = []
    source_bytes, truncated = _maybe_truncate_pdf_pages(source_bytes)

    available_any = any(_ENGINES[n].available() for n in chain if n in _ENGINES)
    if not available_any:
        raise OcrUnavailableError(
            "扫描 OCR 无可用引擎（code=ocr_unavailable）。请安装: "
            "pip install 'aduport[ocr-mineru]' 和/或 'aduport[ocr-paddle]'，"
            "或预置 docling 模型后重试；"
            f"当前链={chain}，探测={list_ocr_engines()['available']}"
        )

    for name in chain:
        eng = _ENGINES.get(name)
        if eng is None:
            continue
        if not eng.available():
            errors.append(f"{name}:unavailable")
            _log.info("OCR skip %s (unavailable) for %s", name, filename)
            continue
        try:
            _log.info("OCR try %s for %s", name, filename)
            model = eng.import_pdf(source_bytes, filename)
            if truncated:
                model.metadata["ocr_pages_truncated"] = truncated
            approx = sum(_node_text_len(n) for n in model.nodes)
            if min_chars and approx < min_chars and name != chain[-1]:
                errors.append(f"{name}:too_short:{approx}")
                _log.warning(
                    "OCR %s output short (%s chars < %s), trying next",
                    name,
                    approx,
                    min_chars,
                )
                continue
            _log.info("OCR ok %s for %s (~%s chars)", name, filename, approx)
            return model
        except InvalidDocumentError as exc:
            errors.append(f"{name}:unavailable:{exc}")
            _log.warning("OCR engine %s unavailable: %s", name, exc)
        except CorruptedFileError as exc:
            errors.append(f"{name}:failed:{exc}")
            _log.warning("OCR engine %s failed: %s", name, exc)
        except Exception as exc:
            errors.append(f"{name}:error:{type(exc).__name__}:{exc}")
            _log.exception("OCR engine %s unexpected error", name)

    raise CorruptedFileError(
        f"扫描件 OCR 全部失败: {filename}；尝试={chain}；详情={'; '.join(errors)[:800]}"
    )
