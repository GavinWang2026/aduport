"""Minimal PDF writer (no WeasyPrint) — text-layer PDF for CI / macOS fallback.

Two rendering paths:

* **ASCII path** (original): Helvetica + WinAnsi.  Tiny and fast, but any
  non-Latin-1 character is replaced with ``?``.
* **CJK path** (v0.17): embeds a *subset* of a system CJK font so that Chinese,
  Japanese and Korean text renders correctly.  Requires ``fontTools`` and a
  discoverable CJK font file.

When neither ``fontTools`` nor a CJK font is available and the document
contains non-Latin-1 text, we **raise** instead of silently emitting a
corrupted PDF (see ``ADUPORT_PDF_CJK_FALLBACK``).
"""

from __future__ import annotations

import hashlib
import logging
import os
from functools import lru_cache
from pathlib import Path

from aduport.config import settings
from aduport.core.document_model import (
    Blockquote,
    Chart,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    SpeakerNote,
    Table,
    TextBox,
)
from aduport.core.exceptions import ExportError

_log = logging.getLogger("aduport.pdf_simple")

_LINES_PER_PAGE = 45
# Soft-wrap width for the simple text PDF path (letter page ≈ 72 chars @ 11pt).
_WRAP_WIDTH = 72


def _wrap_text(text: str, width: int = _WRAP_WIDTH) -> list[str]:
    """Word-wrap without discarding content (replaces hard [:110] truncation)."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for para in text.split("\n"):
        if not para:
            out.append("")
            continue
        # Prefer wrap at spaces; otherwise hard-slice long tokens.
        while len(para) > width:
            cut = para.rfind(" ", 0, width + 1)
            if cut <= 0:
                cut = width
            out.append(para[:cut].rstrip())
            para = para[cut:].lstrip()
        out.append(para)
    return out or [""]


class CJKFontUnavailable(Exception):
    """Raised when CJK text cannot be rendered with the available resources."""


# ─────────────────────────────────────────────────────────────────────────────
# CJK font discovery
# ─────────────────────────────────────────────────────────────────────────────

# (path, face_index).  Ordered by preference — TrueType outlines first because
# CIDFontType2 + /CIDToGIDMap /Identity has the broadest viewer support.
_CJK_FONT_CANDIDATES: tuple[tuple[str, int], ...] = (
    # ── Linux / container (Noto CJK) ──
    ("/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf", 0),
    ("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf", 0),
    ("/usr/share/fonts/noto-cjk/NotoSansCJKsc-Regular.otf", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
    ("/usr/share/fonts/wenquanyi/wqy-zenhei/wqy-zenhei.ttc", 0),
    # ── macOS (TrueType outlines preferred over CFF) ──
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
    ("/Library/Fonts/Arial Unicode.ttf", 0),
    # ── Windows ──
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/simsun.ttc", 0),
)


def _cjk_font_path_override() -> str | None:
    return (getattr(settings, "pdf_cjk_font_path", "") or "").strip() or None


@lru_cache(maxsize=1)
def _find_cjk_font() -> tuple[str, int] | None:
    """Locate a usable CJK font. Result cached for process lifetime."""
    override = _cjk_font_path_override()
    if override:
        if Path(override).is_file():
            return (override, 0)
        _log.warning("ADUPORT_PDF_CJK_FONT_PATH set but file missing: %s", override)

    for path, idx in _CJK_FONT_CANDIDATES:
        if Path(path).is_file():
            return (path, idx)

    # Last resort: search a few font directories by keyword.
    for root in ("/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts"):
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            for p in base.rglob("*.tt[cf]"):
                name = p.name.lower()
                if any(
                    k in name
                    for k in ("notosanscjk", "notosanssc", "wqy", "zenhei", "heiti", "songti")
                ):
                    return (str(p), 0)
        except (OSError, PermissionError):
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Subsetting
# ─────────────────────────────────────────────────────────────────────────────

# Subsetting a 20–55 MB CJK font costs ~0.1–0.3 s, so cache aggressively.
# Keyed by (font path, face index, sha256 of the sorted character set).
_SUBSET_CACHE: "dict[tuple[str, int, str], dict]" = {}
_SUBSET_CACHE_MAX = 4


class _Subset:
    """Result of subsetting a CJK font for a specific character set."""

    __slots__ = (
        "ttf",
        "cmap",
        "units_per_em",
        "is_cff",
        "ascent",
        "descent",
        "bbox",
        "cap_height",
        "widths",
        "stem_v",
        "glyph_order",
    )

    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def _needs_cjk(lines: list[str]) -> bool:
    for line in lines:
        for ch in line:
            if ord(ch) > 0x7F:
                return True
    return False


def _collect_chars(lines: list[str]) -> set[str]:
    chars: set[str] = set()
    for line in lines:
        chars.update(line)
    # Keep a minimal ASCII set so latin text/punctuation still renders.
    chars.update("abcdefghijklmnopqrstuvwxyz")
    chars.update("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    chars.update("0123456789")
    chars.update(" .,:;!?-_/\\()[]{}<>\"'`#*+=|~@$%^&")
    chars.add("\n")
    chars.add(" ")
    return chars


@lru_cache(maxsize=4)
def _load_face(path: str, face_index: int):
    """Load (and memoise) a TTFont / TTCollection face."""
    from fontTools.ttLib import TTFont, TTCollection

    if path.lower().endswith(".ttc"):
        faces = TTCollection(path, lazy=True).fonts
        return faces[min(face_index, len(faces) - 1)]
    return TTFont(path, lazy=True)


def _subset_font(chars: frozenset[str]) -> _Subset:
    """Subset the discovered CJK font down to ``chars`` (cached)."""
    found = _find_cjk_font()
    if not found:
        raise CJKFontUnavailable("未找到可用的 CJK 字体")

    path, idx = found
    key_src = "".join(sorted(chars))
    cache_key = (path, idx, hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:32])
    hit = _SUBSET_CACHE.get(cache_key)
    if hit is not None:
        return hit

    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError as exc:  # pragma: no cover - fontTools is a soft dep
        raise CJKFontUnavailable("未安装 fontTools，无法嵌入 CJK 字体") from exc

    # Fresh TTFont load — lazy parse to cut peak RSS vs full 20–55MB face.
    working = TTFont(path, fontNumber=idx, lazy=True)

    opts = subset.Options()
    opts.drop_tables += ["DSIG"]
    opts.desubroutinize = True
    opts.retain_gids = False
    opts.notdef_outline = True
    opts.recalc_bounds = True
    sub = subset.Subsetter(options=opts)
    sub.populate(unicodes=[ord(c) for c in chars if c.strip() or c == " "])
    sub.subset(working)

    import io

    buf = io.BytesIO()
    working.save(buf)
    ttf_bytes = buf.getvalue()

    cmap = working.getBestCmap()
    head = working["head"]
    upem = head.unitsPerEm or 1000

    os2 = working["OS/2"] if "OS/2" in working else None
    hhea = working["hhea"] if "hhea" in working else None
    ascent = getattr(os2, "sTypoAscender", None) or getattr(hhea, "ascent", 880)
    descent = getattr(os2, "sTypoDescender", None) or getattr(hhea, "descent", -120)
    cap_height = getattr(os2, "sCapHeight", None) or int(upem * 0.7)

    hmtx = working["hmtx"]
    glyph_order = working.getGlyphOrder()
    widths: list[int] = []
    for name in glyph_order:
        try:
            widths.append(int(hmtx[name][0]))
        except KeyError:
            widths.append(int(upem))

    # unitsPerEm → 1000 scale for the PDF font matrix
    scale = 1000.0 / upem
    widths = [int(round(w * scale)) for w in widths]

    is_cff = working.sfntVersion == "OTTO"
    bbox = [head.xMin, head.yMin, head.xMax, head.yMax]
    bbox = [int(round(v * scale)) for v in bbox]

    result = _Subset(
        ttf=ttf_bytes,
        cmap=cmap,
        units_per_em=upem,
        is_cff=is_cff,
        ascent=int(round(ascent * scale)),
        descent=int(round(descent * scale)),
        bbox=bbox,
        cap_height=int(round(cap_height * scale)),
        widths=widths,
        stem_v=80,
        glyph_order=list(glyph_order),
    )
    try:
        working.close()
    except Exception:
        pass
    del working

    if len(_SUBSET_CACHE) >= _SUBSET_CACHE_MAX:
        _SUBSET_CACHE.clear()
    _SUBSET_CACHE[cache_key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def export_pdf_simple(model: DocumentModel) -> bytes:
    """Emit a text-layer PDF from the document model.

    Uses Helvetica for pure-ASCII documents and an embedded CJK subset
    otherwise.  Raises :class:`ExportError` when CJK text is present but
    cannot be rendered, rather than silently producing mojibake.

    Sets ``model.metadata["pdf_engine"] = "pdf_simple"`` and
    ``model.metadata["pdf_degraded"] = True`` so callers can surface the
    WeasyPrint fallback to API clients.
    """
    meta = model.metadata if isinstance(model.metadata, dict) else {}
    meta = dict(meta)
    meta["pdf_engine"] = "pdf_simple"
    meta["pdf_degraded"] = True
    model.metadata = meta

    lines = _model_lines(model)
    if not lines:
        lines = ["(empty)"]

    if not _needs_cjk(lines):
        return _append_ast_sidecar(_export_ascii(lines), model)

    fallback = (getattr(settings, "pdf_cjk_fallback", "error") or "error").strip().lower()
    try:
        return _append_ast_sidecar(_export_cjk(lines), model)
    except CJKFontUnavailable as exc:
        if fallback == "ascii":
            _log.warning("CJK 渲染不可用，按配置降级为 ASCII：%s", exc)
            return _append_ast_sidecar(_export_ascii(lines), model)
        raise ExportError(
            f"文档包含非 ASCII 字符（如中文），但当前环境无法嵌入 CJK 字体：{exc}。"
            "请安装 fontTools 并配置中文字体（ADUPORT_PDF_CJK_FONT_PATH），"
            "或安装 Pango 以启用 WeasyPrint；"
            "若确认可以丢失非 ASCII 字符，可设置 ADUPORT_PDF_CJK_FALLBACK=ascii。"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("CJK PDF 渲染失败：%s", exc)
        if fallback == "ascii":
            return _append_ast_sidecar(_export_ascii(lines), model)
        raise ExportError(f"PDF 渲染失败: {exc}") from exc


_AST_BEGIN = b"\n%%ADUPORT-AST-v1\n"
_AST_END = b"\n%%ADUPORT-AST-END\n"


def _append_ast_sidecar(pdf_bytes: bytes, model: DocumentModel) -> bytes:
    """Embed a JSON AST sidecar after %%EOF (v6/T2-#11 / D-13).

    PDF readers stop at EOF; our importer recovers the model for round-trip.
    Default off (`ADUPORT_PDF_AST_SIDECAR=0`) — not layout fidelity.
    """
    from aduport.config import settings

    if not bool(getattr(settings, "pdf_ast_sidecar", False)):
        return pdf_bytes
    try:
        from aduport.exporters.json_exporter import export_json
        import base64

        payload = export_json(model).encode("utf-8")
        # Cap sidecar size so pathological trees don't explode PDF downloads
        if len(payload) > 2_000_000:
            return pdf_bytes
        return pdf_bytes + _AST_BEGIN + base64.b64encode(payload) + _AST_END
    except Exception as exc:
        _log.debug("pdf AST sidecar skipped: %s", exc)
        return pdf_bytes


def extract_ast_sidecar(pdf_bytes: bytes) -> DocumentModel | None:
    """Recover DocumentModel from an embedded AST sidecar, if present."""
    try:
        start = pdf_bytes.rfind(_AST_BEGIN)
        end = pdf_bytes.rfind(_AST_END)
        if start < 0 or end < 0 or end <= start:
            return None
        import base64

        from aduport.exporters.json_exporter import import_json

        b64 = pdf_bytes[start + len(_AST_BEGIN) : end]
        raw = base64.b64decode(b64)
        model = import_json(raw, "sidecar.json")
        meta = dict(model.metadata or {})
        meta["pdf_ast_sidecar"] = True
        model.metadata = meta
        return model
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ASCII path (original Helvetica implementation)
# ─────────────────────────────────────────────────────────────────────────────


def _export_ascii(lines: list[str]) -> bytes:
    pages: list[list[str]] = [
        lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)
    ]
    content_bodies = [_ascii_page_stream(p) for p in pages]

    n = len(pages)
    font_id = 3 + 2 * n
    widget_id = font_id + 1
    annot_id = font_id + 2
    acro_id = font_id + 3
    kids = [f"{3 + 2 * i} 0 R" for i in range(n)]

    objects: list[bytes] = [
        f"<< /Type /Catalog /Pages 2 0 R /AcroForm {acro_id} 0 R >>".encode(),
        f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>".encode(),
    ]
    for i, body in enumerate(content_bodies):
        annots = ""
        if i == 0:
            annots = f" /Annots [ {widget_id} 0 R {annot_id} 0 R ]"
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {4 + 2 * i} 0 R "
                f"/Resources<< /Font<< /F1 {font_id} 0 R >> >>"
                f"{annots} >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(body)} >>stream\n".encode() + body + b"\nendstream")
    # Helvetica's built-in encoding is StandardEncoding, where byte 0x60 is
    # quoteleft (U+2018). Our text layer is ASCII, so declare WinAnsi and
    # keep backticks as backticks.
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    objects.extend(
        _pdf_form_annot_objects(
            widget_id=widget_id,
            annot_id=annot_id,
            acro_id=acro_id,
            contents=_annot_contents_from_lines(lines),
        )
    )

    return _assemble(objects)


def _pdf_escape_literal(text: str) -> str:
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def _annot_contents_from_lines(lines: list[str]) -> str:
    """Prefer first Comment-like line; fallback to sentinel for Annot gate."""
    for line in lines:
        if "[Comment" in line:
            return _pdf_escape_literal(line[:180])
    return "aduport-pdf-annot"


def _pdf_form_annot_objects(
    *,
    widget_id: int,
    annot_id: int,
    acro_id: int,
    contents: str = "aduport-pdf-annot",
) -> list[bytes]:
    """Minimal AcroForm text field + Text sticky annotation on page 1."""
    # Widget (text field) — Parent page is always object 3 (first page)
    widget = (
        b"<< /Type /Annot /Subtype /Widget /FT /Tx /T (aduport_field) "
        b"/V (AduPort) /DV (AduPort) "
        b"/Rect [72 720 300 744] /F 4 /P 3 0 R "
        b"/DA (/Helv 10 Tf 0 g) >>"
    )
    # Sticky note annotation — Contents carry Comment text when present
    safe = _pdf_escape_literal(contents)[:200] or "aduport-pdf-annot"
    text_annot = (
        f"<< /Type /Annot /Subtype /Text /Name /Comment "
        f"/Contents ({safe}) /Rect [500 740 520 760] "
        f"/C [1 0.8 0] /F 4 /P 3 0 R >>"
    ).encode()
    acro = f"<< /Fields [ {widget_id} 0 R ] /NeedAppearances true >>".encode()
    return [widget, text_annot, acro]


def _pdf_escape(text: str) -> str:
    ascii_parts: list[str] = []
    for ch in text:
        o = ord(ch)
        if 32 <= o < 127 and ch not in ("\\", "(", ")"):
            ascii_parts.append(ch)
        elif ch in ("\\", "(", ")"):
            ascii_parts.append("\\" + ch)
        elif ch in ("\n", "\r", "\t"):
            ascii_parts.append(" ")
        else:
            ascii_parts.append("?")
    return "".join(ascii_parts)


def _ascii_page_stream(lines: list[str]) -> bytes:
    y = 750
    cmds = ["BT", "/F1 11 Tf", "14 TL"]
    first = True
    for line in lines:
        safe = _pdf_escape(line)
        if first:
            cmds.append(f"50 {y} Td ({safe}) Tj")
            first = False
        else:
            cmds.append(f"T* ({safe}) Tj")
    cmds.append("ET")
    return "\n".join(cmds).encode("latin-1", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# CJK path — embedded subset font (Type0 / Identity-H)
# ─────────────────────────────────────────────────────────────────────────────


def _export_cjk(lines: list[str]) -> bytes:
    chars = _collect_chars(lines)
    sub = _subset_font(frozenset(chars))

    pages: list[list[str]] = [
        lines[i : i + _LINES_PER_PAGE] for i in range(0, len(lines), _LINES_PER_PAGE)
    ]
    n = len(pages)

    # Object id plan:
    #   1              Catalog
    #   2              Pages
    #   3+2i           Page i
    #   4+2i           Content i
    #   b = 3+2n       Type0 font
    #   b+1            CIDFont (Type0 or Type2)
    #   b+2            FontDescriptor
    #   b+3            FontFile2/3 stream
    #   b+4            ToUnicode CMap stream
    base = 3 + 2 * n
    f_type0, f_cid, f_desc, f_file, f_tounicode = (
        base, base + 1, base + 2, base + 3, base + 4,
    )

    glyph_cache: dict[str, int] = {}

    def gid_of(ch: str) -> int | None:
        """Unicode → GID for the subset font (None when unsupported)."""
        cached = glyph_cache.get(ch)
        if cached is not None:
            return cached
        name = sub.cmap.get(ord(ch)) if sub.cmap else None
        if name is None:
            return None
        gid = _glyph_id(sub, name)
        glyph_cache[ch] = gid
        return gid

    content_bodies = [_cjk_page_stream(p, gid_of) for p in pages]

    # Widths array: /W [0 [w0 w1 ...]] indexed by GID (/CIDToGIDMap /Identity)
    widths = sub.widths or [1000]
    w_array = " ".join(str(w) for w in widths)

    font_name = "/AduPortCJK"
    cid_subtype = b"/CIDFontType0" if sub.is_cff else b"/CIDFontType2"
    file_key = b"/FontFile3" if sub.is_cff else b"/FontFile2"
    file_subtype = b"/Subtype /CIDFontType0C\n" if sub.is_cff else b""

    bbox = sub.bbox or [0, -200, 1000, 900]
    bbox_str = f"[{bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}]"

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",  # patched below after object count known
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + 2 * i} 0 R' for i in range(n))}] "
        f"/Count {n} >>".encode(),
    ]
    for i, body in enumerate(content_bodies):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {4 + 2 * i} 0 R "
                f"/Resources<< /Font<< /F1 {f_type0} 0 R >> >> >>"
            ).encode()
        )
        objects.append(f"<< /Length {len(body)} >>stream\n".encode() + body + b"\nendstream")

    # Type0
    objects.append(
        (
            f"<< /Type /Font /Subtype /Type0 /BaseFont {font_name} "
            f"/Encoding /Identity-H /DescendantFonts [{f_cid} 0 R] "
            f"/ToUnicode {f_tounicode} 0 R >>"
        ).encode()
    )
    # CIDFont
    objects.append(
        (
            f"<< /Type /Font /Subtype {cid_subtype.decode()} /BaseFont {font_name} "
            f"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            f"/FontDescriptor {f_desc} 0 R "
            f"/DW 1000 /W [0 [{w_array}]] "
            f"/CIDToGIDMap /Identity >>"
        ).encode()
    )
    # FontDescriptor
    objects.append(
        (
            f"<< /Type /FontDescriptor /FontName {font_name} /Flags 4 "
            f"/FontBBox {bbox_str} /ItalicAngle 0 "
            f"/Ascent {sub.ascent} /Descent {sub.descent} /CapHeight {sub.cap_height} "
            f"/StemV {sub.stem_v} {file_key.decode()} {f_file} 0 R >>"
        ).encode()
    )
    # Embedded font program
    objects.append(
        f"<< /Length {len(sub.ttf)} /Length1 {len(sub.ttf)} {file_subtype.decode()}>>stream\n".encode()
        + sub.ttf
        + b"\nendstream"
    )
    # ToUnicode CMap
    tounicode = _build_tounicode(glyph_cache)
    objects.append(
        f"<< /Length {len(tounicode)} >>stream\n".encode() + tounicode + b"\nendstream"
    )

    # Append AcroForm + Annot after font stack; rewrite Catalog + page 1 Annots
    widget_id = len(objects) + 1
    annot_id = widget_id + 1
    acro_id = annot_id + 1
    objects[0] = f"<< /Type /Catalog /Pages 2 0 R /AcroForm {acro_id} 0 R >>".encode()
    # Patch first page (object index 2 = id 3) to include Annots
    page0 = objects[2].decode("latin-1")
    if "/Annots" not in page0:
        page0 = page0.replace(
            ">>",
            f" /Annots [ {widget_id} 0 R {annot_id} 0 R ] >>",
            1,
        )
        objects[2] = page0.encode("latin-1")
    objects.extend(
        _pdf_form_annot_objects(
            widget_id=widget_id,
            annot_id=annot_id,
            acro_id=acro_id,
            contents=_annot_contents_from_lines(lines),
        )
    )

    return _assemble(objects)


def _glyph_id(sub: _Subset, glyph_name: str) -> int:
    """GID for a glyph name, using the subset glyph order."""
    order = sub.glyph_order or _subset_glyph_order(sub)
    try:
        return order.index(glyph_name)
    except ValueError:
        return 0


def _subset_glyph_order(sub: _Subset) -> list[str]:
    """Recover the subset font's glyph order (needed for GID lookup)."""
    import io

    from fontTools.ttLib import TTFont

    tmp = TTFont(io.BytesIO(sub.ttf), lazy=True)
    order = list(tmp.getGlyphOrder())
    sub.glyph_order = order
    return order


def _cjk_page_stream(lines: list[str], gid_of) -> bytes:
    """Identity-H content stream: 2-byte big-endian GIDs as hex strings."""
    y = 750
    cmds = ["BT", "/F1 11 Tf", "15 TL"]
    first = True
    for line in lines:
        hexstr = _line_to_hex(line, gid_of)
        if not hexstr:
            hexstr = "0020"  # space keeps the line rhythm
        if first:
            cmds.append(f"50 {y} Td <{hexstr}> Tj")
            first = False
        else:
            cmds.append(f"T* <{hexstr}> Tj")
    cmds.append("ET")
    return "\n".join(cmds).encode("ascii", errors="replace")


def _line_to_hex(line: str, gid_of) -> str:
    out: list[str] = []
    for ch in line:
        if ch in ("\n", "\r", "\t"):
            gid = gid_of(" ") or 32
            out.append(f"{gid:04X}")
            continue
        gid = gid_of(ch)
        if gid is None:
            gid = gid_of("?") or gid_of(" ") or 0
        out.append(f"{gid:04X}")
    return "".join(out)


def _build_tounicode(glyph_cache: dict[str, int]) -> bytes:
    """Minimal ToUnicode CMap so text stays selectable/searchable."""
    entries = sorted({(gid, ord(ch)) for ch, gid in glyph_cache.items() if gid})
    if not entries:
        entries = [(32, 32)]

    chunks: list[str] = []
    for start in range(0, len(entries), 100):
        block = entries[start : start + 100]
        body = "\n".join(f"<{gid:04X}> <{uni:04X}>" for gid, uni in block)
        chunks.append(f"{len(block)} beginbfchar\n{body}\nendbfchar")

    cmap = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\nbegincmap\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "/CIDSystemInfo 3 dict dup begin\n"
        "  /Registry (Adobe) def\n"
        "  /Ordering (UCS) def\n"
        "  /Supplement 0 def\n"
        "end def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        + "\n".join(chunks)
        + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    )
    return cmap.encode("ascii", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# Shared xref assembly
# ─────────────────────────────────────────────────────────────────────────────


def _assemble(objects: list[bytes], *, engine: str = "pdf_simple") -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    out.extend(f"% aduport-pdf-engine: {engine}\n".encode())
    out.extend(b"% aduport-pdf-degraded: true\n")
    offsets = [0]
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out.extend(f"{i} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    out.extend(f"startxref\n{xref}\n%%EOF\n".encode())
    return bytes(out)


def _model_lines(model: DocumentModel) -> list[str]:
    lines: list[str] = []
    footnotes: list[Footnote] = []
    ordered_counters: dict[int, int] = {}
    if model.title:
        lines.extend(_wrap_text(model.title))
        lines.append("")
    for node in model.nodes:
        if lines and lines[-1] != "":
            lines.append("")
        if isinstance(node, Heading):
            lines.extend(_wrap_text("#" * node.level + " " + node.plain_text()))
        elif isinstance(node, Paragraph):
            from aduport.exporters.rich_visible import render_rich_visible

            lines.extend(_wrap_text(render_rich_visible(node.text)))
        elif isinstance(node, ListItem):
            from aduport.exporters.rich_visible import render_rich_visible

            level = max(0, int(node.level or 0))
            if node.checked is True:
                mark = "[x] "
            elif node.checked is False:
                mark = "[ ] "
            elif node.ordered:
                ordered_counters[level] = ordered_counters.get(level, 0) + 1
                for k in list(ordered_counters):
                    if k > level:
                        del ordered_counters[k]
                mark = f"{ordered_counters[level]}. "
            else:
                for k in list(ordered_counters):
                    if k >= level:
                        del ordered_counters[k]
                mark = "- "
            prefix = ("  " * level) + mark
            body = render_rich_visible(node.text)
            wrapped = _wrap_text(body, width=max(20, _WRAP_WIDTH - len(prefix)))
            lines.append(prefix + (wrapped[0] if wrapped else ""))
            for cont in wrapped[1:]:
                lines.append((" " * len(prefix)) + cont)
        elif isinstance(node, CodeBlock):
            lines.append("```" + (node.lang or ""))
            for raw in (node.content or "").splitlines() or [""]:
                lines.extend(_wrap_text(raw, width=_WRAP_WIDTH))
            lines.append("```")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                t = p if isinstance(p, str) else getattr(p, "text", str(p))
                for w in _wrap_text(str(t)):
                    lines.append("> " + w)
        elif isinstance(node, HorizontalRule):
            lines.append("---")
        elif isinstance(node, MathBlock):
            lines.extend(_wrap_text("$$ " + (node.content or "") + " $$"))
        elif isinstance(node, Image):
            alt = node.alt_text.split("|", 1)[0] if node.alt_text else "image"
            if node.data:
                import base64

                mime = node.mime_type or "image/png"
                b64 = base64.b64encode(node.data).decode("ascii")
                lines.append("ADUPORT-IMG")
                lines.append(mime)
                lines.append(alt.replace("\n", " "))
                for offset in range(0, len(b64), 48):
                    lines.append(b64[offset : offset + 48])
                lines.append("ADUPORT-IMG-END")
            else:
                lines.extend(_wrap_text(f"[Image: {alt}]"))
        elif isinstance(node, Chart):
            lines.extend(_wrap_text(node.plain_text()))
        elif isinstance(node, SpeakerNote):
            lines.extend(_wrap_text(f"[SpeakerNote s{node.slide_index}] {node.text}"))
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                lines.extend(_wrap_text(text))
        elif isinstance(node, Footnote):
            footnotes.append(node)
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            lines.extend(_wrap_text(f"[Comment {node.author}{anchor}] {node.text}"))
        elif isinstance(node, Revision):
            if node.change == "delete":
                lines.extend(_wrap_text(f"[-del {node.author}] {node.text}"))
            else:
                lines.extend(_wrap_text(f"[+ins {node.author}] {node.text}"))
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            lines.extend(_wrap_text(f"[Control:{label}] {node.text}"))
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            lines.extend(_wrap_text(f"[TextBox{src}] {node.text}"))
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            lines.extend(_wrap_text(f"[PivotTable {node.name}] {fields}"))
        elif isinstance(node, Table):
            matrix = node.rows or []
            if not matrix and getattr(node, "cells", None):
                matrix = [[getattr(c, "text", str(c)) for c in crow] for crow in node.cells]
            if matrix:
                width = max(len(row) for row in matrix)

                def _cell(row: list, idx: int) -> str:
                    return str(row[idx]) if idx < len(row) else ""

                lines.extend(_wrap_text(" | ".join(_cell(matrix[0], i) for i in range(width))))
                lines.append(" | ".join("---" for _ in range(width)))
                for row in matrix[1:]:
                    lines.extend(_wrap_text(" | ".join(_cell(row, i) for i in range(width))))
            elif getattr(node, "cells", None):
                from aduport.exporters.export_warnings import warn_export

                warn_export(model, "pdf_simple: Table.cells flattened without span layout")
                for crow in node.cells:
                    texts = [getattr(c, "text", str(c)) for c in crow]
                    lines.extend(_wrap_text(" | ".join(texts)))
            else:
                from aduport.exporters.export_warnings import placeholder_line, warn_export

                warn_export(model, "pdf_simple: empty Table → placeholder")
                lines.append(placeholder_line("Table", "(empty)"))
        else:
            plain = getattr(node, "plain_text", None)
            if callable(plain):
                text = plain()
                if text:
                    lines.extend(_wrap_text(text))
                else:
                    from aduport.exporters.export_warnings import (
                        node_type_name,
                        placeholder_line,
                        warn_export,
                    )

                    kind = node_type_name(node)
                    warn_export(model, f"pdf_simple: empty {kind} → placeholder")
                    lines.append(placeholder_line(kind))
            else:
                from aduport.exporters.export_warnings import (
                    node_type_name,
                    placeholder_line,
                    warn_export,
                )

                kind = node_type_name(node)
                warn_export(model, f"pdf_simple: unhandled {kind} → placeholder")
                lines.append(placeholder_line(kind))
    if footnotes:
        lines.append("")
        if lines and lines[-1] != "":
            lines.append("")
        for fn in footnotes:
            lines.extend(_wrap_text(f"[^{fn.id}]: {fn.text}"))
    return lines
