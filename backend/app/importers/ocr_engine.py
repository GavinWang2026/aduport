"""Pluggable OCR engines for scanned PDF → Markdown → DocumentModel.

Engines are optional extras (not required for digital PDF / docling path):

- mineru: balanced default for scan → structure (CLI or ``mineru`` package)
- paddleocr_vl: high-accuracy / fallback for hard scans
- docling: existing office stack (tesseract-backed when OCR on)

Routing (when ``MDBRIDGE_OCR_ENABLED=true`` and text layer is weak):

- ocr_mode=balanced → MinerU → fallback (default paddleocr_vl) → docling
- ocr_mode=accurate → PaddleOCR-VL → MinerU → docling
- ocr_engine=mineru|paddleocr_vl|docling → force that engine
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Callable

from app.config import settings
from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError

_log = logging.getLogger("mdbridge.ocr")
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-cvt")


class OcrEngine(ABC):
    name: str

    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        ...

    def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
        md = self.pdf_to_markdown(source_bytes, filename).strip()
        if len(md) < 8:
            raise CorruptedFileError(f"OCR 引擎 {self.name} 未产出有效文本: {filename}")
        from app.importers.md_importer import import_md

        return import_md(md.encode("utf-8"))


def _run_with_timeout(fn: Callable[[], str], timeout: float, label: str) -> str:
    fut = _pool.submit(fn)
    try:
        return fut.result(timeout=timeout)
    except FuturesTimeout as exc:
        fut.cancel()
        raise CorruptedFileError(f"OCR 超时（>{timeout:.0f}s）: {label}") from exc


class MinerUEngine(OcrEngine):
    name = "mineru"

    def available(self) -> bool:
        if shutil.which((settings.mineru_bin or "mineru").strip() or "mineru"):
            return True
        try:
            import importlib.util

            return importlib.util.find_spec("mineru") is not None
        except Exception:
            return False

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        if not self.available():
            raise InvalidDocumentError(
                "未安装 MinerU：pip install 'md-bridge[ocr-mineru]' 或安装 mineru CLI"
            )
        timeout = max(30.0, float(settings.ocr_timeout_seconds))
        return _run_with_timeout(
            lambda: self._convert(source_bytes, filename),
            timeout,
            f"mineru:{filename}",
        )

    def _convert(self, source_bytes: bytes, filename: str) -> str:
        # Prefer CLI: ProcessPool spawn is unreliable under some entrypoints (e.g. -c).
        cli_err: Exception | None = None
        try:
            return self._via_cli(source_bytes, filename)
        except CorruptedFileError as exc:
            cli_err = exc
            _log.info("mineru CLI failed (%s), trying python API", exc)
        except Exception as exc:
            cli_err = exc
            _log.info("mineru CLI unavailable (%s), trying python API", exc)
        try:
            return self._via_python(source_bytes, filename)
        except Exception as py_exc:
            if cli_err is not None:
                raise CorruptedFileError(
                    f"mineru 失败: CLI={cli_err}; python={py_exc}"
                ) from py_exc
            raise

    def _via_python(self, source_bytes: bytes, filename: str) -> str:
        # Soft-probe common MinerU entrypoints across versions.
        with tempfile.TemporaryDirectory(prefix="mdbridge-mineru-") as tmp:
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
                return _read_first_markdown(out_dir)
            except Exception:
                pass

            raise RuntimeError("mineru python API not compatible")

    def _via_cli(self, source_bytes: bytes, filename: str) -> str:
        import subprocess

        bin_name = (settings.mineru_bin or "mineru").strip() or "mineru"
        with tempfile.TemporaryDirectory(prefix="mdbridge-mineru-cli-") as tmp:
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
            return _read_first_markdown(out_dir)


class PaddleOcrVlEngine(OcrEngine):
    name = "paddleocr_vl"
    _pipeline: object | None = None

    def available(self) -> bool:
        try:
            import importlib.util

            return (
                importlib.util.find_spec("paddleocr") is not None
                or importlib.util.find_spec("paddlex") is not None
            )
        except Exception:
            return False

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        if not self.available():
            raise InvalidDocumentError(
                "未安装 PaddleOCR-VL：pip install 'md-bridge[ocr-paddle]' "
                "（需 GPU 环境与 PaddlePaddle）"
            )
        timeout = max(30.0, float(settings.ocr_timeout_seconds))
        return _run_with_timeout(
            lambda: self._convert(source_bytes, filename),
            timeout,
            f"paddleocr_vl:{filename}",
        )

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
        with tempfile.TemporaryDirectory(prefix="mdbridge-paddle-") as tmp:
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
                return md
            except CorruptedFileError:
                raise
            except Exception as exc:
                raise CorruptedFileError(
                    f"PaddleOCR-VL 调用失败: {filename} — {exc}"
                ) from exc


class DoclingOcrEngine(OcrEngine):
    """Thin wrapper: reuse existing docling PDF path (may use tesseract)."""

    name = "docling"

    def available(self) -> bool:
        try:
            from app.importers.docling_ready import docling_models_ready

            return docling_models_ready()
        except Exception:
            return False

    def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
        # Not used — import_pdf goes through docling DocumentModel directly.
        raise NotImplementedError

    def import_pdf(self, source_bytes: bytes, filename: str) -> DocumentModel:
        from app.importers.pdf_importer import import_pdf_via_docling

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
            except Exception:
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
        except Exception:
            pass
    return ""


def _apply_ocr_home() -> None:
    """Point PaddleX / ModelScope caches at a writable project-local home when set."""
    import os

    home = (settings.ocr_home or "").strip()
    if home:
        Path(home).mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HOME", home)
    # Skip slow hoster probe when models are already cached / offline-ish
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


def _ocr_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    home = (settings.ocr_home or "").strip()
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
    return {
        "enabled": bool(settings.ocr_enabled),
        "engine": (settings.ocr_engine or "auto").lower(),
        "mode": (settings.ocr_mode or "balanced").lower(),
        "fallback": (settings.ocr_fallback or "paddleocr_vl").lower(),
        "available": {name: eng.available() for name, eng in _ENGINES.items()},
        "hint": (
            "扫描件：MDBRIDGE_OCR_ENABLED=true；"
            "balanced→MinerU，accurate→PaddleOCR-VL；"
            "可选依赖 md-bridge[ocr-mineru] / [ocr-paddle]"
        ),
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
        except Exception:
            pass
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
    except Exception:
        return 0


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

    available_any = any(_ENGINES[n].available() for n in chain if n in _ENGINES)
    if not available_any:
        raise InvalidDocumentError(
            "扫描 OCR 无可用引擎。请安装: pip install 'md-bridge[ocr-mineru]' "
            "和/或 'md-bridge[ocr-paddle]'，或预置 docling 模型；"
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
