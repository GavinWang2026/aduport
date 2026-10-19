"""anydoc bridge — high-fidelity Office/EPUB/spreadsheet import via Rust.

anydoc (MIT, https://github.com/firecrawl/anydoc) parses every Office family
format into one document model and serializes GitHub-Flavored Markdown. It is
pure Rust with no network calls, no ML models and no LibreOffice dependency,
which makes it a drop-in accelerator for AduPort's import side.

Two conversion channels are exposed:

* **Fast channel** — :func:`markdown_via_anydoc` returns anydoc's own GFM
  directly. Used when the export target is Markdown, so no fidelity is lost
  to the intermediate model. Text PDFs are included when
  ``anydoc_pdf_markdown`` is on (routing via :func:`should_handle_fast_md`).
* **Model channel** — :func:`import_via_anydoc` maps anydoc's document model
  onto AduPort's :class:`DocumentModel`, feeding every existing exporter
  (docx / pdf / epub / ...) with span-aware tables, real list numbering,
  LaTeX equations and embedded assets. PDFs are never imported this way.

Both channels degrade to AduPort's native importers whenever anydoc cannot
produce a complete document, so enabling this module never reduces coverage.
"""

from __future__ import annotations

from aduport.importers.import_guard import swallow_import_error
import logging
import re
from typing import Any

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    ListMarker,
    MathBlock,
    Paragraph,
    RichText,
    Table,
    TableCell,
)
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError

logger = logging.getLogger(__name__)

# AduPort source format -> anydoc format name. anydoc folds container
# variants onto one parser (docm->docx, xls/xlsb/xlsm->xlsx, pps/pot->ppt).
_FORMAT_MAP: dict[str, str] = {
    "doc": "doc",
    "docx": "docx",
    "docm": "docx",
    "odt": "odt",
    "ppt": "ppt",
    "pps": "ppt",
    "pot": "ppt",
    "pptx": "pptx",
    "pptm": "pptx",
    "ppsx": "pptx",
    "ppsm": "pptx",
    "rtf": "rtf",
    "epub": "epub",
    "xls": "xlsx",
    "xlsx": "xlsx",
    "xlsm": "xlsx",
    "xlsb": "xlsx",
    "ods": "ods",
    "odp": "odp",
    "csv": "csv",
    # PDF: Markdown fast channel only (pdf-inspector). Not used by import_via_anydoc.
    "pdf": "pdf",
}

# Formats AduPort has NO native importer for — anydoc is the only reader.
# Each entry maps to a compatible native sibling (same container family) used
# purely as a graceful fallback when anydoc is disabled, or None when no sibling
# exists. Because there is no native alternative, these formats MUST be handled
# by anydoc whenever it is enabled, regardless of the per-format `anydoc_formats`
# allow-list (which only governs formats that also have a native importer).
#
# Fast-path migration notes (shared formats → default allowlist):
#   MOVE NOW     : doc, pptx  — Rust OLE/OOXML strong; unlocks to_markdown_bytes
#   NATIVE FIRST : docx (v9 P1) — images / List Bullet / span HTML on native path;
#                  put docx back into anydoc_formats only when intentionally
#                  preferring speed over fidelity
#   ALREADY      : rtf, odt (+ anydoc-only siblings)
#   KEEP OUT     : xlsx (native header-row)
#   PDF          : no document model — never in anydoc_formats / import_via_anydoc.
#                  Text PDFs use MD fast channel via anydoc_pdf_markdown (pdf-inspector);
#                  scanned / NeedsOcr → AnydocFallback → AduPort OCR/docling.
#   ENABLED      : epub, csv — default allowlist (P2-4); native fallback when anydoc declines
ANYDOC_ONLY_SIBLINGS: dict[str, str | None] = {
    # value = native sibling importer format (None → dedicated native below)
    "ppt": "ppt",
    "pps": "pps",
    "pot": "pot",
    "xls": "xls",
    "xlsb": "xlsb",
    "xlsm": "xlsx",
    "ods": "ods",
    "odp": "odp",
    "docm": "docx",
    "pptm": "pptx",
    "ppsx": "pptx",
    "ppsm": "pptx",
}
_ANYDOC_ONLY = frozenset(ANYDOC_ONLY_SIBLINGS)


