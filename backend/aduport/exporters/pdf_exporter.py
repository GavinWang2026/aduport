"""Export DocumentModel to PDF via HTML + WeasyPrint."""

from __future__ import annotations

import html

from aduport.config import settings
from aduport.core.css_sanitize import (
    compact_font_family,
    prefer_subsettable_cjk,
    sanitize_custom_css,
    sanitize_font_family,
)
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import ExportError, InvalidDocumentError
from aduport.exporters.html_render import build_toc_html, render_body_html

DEFAULT_CSS_TEMPLATE = """
body {{
  font-family: {font_family};
  line-height: 1.75;
  margin: 0;
  color: #222;
  text-align: justify;
  word-break: break-word;
}}
p {{
  margin: 0.6em 0;
  text-indent: {text_indent};
}}
h1,h2,h3,h4,h5,h6 {{
  margin-top: 1.2em;
  margin-bottom: 0.4em;
  text-indent: 0;
  text-align: left;
  /* Stay on the regular face when possible — a 700 pull often embeds a
     second CJK bold file (PingFang SemiBold / Hiragino) for a short doc. */
  font-weight: 600;
  line-height: 1.35;
}}
h1 {{ font-size: 22pt; }}
h2 {{ font-size: 16pt; }}
h3 {{ font-size: 13pt; }}
h4 {{ font-size: 12pt; }}
h5 {{ font-size: 11pt; }}
h6 {{ font-size: 10.5pt; }}
{bookmark_css}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; text-indent: 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
th {{ background: #f5f5f5; }}
pre {{ background: #f6f8fa; padding: 12px; overflow-x: auto; border-radius: 4px; text-indent: 0; }}
code {{ font-family: {code_font_family}; font-size: 0.9em; }}
img {{ max-width: 100%; height: auto; }}
ul, ol {{ margin: 0.5em 0; padding-left: 1.4em; text-indent: 0; }}
li {{ margin: 0.25em 0; text-align: left; }}
blockquote {{
  margin: 0.8em 0;
  padding: 0.4em 0 0.4em 1em;
  border-left: 3px solid #ccc;
  color: #444;
  text-indent: 0;
}}
hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }}
pre.math {{ background: #fafafa; font-style: italic; }}
.footnotes {{ font-size: 0.85em; color: #555; margin-top: 2em; border-top: 1px solid #ddd; padding-top: 0.8em; }}
.footnotes li {{ margin: 0.35em 0; text-indent: 0; }}
p.footnote {{ font-size: 0.85em; color: #555; text-indent: 0; }}
ins.revision {{ background: #e1f5ee; }}
del.revision {{ color: #a32d2d; text-decoration: line-through; }}
aside.comment {{ border-left: 3px solid #9ab; padding-left: 0.8em; }}
aside.comment {{
  border-left: 3px solid #9ab;
  font-size: 0.9em;
  margin: 0.8em 0;
  padding: 0.4em 0.8em;
  background: #f8f9fc;
  color: #333;
  text-indent: 0;
}}
.comment-author {{ font-weight: 600; }}
.comment-anchor {{ color: #666; }}
aside.content-control {{ border: 1px dashed #185FA5; padding: 0.25em 0.5em; margin: 0.4em 0; background: #f0f4ff; }}
aside.text-box {{ border-left: 3px solid #6b8; padding-left: 0.6em; margin: 0.5em 0; font-style: italic; }}
aside.section-props {{ font-size: 0.85em; color: #666; margin: 0.6em 0; }}
aside.pivot-table {{ border-left: 3px solid #185FA5; padding: 0.5em 1em; margin: 0.8em 0; background: #fafafa; }}
.pdf-layout-preview {{ margin: 1em 0; }}
.pdf-page {{ position: relative; border: 1px solid #ddd; margin: 0.8em 0; background: #fff; }}
.pdf-block {{ position: absolute; font-size: 11px; line-height: 1.25; white-space: pre-wrap; }}
nav.toc {{ page-break-after: always; text-indent: 0; }}
nav.toc ol {{ list-style: none; padding-left: 0; }}
nav.toc .toc-l2 {{ padding-left: 1rem; }}
nav.toc .toc-l3 {{ padding-left: 2rem; }}
@page {{
  size: {page_size};
  margin: {page_margin};
  {header_footer_css}
}}
"""


# Export engines. ``settings.pdf_engine`` is the PDF *import* engine
# (auto/text/docling/ocr/...) and is intentionally not read here.
_EXPORT_PDF_ENGINES = frozenset({"", "auto", "weasyprint", "simple", "pdf_simple"})


