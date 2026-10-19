"""Best-effort Apple iWork (.pages / .numbers / .key) text extract (§6.4)."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from typing import NoReturn

from aduport.core.document_model import DocumentModel, Heading, Paragraph
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError, UnsupportedFormatError
from aduport.core.safe_zip import ZipInflateBudget, assert_zip_package_budget
from aduport.importers.import_guard import import_failure_budget, swallow_import_error

_IWORK_MIMES = {
    "pages": "application/vnd.apple.pages",
    "numbers": "application/vnd.apple.numbers",
    "keynote": "application/vnd.apple.keynote",
}

_TEXT_RUN_RE = re.compile(rb"[\x20-\x7e\xc2-\xf4][\x80-\xbf]*|[A-Za-z0-9 ._:\-]{4,}")


def _extract_strings(data: bytes, *, limit: int = 400) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for m in _TEXT_RUN_RE.finditer(data):
        try:
            s = m.group(0).decode("utf-8", "ignore").strip()
        except UnicodeDecodeError:
            continue
        if len(s) < 4 or s in seen:
            continue
        if s.startswith("http://") or s.startswith("https://"):
            continue
        seen.add(s)
        found.append(s)
        if len(found) >= limit:
            break
    return found


def _try_preview_pdf(data: bytes) -> DocumentModel | None:
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            # Budget BEFORE any member inflate (report P1-2).
            assert_zip_package_budget(zf)
            budget = ZipInflateBudget()
            for name in ("QuickLook/Preview.pdf", "preview.pdf", "Preview.pdf"):
                if name in zf.namelist():
                    pdf = budget.read(zf, name)
                    from aduport.importers.pdf_text_fallback import try_extract_pdf_model

                    return try_extract_pdf_model(pdf, title="iWork Preview")
    except Exception as exc:
        swallow_import_error(exc, where="iwork:preview_pdf")
    return None


def import_iwork(source_bytes: bytes, filename: str = "", fmt: str = "pages") -> DocumentModel:
    fmt = (fmt or "pages").lower().lstrip(".")
    title = filename.rsplit("/", 1)[-1] if filename else fmt
    with import_failure_budget(limit=24):
        preview = _try_preview_pdf(source_bytes)
        if preview is not None and preview.nodes:
            preview.metadata.update({"importer": "iwork", "engine": "preview-pdf", "format": fmt})
            return preview

        try:
            zf = zipfile.ZipFile(BytesIO(source_bytes))
            assert_zip_package_budget(zf)
        except zipfile.BadZipFile as exc:
            raise CorruptedFileError(f"无效的 iWork 包: {title}") from exc

        budget = ZipInflateBudget()
        chunks: list[str] = []
        try:
            for name in sorted(zf.namelist()):
                low = name.lower()
                if not (low.endswith(".iwa") or low.endswith(".xml") or low.endswith(".plist")):
                    continue
                if name.startswith("Metadata/") or name.startswith("Index/"):
                    try:
                        raw = budget.read(zf, name)
                    except Exception as exc:
                        swallow_import_error(exc, where=f"iwork:{name}")
                        continue
                    chunks.extend(_extract_strings(raw, limit=80))
        finally:
            zf.close()

        if not chunks:
            raise InvalidDocumentError(
                f"无法从 iWork 文档提取文本: {title}",
                code="iwork_empty",
            )

        nodes: list = [Heading(level=1, text=title or fmt.upper())]
        for line in chunks[:300]:
            nodes.append(Paragraph(text=line))
        return DocumentModel(
            title=title,
            nodes=nodes,
            metadata={
                "importer": "iwork",
                "engine": "zip-string-scan",
                "format": fmt,
                "mime": _IWORK_MIMES.get(fmt, ""),
            },
        )


def refuse_iwork(fmt: str) -> NoReturn:
    """Public iWork entry points stay registered and always refuse.

    The scan below is kept so a future Snappy/IWA reader can replace this
    refusal. Do not point these functions back at ``import_iwork``: the gate
    samples are uncompressed plaintext, and a regex tweak would pass them
    while real iWork '13+ files still fail.
    """
    label = {"pages": "Pages", "numbers": "Numbers", "keynote": "Keynote"}.get(
        (fmt or "").lower(), fmt or "iWork"
    )
    raise UnsupportedFormatError(
        f"{label} 已摘牌：真实 iWork '13+ 正文在 Snappy 压缩的 IWA 中，当前不能可靠提取"
    )


def import_pages(source_bytes: bytes, filename: str = "") -> DocumentModel:
    del source_bytes, filename
    refuse_iwork("pages")


def import_numbers(source_bytes: bytes, filename: str = "") -> DocumentModel:
    del source_bytes, filename
    refuse_iwork("numbers")


def import_keynote(source_bytes: bytes, filename: str = "") -> DocumentModel:
    del source_bytes, filename
    refuse_iwork("keynote")