def should_handle(fmt: str) -> bool:
    """Whether anydoc should own this source format for the *model* import path.

    Used by the importer wrapper (``_anydoc_first``). anydoc-only formats are
    always owned when enabled; shared formats (with a native importer) are owned
    only when listed in ``anydoc_formats``. PDF is never owned here — it has no
    document model; see :func:`should_handle_fast_md`.
    """
    from aduport.config import settings

    if not settings.anydoc_enabled:
        return False
    fmt = fmt.lower().lstrip(".")
    if fmt == "pdf":
        return False
    if fmt in _ANYDOC_ONLY:
        return True
    allowed = settings.anydoc_format_list()
    return "*" in allowed or fmt in allowed


def should_handle_fast_md(fmt: str) -> bool:
    """Whether anydoc may take the Markdown fast channel for this format.

    Mirrors :func:`should_handle` for office/ebook sources, and additionally
    allows PDF when ``anydoc_pdf_markdown`` is on (text-layer extraction via
    pdf-inspector; OCR pages fall through to the native PDF pipeline).
    """
    from aduport.config import settings

    if not settings.anydoc_enabled or not settings.anydoc_fast_markdown:
        return False
    fmt = fmt.lower().lstrip(".")
    if fmt == "pdf":
        return bool(getattr(settings, "anydoc_pdf_markdown", True))
    return should_handle(fmt)


# anydoc format -> canonical AduPort source format, for content sniffing.
_SNIFF_MAP: dict[str, str] = {
    "doc": "doc",
    "docx": "docx",
    "odt": "odt",
    "ppt": "ppt",
    "pptx": "pptx",
    "rtf": "rtf",
    "epub": "epub",
    "xlsx": "xlsx",
    "ods": "ods",
    "odp": "odp",
    "pdf": "pdf",
    "csv": "csv",
}

_MARKER_MAP: dict[str, ListMarker] = {
    "bullet": ListMarker.BULLET,
    "decimal": ListMarker.DECIMAL,
    "lower_alpha": ListMarker.LOWER_ALPHA,
    "upper_alpha": ListMarker.UPPER_ALPHA,
    "lower_roman": ListMarker.LOWER_ROMAN,
    "upper_roman": ListMarker.UPPER_ROMAN,
}


class AnydocUnavailable(RuntimeError):
    """The anydoc extension module is not installed."""


class AnydocFallback(RuntimeError):
    """anydoc declined this document; the caller must use a native importer.

    Raised for recoverable outcomes only (unknown format, or a PDF whose pages
    need OCR). Hard failures — encrypted, malformed, over a safety limit —
    surface as AduPort errors instead, because no other importer would fare
    better and silently retrying would only mask the cause.
    """

    def __init__(self, reason: str, *, code: str = "unsupported") -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def _anydoc() -> Any:
    """Load the compiled extension (new name, then pre-rename wheel)."""
    try:
        import aduport_native._doc as anydoc  # type: ignore[import-not-found]

        return anydoc
    except ImportError:
        pass
    try:
        # Pre-rename local wheels still ship as mdbridge_native.
        import mdbridge_native._doc as anydoc  # type: ignore[import-not-found]

        return anydoc
    except ImportError as exc:  # pragma: no cover - depends on the compiled ext
        raise AnydocUnavailable(
            "AduPort 原生文档解析扩展未安装；"
            "预编译: bash scripts/install_native_wheel.sh --release；"
            "或编译: bash scripts/build_native.sh"
        ) from exc


def anydoc_available() -> bool:
    """True when the compiled anydoc module can be imported."""
    try:
        _anydoc()
    except AnydocUnavailable:
        return False
    return True


def anydoc_version() -> str | None:
    """Installed anydoc distribution version, or None when absent."""
    try:
        from importlib.metadata import version

        for dist in ("aduport-native", "mdbridge-native"):
            try:
                return version(dist)
            except Exception:
                continue
    except Exception as exc:
        return swallow_import_error(exc, where='anydoc_bridge.py:197', default=None)
    return None


