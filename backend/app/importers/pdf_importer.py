"""PDF importer: docling (optional) with local text-layer fallback."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from app.config import settings
from app.core.document_model import DocumentModel
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers._docling_utils import docling_document_to_model
from app.importers.pdf_text_fallback import extract_pdf_text_model, pdf_text_char_count

_log = logging.getLogger("mdbridge.pdf")


def _require_docling():
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise InvalidDocumentError(
            "PDF 导入需要安装 docling：pip install 'md-bridge[office]'"
        ) from exc
    return DocumentConverter


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


def _import_with_docling(source_bytes: bytes, filename: str) -> DocumentModel:
    _apply_hf_endpoint()
    _require_docling()
    from app.importers._docling_converter import get_pdf_converter

    suffix = Path(filename).suffix or ".pdf"
    with tempfile.TemporaryDirectory(prefix="mdbridge-pdf-") as tmpdir:
        tmp_path = Path(tmpdir) / f"input{suffix}"
        tmp_path.write_bytes(source_bytes)
        os.chmod(tmp_path, 0o600)
        converter = get_pdf_converter()
        result = converter.convert(str(tmp_path))
        return docling_document_to_model(result.document, title=Path(filename).stem)


def import_pdf(source_bytes: bytes, filename: str = "input.pdf") -> DocumentModel:
    if b"%PDF" not in source_bytes[:1024]:
        raise InvalidDocumentError(f"文件不是有效的 PDF: {filename}")

    engine = (settings.pdf_engine or "auto").lower().strip()

    if engine == "text":
        return extract_pdf_text_model(source_bytes, filename)

    if engine == "auto":
        chars = pdf_text_char_count(source_bytes)
        if chars >= settings.pdf_text_min_chars:
            _log.info("PDF text-layer fallback (%s chars): %s", chars, filename)
            try:
                return extract_pdf_text_model(source_bytes, filename)
            except Exception as exc:
                _log.warning("text fallback failed, trying docling: %s", exc)

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
                    f"3) 纯文本 PDF 可设 MDBRIDGE_PDF_ENGINE=text。"
                    f" 原始错误: {exc}"
                ) from exc
        raise CorruptedFileError(
            f"PDF 解析失败（文件可能已损坏）: {filename} — {exc}"
        ) from exc
