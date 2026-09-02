"""PDF importer: docling (optional) with local text-layer fallback."""

from __future__ import annotations

import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from pathlib import Path

from app.config import settings
from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError, OcrUnavailableError
from app.importers._docling_utils import docling_document_to_model
from app.importers.docling_ready import docling_models_ready
from app.importers.pdf_text_fallback import extract_pdf_text_model, pdf_text_char_count

_log = logging.getLogger("mdbridge.pdf")
_docling_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="docling-cvt")


def _require_docling():
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
    except ImportError as exc:
        raise InvalidDocumentError(
            "PDF 导入需要安装 docling：pip install 'md-bridge[office]'"
        ) from exc


def _is_hub_network_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = (
        "connecttimeout",
        "connection timed out",
        "timed out",
        "huggingface",
        "files on the hub",
        "hf_hub",
        "localentrynotfound",
        "offline",
        "name or service not known",
        "nodename nor servname",
        "max retries exceeded",
    )
    return any(n in msg for n in needles)


def _apply_hf_endpoint() -> None:
    endpoint = (settings.hf_endpoint or "").strip()
    if endpoint and not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = endpoint.rstrip("/")


def _force_offline_hub() -> None:
    """Prevent runtime Hub downloads (P0-2 fast-fail)."""
    if settings.docling_offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


def _import_with_docling(source_bytes: bytes, filename: str) -> DocumentModel:
    _apply_hf_endpoint()
    _force_offline_hub()
    _require_docling()
    from app.importers._docling_converter import get_pdf_converter

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="mdbridge-pdf-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        converter = get_pdf_converter()
        timeout = max(5.0, float(settings.docling_timeout_seconds))

        def _run():
            return converter.convert(str(tmp_path))

        fut = _docling_pool.submit(_run)
        try:
            result = fut.result(timeout=timeout)
        except FuturesTimeout as exc:
            fut.cancel()
            raise CorruptedFileError(
                f"PDF docling 转换超时（>{timeout:.0f}s）: {filename}"
            ) from exc
        return docling_document_to_model(result.document, title=Path(filename).stem)


def import_pdf_via_docling(source_bytes: bytes, filename: str) -> DocumentModel:
    """Public entry used by OCR router ``docling`` engine."""
    return _import_with_docling(source_bytes, filename)


def import_pdf(source_bytes: bytes, filename: str = "input.pdf") -> DocumentModel:
    if b"%PDF" not in source_bytes[:1024]:
        raise InvalidDocumentError(f"文件不是有效的 PDF: {filename}")

    engine = (settings.pdf_engine or "auto").lower().strip()

    if engine == "text":
        return extract_pdf_text_model(source_bytes, filename)

    # Explicit OCR engines (ignore text-layer short-circuit)
    if engine in ("mineru", "paddleocr_vl"):
        from app.importers.ocr_engine import import_pdf_with_ocr

        return import_pdf_with_ocr(source_bytes, filename, force_engine=engine)

    if engine == "auto":
        chars = pdf_text_char_count(source_bytes)
        if chars >= settings.pdf_text_min_chars:
            _log.info("PDF text-layer fallback (%s chars): %s", chars, filename)
            try:
                return extract_pdf_text_model(source_bytes, filename)
            except Exception as exc:
                _log.warning("text fallback failed, trying structured import: %s", exc)

        # Scanned / weak text: optional OCR chain (MinerU / PaddleOCR-VL / docling)
        if settings.ocr_enabled:
            try:
                from app.importers.ocr_engine import import_pdf_with_ocr

                return import_pdf_with_ocr(source_bytes, filename)
            except OcrUnavailableError:
                # No engine installed — fall through to text/docling with clear terminal error
                _log.warning("OCR unavailable for %s; trying text/docling fallback", filename)
            except Exception as exc:
                _log.warning("OCR chain failed, falling back to docling/text: %s", exc)

    # Fast-fail: no local models → never block on Hub (P0-2)
    if not docling_models_ready():
        _log.warning(
            "docling models missing — skip Hub download, use text layer: %s",
            filename,
        )
        try:
            return extract_pdf_text_model(source_bytes, filename)
        except InvalidDocumentError as exc:
            if settings.ocr_enabled:
                raise OcrUnavailableError(
                    f"PDF 无可用文本层且扫描 OCR/docling 均不可用: {filename}。"
                    "请安装 pip install 'md-bridge[ocr-mineru]' 或 'md-bridge[ocr-paddle]'，"
                    "或运行 scripts/download_docling_models.sh；"
                    "纯文本 PDF 可设 MDBRIDGE_PDF_ENGINE=text。"
                ) from exc
            raise CorruptedFileError(
                f"PDF 解析失败：本地无 docling 模型且无可提取文本层: {filename}。"
                f"请运行 scripts/download_docling_models.sh 或设置 "
                f"MDBRIDGE_DOCLING_ARTIFACTS_PATH；纯文本 PDF 可设 MDBRIDGE_PDF_ENGINE=text。"
                f"扫描件请安装 MinerU/PaddleOCR-VL 并设 MDBRIDGE_OCR_ENABLED=true。"
            ) from exc

    # docling path (engine=docling or auto after weak/no text)
    try:
        return _import_with_docling(source_bytes, filename)
    except (InvalidDocumentError, CorruptedFileError):
        raise
    except Exception as exc:
        if _is_hub_network_error(exc):
            _log.warning("docling Hub/network error, falling back to text: %s", exc)
            try:
                return extract_pdf_text_model(source_bytes, filename)
            except InvalidDocumentError:
                raise CorruptedFileError(
                    f"PDF 解析失败：无法从 Hugging Face 下载 docling 模型（网络超时），"
                    f"且文件无可提取文本层（可能为扫描件）: {filename}。"
                    f"处理建议：1) 设置环境变量 HF_ENDPOINT=https://hf-mirror.com 后重启；"
                    f"2) 或运行 docling-tools models download 预下载模型；"
                    f"3) 纯文本 PDF 可设 MDBRIDGE_PDF_ENGINE=text；"
                    f"4) 扫描件启用 MDBRIDGE_OCR_ENABLED + MinerU/PaddleOCR-VL。"
                    f" 原始错误: {exc}"
                ) from exc
        raise CorruptedFileError(
            f"PDF 解析失败（文件可能已损坏）: {filename} — {exc}"
        ) from exc
