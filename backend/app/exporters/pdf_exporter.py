"""Export DocumentModel to PDF via HTML + WeasyPrint."""

from __future__ import annotations

import html

from app.config import settings
from app.core.css_sanitize import sanitize_custom_css, sanitize_font_family
from app.core.document_model import DocumentModel
from app.core.exceptions import ExportError
from app.exporters.html_render import build_toc_html, render_body_html

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
  font-weight: 700;
  line-height: 1.35;
}}
{bookmark_css}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; text-indent: 0; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
th {{ background: #f5f5f5; }}
pre {{ background: #f6f8fa; padding: 12px; overflow-x: auto; border-radius: 4px; text-indent: 0; }}
code {{ font-family: 'Courier New', Consolas, 'Sarasa Mono SC', monospace; font-size: 0.9em; }}
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
p.footnote {{ font-size: 0.85em; color: #555; text-indent: 0; }}
nav.toc {{ page-break-after: always; text-indent: 0; }}
nav.toc ol {{ list-style: none; padding-left: 0; }}
nav.toc .toc-l2 {{ padding-left: 1rem; }}
nav.toc .toc-l3 {{ padding-left: 2rem; }}
@page {{
  size: {page_size};
  margin: 2cm;
  {header_footer_css}
}}
"""


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
) -> bytes:
    try:
        from weasyprint import HTML
    except (OSError, ImportError):
        from app.exporters.pdf_simple import export_pdf_simple

        return export_pdf_simple(model)

    safe_font = sanitize_font_family(font_family, settings.pdf_font_family)
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
        return HTML(string=html_content, url_fetcher=_blocked_fetcher).write_pdf()
    except OSError:
        from app.exporters.pdf_simple import export_pdf_simple

        return export_pdf_simple(model)
    except Exception as exc:
        # Shared-library load failures often surface as generic Exception
        msg = str(exc).lower()
        if "pango" in msg or "gobject" in msg or "cairo" in msg or "cannot load" in msg:
            from app.exporters.pdf_simple import export_pdf_simple

            return export_pdf_simple(model)
        raise ExportError(f"PDF 渲染失败: {exc}") from exc


def _blocked_fetcher(url: str, timeout: int = 10, ssl_context=None):
    if url.startswith("data:") or url.startswith("about:"):
        from weasyprint import default_url_fetcher

        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)
    raise ExportError(f"拒绝加载远程资源（SSRF 防护）: {url[:120]}")


def _normalize_page_size(page_size: str | None, orientation: str | None) -> str:
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
    base_css = DEFAULT_CSS_TEMPLATE.format(
        font_family=html.escape(font_family, quote=True),
        text_indent="2em" if settings.pdf_cjk_typeset else "0",
        page_size=_normalize_page_size(page_size, orientation),
        header_footer_css=_header_footer_css(
            header=header, footer=footer, page_numbers=page_numbers, title=model.title or ""
        ),
        bookmark_css=_bookmark_css(True),
    )
    extra = custom_css.strip() if custom_css else ""
    style_block = base_css + ("\n" + extra if extra else "")

    toc_html = build_toc_html(model) if toc_on else ""
    body = render_body_html(model)

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