def export_pdf(
    model: DocumentModel,
    *,
    font_family: str | None = None,
    custom_css: str | None = None,
    page_size: str | None = None,
    orientation: str | None = None,
    header: str | None = None,
    footer: str | None = None,
    page_numbers: bool | None = None,
    toc: bool | None = None,
    engine: str | None = None,
) -> bytes:
    requested = (engine or "").strip().lower()
    if requested not in _EXPORT_PDF_ENGINES:
        raise InvalidDocumentError(f"pdf_engine 取值无效: {requested}")
    if requested in {"simple", "pdf_simple"}:
        return _finish_pdf(_export_pdf_simple(model, reason="requested_simple"), model)
    try:
        from weasyprint import HTML
    except (OSError, ImportError):
        return _finish_pdf(_export_pdf_simple(model, reason="weasyprint_unavailable"), model)

    safe_font = sanitize_font_family(font_family, settings.pdf_font_family)
    if getattr(settings, "pdf_font_compact", True):
        safe_font = compact_font_family(safe_font)
        # PingFang / Noto Sans CJK embed ~150KB whole; Songti/STSong subset.
        safe_font = prefer_subsettable_cjk(safe_font)
    raw_css = custom_css if custom_css is not None else settings.pdf_custom_css
    safe_css = sanitize_custom_css(raw_css)

    html_content = _render_html(
        model,
        font_family=safe_font,
        custom_css=safe_css,
        page_size=page_size,
        orientation=orientation,
        header=header,
        footer=footer,
        page_numbers=page_numbers,
        toc=toc,
    )
    try:
        _fetch_notes.clear()
        pdf = HTML(string=html_content, url_fetcher=_blocked_fetcher).write_pdf()
        meta = model.metadata if isinstance(model.metadata, dict) else {}
        meta["pdf_engine"] = "weasyprint"
        if _fetch_notes:
            from aduport.exporters.export_warnings import warn_export

            warn_export(model, "degraded: PDF remote resources were replaced")
            for note in _fetch_notes:
                warn_export(model, note)
            meta["fidelity_degraded"] = True
            meta["pdf_engine_reason"] = "remote_resource_blocked"
        model.metadata = meta
        return _finish_pdf(pdf, model)
    except OSError:
        return _finish_pdf(_export_pdf_simple(model, reason="weasyprint_oserror"), model)
    except ExportError:
        raise
    except Exception as exc:
        # WeasyPrint 70 dropped default_url_fetcher; a fetcher failure used to
        # surface as AttributeError and become a 500. Degrade instead.
        return _finish_pdf(
            _export_pdf_simple(
                model,
                reason="weasyprint_render_failed",
                detail=type(exc).__name__,
            ),
            model,
        )


def _finish_pdf(pdf: bytes, model: DocumentModel) -> bytes:
    """Attach cue markdown so a later import can fold structure.

    The cues used to be a second visible page. An embedded file keeps the
    same text out of the page the user prints. If attaching fails, the visual
    PDF is still returned.
    """
    from aduport.exporters.html_render import PDF_CUE_ATTACHMENT, pdf_cue_markdown

    payload = pdf_cue_markdown(model.nodes)
    if not payload:
        return pdf
    try:
        from io import BytesIO

        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(BytesIO(pdf))
        writer = PdfWriter(clone_from=reader)
        writer.add_attachment(PDF_CUE_ATTACHMENT, payload.encode("utf-8"))
        out = BytesIO()
        writer.write(out)
    except Exception:
        return pdf
    meta = model.metadata if isinstance(model.metadata, dict) else {}
    meta["pdf_cues"] = "attachment"
    model.metadata = meta
    return out.getvalue()


def _export_pdf_simple(
    model: DocumentModel,
    *,
    reason: str,
    detail: str = "",
) -> bytes:
    from aduport.exporters.export_warnings import warn_export
    from aduport.exporters.pdf_simple import export_pdf_simple

    extra = f"；{detail}" if detail else ""
    if reason == "requested_simple":
        message = "PDF 已降级为 pdf_simple（请求指定 simple）：图片/复杂表格/分页能力有限"
    elif reason == "weasyprint_unavailable":
        message = (
            "PDF 已降级为 pdf_simple（WeasyPrint 不可用）：图片/复杂表格/分页能力有限；"
            "请安装 WeasyPrint + Pango 以获得高保真 PDF"
        )
    elif reason == "weasyprint_oserror":
        message = "PDF 已降级为 pdf_simple（WeasyPrint OSError）：图片/复杂表格/分页能力有限"
    else:
        message = (
            "PDF 已降级为 pdf_simple（WeasyPrint 渲染失败）：图片/复杂表格/分页能力有限"
            + extra
        )
    warn_export(model, message)
    out = export_pdf_simple(model)
    meta = model.metadata if isinstance(model.metadata, dict) else {}
    meta["pdf_engine"] = "pdf_simple"
    meta["pdf_engine_reason"] = reason
    meta["fidelity_degraded"] = True
    model.metadata = meta
    return out