def supports(fmt: str) -> bool:
    """True when anydoc has a parser for this AduPort source format."""
    return fmt.lower().lstrip(".") in _FORMAT_MAP


def sniff_format(data: bytes) -> str | None:
    """Detect the source format from content markers alone.

    anydoc reads the identity each container specification designates (PDF
    header, RTF open group, OLE stream names, ZIP mimetype / content types),
    so mislabeled and extension-less uploads still route correctly. Returns
    None for signature-less formats (CSV) and anything unrecognized.
    """
    try:
        detected = _anydoc().format_from_bytes(data)
    except AnydocUnavailable:
        return None
    except Exception:  # defensive: detection must never raise
        return None
    return _SNIFF_MAP.get(detected) if detected else None


def _translate_error(exc: BaseException, filename: str) -> Exception:
    """Map an anydoc ConvertError / PanicException onto AduPort's error contract."""
    name = type(exc).__name__
    label = filename or "文档"
    # PyO3 panics become PanicException(BaseException), not Exception — must map
    # here so callers' `except Exception` paths and native fallbacks still work.
    if name == "PanicException":
        return AnydocFallback(f"anydoc native panic: {exc}", code="panic")
    if name == "UnsupportedError":
        return AnydocFallback(f"anydoc 不支持该格式: {exc}", code="unsupported")
    if name == "NeedsOcrError":
        pages = getattr(exc, "pages", []) or []
        return AnydocFallback(
            f"PDF 需要 OCR（页码 {pages[:10]}），转交 AduPort OCR 引擎",
            code="needs_ocr",
        )
    if name == "EncryptedError":
        return InvalidDocumentError(f"文档已加密或受密码保护: {label}")
    if name == "ResourceLimitError":
        limit = getattr(exc, "limit", "unknown")
        return InvalidDocumentError(
            f"文档超出安全解析限制（{limit}），疑似解压炸弹或深度嵌套: {label}"
        )
    if name == "MissingPartError":
        part = getattr(exc, "part", "unknown")
        return CorruptedFileError(f"文档缺少必需部件 {part}: {label}")
    if name == "MalformedError":
        part = getattr(exc, "part", None)
        detail = f"（部件 {part}）" if part else ""
        return CorruptedFileError(f"文档结构不可用，无法提取内容{detail}: {label}")
    return AnydocFallback(f"anydoc 转换失败: {exc}", code="unknown")


def _reraise_or_map_native(exc: BaseException, filename: str) -> Exception:
    """Re-raise process control signals; map everything else including panics."""
    if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
        raise exc
    return _translate_error(exc, filename)


# --------------------------------------------------------------------------
# Fast channel: straight to Markdown
# --------------------------------------------------------------------------


def markdown_via_anydoc(data: bytes, fmt: str, filename: str = "") -> str:
    """Convert to GFM Markdown with anydoc, bypassing the intermediate model.

    Raises :class:`AnydocFallback` when a native importer should take over.
    """
    mod = _anydoc()
    target = _FORMAT_MAP.get(fmt.lower().lstrip("."))
    if target is None:
        raise AnydocFallback(f"anydoc 无此格式解析器: {fmt}")
    try:
        # CSV has no signature; PDF is named so a wrong extension still hits
        # pdf-inspector. Other formats trust content sniffing over the label.
        explicit = target if target in ("csv", "pdf") else None
        text = mod.to_markdown_bytes(data, explicit)
        if target == "epub":
            text = _drop_epub_dc_title_echo(text)
            text = _drop_footnote_heading(text)
        elif target == "pdf":
            text = _split_embedded_fences(text)
            text = _drop_footnote_heading(text)
        return text
    except OSError as exc:
        raise CorruptedFileError(f"无法读取文档: {filename or fmt} — {exc}") from exc
    except Exception as exc:
        raise _translate_error(exc, filename) from exc
    except BaseException as exc:
        raise _reraise_or_map_native(exc, filename) from exc


# --------------------------------------------------------------------------
# Model channel: anydoc document model -> AduPort DocumentModel
# --------------------------------------------------------------------------


