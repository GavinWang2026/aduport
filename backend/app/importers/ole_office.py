"""Legacy OLE Office formats (xls / ppt / pps / pot).

T36: structured BIFF8 grid + PPT atom text when CFB streams are readable;
falls back to UTF-16/ASCII scrape if structure parse yields nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.document_model import (
    DocumentModel,
    Heading,
    Paragraph,
    RichText,
    Table,
)
from app.core.exceptions import CorruptedFileError, InvalidDocumentError
from app.importers.ole_biff8 import parse_biff8_workbook
from app.importers.ole_cfb import CfbError, read_cfb_stream
from app.importers.ole_ppt import extract_ppt_texts


def _ole_extract_text(data: bytes) -> str:
    chunks: list[str] = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){6,}", data):
        try:
            chunks.append(m.group().decode("utf-16-le"))
        except UnicodeDecodeError:
            continue
    if not chunks:
        for m in re.finditer(rb"[\x20-\x7e]{8,}", data):
            chunks.append(m.group().decode("ascii", errors="ignore"))
    out: list[str] = []
    for c in chunks:
        c = c.strip()
        if len(c) < 4:
            continue
        if out and c in out[-1]:
            continue
        out.append(c)
    return "\n\n".join(out[:300])


def _model_from_paragraphs(
    filename: str,
    kind: str,
    paragraphs: list[str],
    *,
    engine: str,
    degraded: bool,
    tables: list[Table] | None = None,
) -> DocumentModel:
    nodes: list = [
        Heading(level=1, text=RichText(text=Path(filename).stem or kind.upper()))
    ]
    for tbl in tables or []:
        nodes.append(tbl)
    for para in paragraphs:
        para = para.strip()
        if para:
            nodes.append(Paragraph(text=RichText(text=para)))
    if len(nodes) == 1:
        raise CorruptedFileError(
            f"{kind.upper()} 原生解析无有效文本（建议启用 vendored anydoc）: {filename}"
        )
    return DocumentModel(
        title=Path(filename).stem,
        nodes=nodes,
        metadata={
            "importer": f"{kind}_native",
            "engine": engine,
            "degraded": degraded,
        },
    )


def _import_xls_structured(source_bytes: bytes, filename: str) -> DocumentModel | None:
    try:
        stream = read_cfb_stream(source_bytes, "Workbook") or read_cfb_stream(
            source_bytes, "Book"
        )
    except CfbError:
        return None
    if not stream:
        return None
    try:
        sheets = parse_biff8_workbook(stream)
    except Exception:
        return None
    tables: list[Table] = []
    paras: list[str] = []
    for sh in sheets:
        rows = sh.as_rows()
        if not rows:
            continue
        tables.append(Table(rows=rows, name=sh.name, header_rows=1 if len(rows) > 1 else 0))
        # Also surface sheet name for greppable round-trip when single-cell
        if len(rows) == 1 and len(rows[0]) == 1:
            paras.append(f"{sh.name}: {rows[0][0]}")
    if not tables:
        return None
    return _model_from_paragraphs(
        filename,
        "xls",
        paras,
        engine="biff8",
        degraded=False,
        tables=tables,
    )


def _import_ppt_structured(source_bytes: bytes, filename: str, kind: str) -> DocumentModel | None:
    try:
        stream = read_cfb_stream(source_bytes, "PowerPoint Document")
    except CfbError:
        return None
    if not stream:
        return None
    try:
        texts = extract_ppt_texts(stream)
    except Exception:
        return None
    if not texts:
        return None
    # Group into slide-like paragraphs (PPT atoms are already ordered)
    return _model_from_paragraphs(
        filename,
        kind,
        texts,
        engine="ppt_atoms",
        degraded=False,
    )


def _import_ole(
    source_bytes: bytes,
    filename: str,
    *,
    kind: str,
) -> DocumentModel:
    if len(source_bytes) < 8:
        raise InvalidDocumentError(f"{kind.upper()} 文件过小: {filename}")
    if not source_bytes.startswith(b"\xd0\xcf\x11\xe0"):
        raise InvalidDocumentError(f"不是 OLE/{kind.upper()} 复合文档: {filename}")

    if kind == "xls":
        structured = _import_xls_structured(source_bytes, filename)
        if structured is not None:
            return structured
    if kind in ("ppt", "pps", "pot"):
        structured = _import_ppt_structured(source_bytes, filename, kind)
        if structured is not None:
            return structured

    text = _ole_extract_text(source_bytes)
    if not text.strip():
        raise CorruptedFileError(
            f"{kind.upper()} 原生文本提取失败（建议启用 vendored anydoc）: {filename}"
        )
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return _model_from_paragraphs(
        filename,
        kind,
        paras,
        engine="ole_text",
        degraded=True,
    )


def import_xls(source_bytes: bytes, filename: str = "input.xls") -> DocumentModel:
    return _import_ole(source_bytes, filename, kind="xls")


def import_xlsb(source_bytes: bytes, filename: str = "input.xlsb") -> DocumentModel:
    """Import Excel Binary Workbook (.xlsb).

    True xlsb is a ZIP with ``xl/workbook.bin``. Mislabeled xlsx (workbook.xml)
    is accepted via the xlsx importer. Classic OLE is rare and best-effort.
    """
    if source_bytes.startswith(b"PK"):
        import zipfile
        from io import BytesIO

        try:
            with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile as exc:
            raise InvalidDocumentError(f"不是有效的 XLSB/ZIP: {filename}") from exc

        if "xl/workbook.bin" in names:
            # Prefer anydoc when compiled; otherwise fail with a clear signal.
            try:
                from app.importers.anydoc_bridge import (
                    AnydocUnavailable,
                    import_via_anydoc,
                    should_handle,
                )

                if should_handle("xlsb"):
                    model = import_via_anydoc(source_bytes, filename=filename, fmt="xlsb")
                    # Corrupted / empty binary workbook must not look like success.
                    if model is None or not getattr(model, "nodes", None):
                        raise InvalidDocumentError(
                            f"XLSB 结构无效或无可解析内容: {filename}"
                        )
                    textish = " ".join(
                        n.plain_text()
                        for n in model.nodes
                        if hasattr(n, "plain_text") and n.plain_text()
                    ).strip()
                    if not textish and not any(
                        type(n).__name__ in ("Table", "Image", "Chart")
                        for n in model.nodes
                    ):
                        raise InvalidDocumentError(
                            f"XLSB 解析结果为空: {filename}"
                        )
                    return model
            except AnydocUnavailable as exc:
                raise InvalidDocumentError(
                    f"真·XLSB（workbook.bin）需要 vendored anydoc: {filename}"
                ) from exc
            except ImportError:
                pass
            except InvalidDocumentError:
                raise
            except Exception as exc:
                raise InvalidDocumentError(
                    f"XLSB 解析失败: {filename} — {exc}"
                ) from exc
            raise InvalidDocumentError(
                f"真·XLSB（xl/workbook.bin）需启用 anydoc（MDBRIDGE_ANYDOC_ENABLED）: {filename}"
            )

        if "xl/workbook.xml" in names:
            from app.exporters.xlsx_exporter import import_xlsx

            model = import_xlsx(source_bytes, filename)
            model.metadata["detected_as"] = "xlsx_mislabeled_xlsb"
            model.metadata["degraded"] = True
            return model

        raise InvalidDocumentError(f"ZIP 中无 Excel workbook 部件: {filename}")

    return _import_ole(source_bytes, filename, kind="xlsb")


def import_ppt(source_bytes: bytes, filename: str = "input.ppt") -> DocumentModel:
    return _import_ole(source_bytes, filename, kind="ppt")


def import_pps(source_bytes: bytes, filename: str = "input.pps") -> DocumentModel:
    return _import_ole(source_bytes, filename, kind="pps")


def import_pot(source_bytes: bytes, filename: str = "input.pot") -> DocumentModel:
    return _import_ole(source_bytes, filename, kind="pot")