# 1×1 PNG so a blocked remote image does not abort the whole render.
_BLANK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
_fetch_notes: list[str] = []


def _fetch_response(url: str, body: bytes, mime: str):
    """WeasyPrint 70 rejects a bare dict from a custom fetcher."""
    from weasyprint.urls import URLFetcherResponse

    return URLFetcherResponse(
        url,
        body=body or b"",
        headers={"Content-Type": mime or "application/octet-stream"},
    )


def _blocked_fetcher(url: str, timeout: int = 10, ssl_context=None):
    del timeout, ssl_context
    if url.startswith("data:"):
        decoded = _decode_data_url(url)
        return _fetch_response(url, decoded["string"], str(decoded["mime_type"]))
    if url.startswith("about:"):
        return _fetch_response(url, b"", "text/plain")
    note = f"PDF 跳过远程资源（SSRF 防护）: {url[:120]}"
    if note not in _fetch_notes:
        _fetch_notes.append(note)
    return _fetch_response(url, _BLANK_PNG, "image/png")


def _decode_data_url(url: str) -> dict[str, object]:
    import base64
    import urllib.parse

    head, _, payload = url[5:].partition(",")
    mime = (head.split(";", 1)[0] or "application/octet-stream").strip()
    try:
        raw = (
            base64.b64decode(payload)
            if "base64" in head
            else urllib.parse.unquote_to_bytes(payload)
        )
    except Exception:
        raw = b""
    return {"string": raw, "mime_type": mime or "application/octet-stream"}


def _normalize_page_size(
    page_size: str | None, orientation: str | None, model: DocumentModel | None = None
) -> str:
    if model and page_size is None:
        props = (model.metadata or {}).get("section_props") or {}
        w = props.get("page_width_mm")
        h = props.get("page_height_mm")
        if w and h:
            try:
                wf, hf = float(w), float(h)
                if wf > 0 and hf > 0:
                    orient = (orientation or settings.pdf_orientation or "portrait").strip().lower()
                    if orient == "landscape" and hf > wf:
                        wf, hf = hf, wf
                    return f"{wf:.0f}mm {hf:.0f}mm"
            except (TypeError, ValueError):
                pass
    size = (page_size or settings.pdf_page_size or "A4").strip()
    # allow only known tokens
    allowed = {"A4", "A3", "A5", "Letter", "Legal"}
    token = size.split()[0]
    if token not in allowed:
        token = "A4"
    orient = (orientation or settings.pdf_orientation or "portrait").strip().lower()
    if orient == "landscape":
        return f"{token} landscape"
    return token


def _page_margin_css(model: DocumentModel) -> str:
    props = (model.metadata or {}).get("section_props") or {}
    margins = props.get("margins_mm") or {}
    if not isinstance(margins, dict) or not margins:
        return "2cm"
    order = ("top", "right", "bottom", "left")
    vals: list[str] = []
    for side in order:
        raw = margins.get(side)
        if raw is None:
            vals.append("2cm")
        else:
            try:
                vals.append(f"{float(raw):.1f}mm")
            except (TypeError, ValueError):
                vals.append("2cm")
    return " ".join(vals)