class _InlineBuilder:
    """Accumulates anydoc inlines into offset-based RichText."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.marks: list[InlineMark] = []
        self.length = 0
        self.images: list[Image] = []
        self.checked: bool | None = None

    def _add(self, chunk: str) -> tuple[int, int]:
        start = self.length
        self.text.append(chunk)
        self.length += len(chunk)
        return start, self.length

    def feed(self, inlines: list[Any] | None, assets: dict[int, Any]) -> None:
        for node in inlines or []:
            kind = node.kind
            if kind == "text":
                chunk = node.text or ""
                if not chunk:
                    continue
                start, end = self._add(chunk)
                style = node.style
                if style is not None:
                    # `code` wins as a mark type because AduPort's InlineMark
                    # is single-valued; bold/italic still apply alongside.
                    if style.code:
                        self.marks.append(
                            InlineMark(type=InlineMarkType.CODE, start=start, end=end)
                        )
                    if style.bold:
                        self.marks.append(
                            InlineMark(type=InlineMarkType.BOLD, start=start, end=end)
                        )
                    if style.italic:
                        self.marks.append(
                            InlineMark(type=InlineMarkType.ITALIC, start=start, end=end)
                        )
                    if style.strike:
                        self.marks.append(
                            InlineMark(
                                type=InlineMarkType.STRIKETHROUGH, start=start, end=end
                            )
                        )
            elif kind == "link":
                start = self.length
                self.feed(node.content, assets)
                end = self.length
                target = node.target
                if target is not None and end > start:
                    href = target.value
                    if target.kind == "anchor":
                        href = f"#{href.lstrip('#')}"
                    self.marks.append(
                        InlineMark(
                            type=InlineMarkType.LINK, start=start, end=end, href=href
                        )
                    )
            elif kind == "image":
                alt = node.alt or ""
                if alt:
                    self._add(alt)
                self.images.append(_image_from_inline(node, assets))
            elif kind == "note_ref":
                note_id = node.note_id or ""
                if note_id:
                    self._add(f"[^{note_id}]")
            elif kind == "line_break":
                self._add("\n")
            elif kind == "math":
                expr = (node.text or "").strip()
                if expr:
                    self._add(f"${expr}$")
            elif kind == "checkbox":
                self.checked = bool(node.checked)
            # `anchor` is a zero-width target marker with no text of its own.

    def build(self) -> RichText:
        return RichText(text="".join(self.text), marks=self.marks)


def _image_from_inline(node: Any, assets: dict[int, Any]) -> Image:
    source = node.source
    alt = node.alt or ""
    if source is None or source.kind == "unavailable":
        return Image(data=b"", alt_text=alt, mime_type="application/octet-stream")
    if source.kind == "external":
        return Image(
            data=b"", alt_text=alt, mime_type="image/png", url=source.url or ""
        )
    asset = assets.get(source.asset_id if source.asset_id is not None else -1)
    if asset is None:
        return Image(data=b"", alt_text=alt, mime_type="application/octet-stream")
    return Image(
        data=bytes(asset.data),
        alt_text=alt,
        mime_type=asset.media_type or "application/octet-stream",
        origin_part=asset.origin_part,
    )


def _rich(inlines: list[Any] | None, assets: dict[int, Any]) -> tuple[RichText, list[Image]]:
    builder = _InlineBuilder()
    builder.feed(inlines, assets)
    return builder.build(), builder.images


def _flatten_cell_text(blocks: list[Any] | None, assets: dict[int, Any]) -> str:
    """Collapse a cell's blocks into one line, as the flat grid requires."""
    parts: list[str] = []
    for block in blocks or []:
        kind = block.kind
        if kind in ("heading", "paragraph"):
            rich, _ = _rich(block.content, assets)
            if rich.text.strip():
                parts.append(rich.text.strip())
        elif kind == "code_block":
            if block.text:
                parts.append(block.text.strip())
        elif kind == "math":
            if block.text:
                parts.append(f"${block.text.strip()}$")
        elif kind == "list" and block.list is not None:
            for item in block.list.items:
                inner = _flatten_cell_text(item.blocks, assets)
                if inner:
                    parts.append(inner)
        elif kind == "block_quote":
            inner = _flatten_cell_text(block.blocks, assets)
            if inner:
                parts.append(inner)
        elif kind == "table" and block.table is not None:
            # Nested table inside a cell: keep the text, drop the structure.
            for row in block.table.grid:
                for slot in row:
                    if slot.kind == "origin" and slot.cell is not None:
                        inner = _flatten_cell_text(slot.cell.blocks, assets)
                        if inner:
                            parts.append(inner)
    # <br> keeps multi-paragraph cells readable inside a GFM table cell.
    return "<br>".join(p for p in parts if p)


def _convert_table(table: Any, assets: dict[int, Any]) -> Table:
    rows: list[list[str]] = []
    cells: list[list[TableCell]] = []
    for row in table.grid:
        flat_row: list[str] = []
        cell_row: list[TableCell] = []
        for slot in row:
            if slot.kind == "origin" and slot.cell is not None:
                text = _flatten_cell_text(slot.cell.blocks, assets)
                flat_row.append(text)
                cell_row.append(
                    TableCell(
                        text=text,
                        row_span=slot.cell.row_span,
                        col_span=slot.cell.col_span,
                    )
                )
            else:
                # A covered position renders empty but remembers its origin,
                # so span-aware exporters can rebuild the merge.
                flat_row.append("")
                cell_row.append(
                    TableCell(
                        text="",
                        covered=True,
                        origin_row=slot.origin_row,
                        origin_col=slot.origin_col,
                    )
                )
        rows.append(flat_row)
        cells.append(cell_row)
    return Table(
        rows=rows,
        cells=cells,
        header_rows=table.header_rows,
        kind=table.kind if table.kind in ("data", "layout") else "data",
    )


def _marker_number(marker: str, start: int, index: int) -> int:
    """Ordinal an item carries, honouring the list's own start value."""
    return start + index if marker != "bullet" else 0


def _convert_list(block: Any, assets: dict[int, Any], level: int, out: list[Any]) -> None:
    lst = block.list
    if lst is None:
        return
    marker_name = lst.marker
    ordered = marker_name != "bullet"
    marker = _MARKER_MAP.get(marker_name, ListMarker.BULLET)
    for index, item in enumerate(lst.items):
        head: RichText | None = None
        extra: list[Paragraph] = []
        pending: list[Any] = []
        checked: bool | None = None
        for sub in item.blocks:
            kind = sub.kind
            if kind in ("heading", "paragraph"):
                builder = _InlineBuilder()
                builder.feed(sub.content, assets)
                rich = builder.build()
                if builder.checked is not None:
                    checked = builder.checked
                pending.extend(builder.images)
                if head is None:
                    head = rich
                elif rich.text.strip():
                    extra.append(Paragraph(text=rich))
            elif kind == "list":
                # Nested list: emit after this item at one level deeper.
                pending.append(("list", sub))
            else:
                pending.append(("block", sub))
        if head is not None:
            from aduport.importers.list_markers import rich_without_redundant_list_prefix

            head = rich_without_redundant_list_prefix(head, ordered=ordered)
        item_node = ListItem(
            text=head if head is not None else RichText(text=""),
            ordered=ordered,
            level=level,
            checked=checked,
            children=extra,
            marker=marker,
            number=_marker_number(marker_name, lst.start, index) or None,
            marker_label=item.marker_label,
        )
        out.append(item_node)
        for entry in pending:
            if isinstance(entry, Image):
                out.append(entry)
            elif isinstance(entry, tuple) and entry[0] == "list":
                _convert_list(entry[1], assets, level + 1, out)
            elif isinstance(entry, tuple) and entry[0] == "block":
                _convert_block(entry[1], assets, out)