def _header_footer_css(
    *,
    header: str | None,
    footer: str | None,
    page_numbers: bool | None,
    title: str = "",
) -> str:
    hdr = header if header is not None else settings.pdf_header
    ftr = footer if footer is not None else settings.pdf_footer
    nums = settings.pdf_page_numbers if page_numbers is None else page_numbers
    hdr = _expand_placeholders(hdr or "", title=title)
    ftr = _expand_placeholders(ftr or "", title=title)
    parts: list[str] = []
    if hdr:
        # {page} in header → CSS counter
        if "{page}" in (header if header is not None else settings.pdf_header or ""):
            raw = header if header is not None else settings.pdf_header
            raw = _expand_placeholders(raw or "", title=title, keep_page=True)
            css_content = _css_content_with_page(raw)
            parts.append(f"@top-center {{ content: {css_content}; font-size: 9pt; color: #666; }}")
        else:
            safe = html.escape(hdr, quote=True)
            parts.append(f'@top-center {{ content: "{safe}"; font-size: 9pt; color: #666; }}')
    if ftr or nums:
        raw_ftr = footer if footer is not None else settings.pdf_footer
        if raw_ftr and "{page}" in raw_ftr:
            expanded = _expand_placeholders(raw_ftr, title=title, keep_page=True)
            css_content = _css_content_with_page(expanded)
            parts.append(f"@bottom-center {{ content: {css_content}; font-size: 9pt; color: #666; }}")
        elif nums and ftr:
            safe = html.escape(ftr, quote=True)
            content = f'"{safe} · " counter(page)'
            parts.append(f"@bottom-center {{ content: {content}; font-size: 9pt; color: #666; }}")
        elif nums:
            parts.append(
                "@bottom-center { content: counter(page); font-size: 9pt; color: #666; }"
            )
        else:
            safe = html.escape(ftr or "", quote=True)
            parts.append(f'@bottom-center {{ content: "{safe}"; font-size: 9pt; color: #666; }}')
    return "\n  ".join(parts)


def _expand_placeholders(text: str, *, title: str = "", keep_page: bool = False) -> str:
    from datetime import date

    out = text.replace("{title}", title or "")
    out = out.replace("{date}", date.today().isoformat())
    if not keep_page:
        out = out.replace("{page}", "")  # page handled in CSS when keep_page
    return out


def _css_content_with_page(template: str) -> str:
    """Turn 'Foo {page} Bar' into '"Foo " counter(page) " Bar"'."""
    parts = template.split("{page}")
    chunks: list[str] = []
    for i, part in enumerate(parts):
        if part:
            chunks.append(f'"{html.escape(part, quote=True)}"')
        if i < len(parts) - 1:
            chunks.append("counter(page)")
    return " ".join(chunks) if chunks else '""'


def _css_escape_font_stack(value: str) -> str:
    """Escape ``<``/``&`` for a ``<style>`` block without turning quotes into entities."""
    return html.escape(value or "", quote=False)


def _bookmark_css(enabled: bool) -> str:
    if not enabled:
        return ""
    return """
h1 { bookmark-level: 1; bookmark-label: content(); }
h2 { bookmark-level: 2; bookmark-label: content(); }
h3 { bookmark-level: 3; bookmark-label: content(); }
"""


def _render_html(
    model: DocumentModel,
    *,
    font_family: str,
    custom_css: str,
    page_size: str | None = None,
    orientation: str | None = None,
    header: str | None = None,
    footer: str | None = None,
    page_numbers: bool | None = None,
    toc: bool | None = None,
) -> str:
    toc_on = settings.pdf_toc if toc is None else toc
    # Courier New has no CJK; Songti SC is the subsettable fallback. Do not
    # html.escape(..., quote=True) — &#x27; in <style> makes WeasyPrint ignore
    # the stack and fall back to Nanum/Hiragino (~30–100KB per code block).
    code_font = "Courier New, Songti SC, monospace"
    if getattr(settings, "pdf_font_compact", True):
        code_font = prefer_subsettable_cjk(code_font)
    base_css = DEFAULT_CSS_TEMPLATE.format(
        font_family=_css_escape_font_stack(font_family),
        code_font_family=_css_escape_font_stack(code_font),
        text_indent="2em" if settings.pdf_cjk_typeset else "0",
        page_size=_normalize_page_size(page_size, orientation, model),
        page_margin=_page_margin_css(model),
        header_footer_css=_header_footer_css(
            header=header, footer=footer, page_numbers=page_numbers, title=model.title or ""
        ),
        bookmark_css=_bookmark_css(True),
    )
    extra = custom_css.strip() if custom_css else ""
    style_block = base_css + ("\n" + extra if extra else "")

    toc_html = build_toc_html(model) if toc_on else ""
    # Paint markdown cues into the text layer so a later PDF→MD pass can
    # recover inline code, quotes, and fences (WeasyPrint drops the tags).
    body = render_body_html(model, markdown_cues=True)

    return "\n".join(
        [
            "<!DOCTYPE html>",
            "<html><head><meta charset='utf-8'>",
            "<style>",
            style_block,
            "</style></head><body>",
            toc_html,
            body,
            "</body></html>",
        ]
    )