def _convert_block(block: Any, assets: dict[int, Any], out: list[Any]) -> None:
    kind = block.kind
    if kind == "heading":
        rich, images = _rich(block.content, assets)
        level = block.level if block.level and 1 <= block.level <= 6 else 1
        out.append(Heading(level=level, text=rich, anchor=block.anchor))
        out.extend(images)
    elif kind == "paragraph":
        rich, images = _rich(block.content, assets)
        if rich.text.strip() or not images:
            out.append(Paragraph(text=rich))
        out.extend(images)
    elif kind == "list":
        _convert_list(block, assets, 0, out)
    elif kind == "table" and block.table is not None:
        out.append(_convert_table(block.table, assets))
    elif kind == "block_quote":
        paragraphs: list[RichText | str] = []
        nested: list[Any] = []
        for sub in block.blocks or []:
            if sub.kind in ("heading", "paragraph"):
                rich, images = _rich(sub.content, assets)
                if rich.text.strip():
                    paragraphs.append(rich)
                nested.extend(images)
            else:
                _convert_block(sub, assets, nested)
        out.append(Blockquote(paragraphs=paragraphs))
        out.extend(nested)
    elif kind == "code_block":
        out.append(CodeBlock(lang=block.lang or "", content=block.text or ""))
    elif kind == "rule":
        out.append(HorizontalRule())
    elif kind == "math":
        out.append(MathBlock(content=(block.text or "").strip(), display=True))


def prefer_native_importer(fmt: str, data: bytes) -> bool:
    """Packages we wrote carry structure anydoc's fast path flattens."""
    kind = (fmt or "").lower().lstrip(".")
    if kind == "odt":
        return aduport_authored_odt(data)
    if kind == "rtf":
        return b"AduPort" in data[:800]
    # anydoc turns every CSV into one table, including ``#`` headings,
    # lists, and the ``_CONTENT`` prose column. The native importer keeps
    # a real grid as a table and restores that prose.
    if kind == "csv":
        return True
    if kind in ("odp", "ods"):
        return _zip_member_contains(data, "meta.xml", b"AduPort")
    if kind == "pptx":
        return _zip_member_contains(data, "docProps/core.xml", b"AduPort")
    if kind == "epub":
        return _zip_contains(data, b'content="AduPort"', suffix=(".xhtml", ".html", ".opf"))
    return False


def _zip_contains(data: bytes, needle: bytes, *, suffix: tuple[str, ...]) -> bool:
    if not data.startswith(b"PK"):
        return False
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.endswith(suffix)][:12]
            for name in names:
                if needle in zf.read(name):
                    return True
    except (KeyError, zipfile.BadZipFile, OSError):
        return False
    return False


def _zip_member_contains(data: bytes, member: str, needle: bytes) -> bool:
    if not data.startswith(b"PK"):
        return False
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            blob = zf.read(member)
    except (KeyError, zipfile.BadZipFile, OSError):
        return False
    return needle in blob


def aduport_authored_odt(data: bytes) -> bool:
    """True when this ODT carries AduPort styles anydoc does not round-trip.

    Those packages (code fences, quotes, lists) must stay on the native
    importer. The marker lives in compressed ``content.xml``, so sniff the
    zip member rather than the raw package bytes.
    """
    if not data.startswith(b"PK"):
        return False
    import zipfile
    from io import BytesIO

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            xml = zf.read("content.xml")
    except (KeyError, zipfile.BadZipFile, OSError):
        return False
    return b"urn:aduport:odt" in xml or b"AduPort" in xml


_EPUB_H1_RE = re.compile(r"^# (.+?)\s*$")


def _drop_footnote_heading(markdown: str) -> str:
    """AnyDoc turns the visual "Footnotes" label into ``### Footnotes``.

    ``## Footnotes`` is a substring of that heading, and round-trip checks
    reject it. The footnote body stays.
    """
    kept: list[str] = []
    for line in markdown.splitlines():
        label = line.lstrip("#").strip().casefold()
        if label == "footnotes":
            continue
        kept.append(line)
    suffix = "\n" if markdown.endswith("\n") else ""
    return "\n".join(kept).strip("\n") + suffix


def _split_embedded_fences(markdown: str) -> str:
    """AnyDoc sometimes glues a PDF fence onto the previous line."""
    lines: list[str] = []
    for line in markdown.splitlines():
        idx = line.find("```")
        if idx > 0:
            head = line[:idx].rstrip()
            if head:
                lines.append(head)
            lines.append(line[idx:])
        else:
            lines.append(line)
    suffix = "\n" if markdown.endswith("\n") else ""
    return "\n".join(lines) + suffix


def _drop_epub_dc_title_echo(markdown: str) -> str:
    """anydoc promotes ``dc:title`` to an H1 the chapter already contains."""
    lines = markdown.splitlines()
    first: tuple[int, str] | None = None
    second: tuple[int, str] | None = None
    for index, line in enumerate(lines):
        match = _EPUB_H1_RE.match(line)
        if match is None:
            continue
        if first is None:
            first = (index, match.group(1).strip())
            continue
        second = (index, match.group(1).strip())
        break
    if first is None or second is None or first[1] != second[1]:
        return markdown
    end = first[0] + 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    kept = lines[: first[0]] + lines[end:]
    suffix = "\n" if markdown.endswith("\n") else ""
    return "\n".join(kept).strip("\n") + suffix


def _drop_repeated_h1(nodes: list[Any]) -> list[Any]:
    """Drop the package-title H1 when the next H1 repeats it."""
    first: int | None = None
    for index, node in enumerate(nodes):
        if not isinstance(node, Heading) or int(getattr(node, "level", 1) or 1) != 1:
            continue
        text = node.plain_text().strip()
        if not text:
            continue
        if first is None:
            first = index
            continue
        if text != nodes[first].plain_text().strip():
            return nodes
        drop = {first}
        for between in range(first + 1, index):
            mid = nodes[between]
            if isinstance(mid, Paragraph) and not (mid.plain_text() or "").strip():
                drop.add(between)
                continue
            return nodes
        return [node for i, node in enumerate(nodes) if i not in drop]
    return nodes


def _drop_fallback_headings(nodes: list[Any], filename: str) -> list[Any]:
    """Package labels (Untitled, the filename stem) are not document headings."""
    from aduport.core.title_source import _is_fallback, _stem

    stem = _stem(filename)
    out: list[Any] = []
    for node in nodes:
        if isinstance(node, Heading) and _is_fallback(node.plain_text(), stem):
            continue
        if isinstance(node, Paragraph) and not (node.plain_text() or "").strip():
            continue
        out.append(node)
    return out


def import_via_anydoc(data: bytes, filename: str = "", fmt: str = "") -> DocumentModel:
    """Parse bytes with anydoc and map the result onto AduPort's model.

    PDFs are rejected with :class:`AnydocFallback`: anydoc renders them to
    Markdown directly and exposes no document model, so AduPort's own PDF
    pipeline (text layer / docling / OCR) stays in charge.
    """
    mod = _anydoc()
    target = _FORMAT_MAP.get(fmt.lower().lstrip(".")) if fmt else None
    if fmt and target is None:
        raise AnydocFallback(f"anydoc 无此格式解析器: {fmt}")
    if target == "pdf" or fmt.lower().lstrip(".") == "pdf":
        raise AnydocFallback("PDF 无文档模型形态，交由 AduPort PDF 管线处理")
    try:
        doc = mod.to_document(data, target if target == "csv" else None)
    except OSError as exc:
        raise CorruptedFileError(f"无法读取文档: {filename or fmt} — {exc}") from exc
    except Exception as exc:
        raise _translate_error(exc, filename) from exc
    except BaseException as exc:
        raise _reraise_or_map_native(exc, filename) from exc

    assets = {a.id: a for a in doc.assets}
    nodes: list[Any] = []
    for block in doc.blocks:
        _convert_block(block, assets, nodes)
    nodes = _drop_fallback_headings(nodes, filename)
    if (fmt or "").lower().lstrip(".") == "epub":
        nodes = _drop_repeated_h1(nodes)

    # Footnote and endnote bodies land at the end, referenced by [^id].
    for note in doc.notes:
        text = _flatten_cell_text(note.blocks, assets).replace("<br>", " ")
        nodes.append(
            Footnote(
                id=note.id,
                text=text,
                kind=note.kind if note.kind in ("footnote", "endnote") else "footnote",
            )
        )

    title = ""
    for node in nodes:
        if isinstance(node, Heading) and node.level == 1:
            title = node.plain_text().strip()
            break

    model = DocumentModel(title="", nodes=nodes)
    model.metadata["importer"] = "anydoc"
    if title:
        model.metadata["detected_title"] = title
    return model
