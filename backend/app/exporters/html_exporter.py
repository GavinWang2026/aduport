"""Export DocumentModel to standalone HTML."""

from __future__ import annotations

from app.config import settings
from app.core.css_sanitize import sanitize_custom_css, sanitize_font_family
from app.core.document_model import DocumentModel
from app.exporters.html_render import build_toc_html, render_body_html

_BASE_CSS = """
body {{ font-family: {font_family}; line-height: 1.7; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; color: #222; }}
h1,h2,h3,h4,h5,h6 {{ line-height: 1.35; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ccc; padding: 6px 10px; }}
pre {{ background: #f6f8fa; padding: 12px; overflow-x: auto; }}
blockquote {{ border-left: 3px solid #ccc; padding-left: 1em; color: #444; }}
nav.toc {{ background: #fafafa; padding: 1rem; margin-bottom: 2rem; }}
nav.toc ol {{ list-style: none; padding-left: 0; }}
nav.toc .toc-l2 {{ padding-left: 1rem; }}
nav.toc .toc-l3 {{ padding-left: 2rem; }}
img {{ max-width: 100%; height: auto; }}
.math-display {{ margin: 1em 0; overflow-x: auto; }}
.footnote {{ font-size: 0.92em; color: #444; border-top: 1px solid #eee; padding-top: 0.5em; }}
"""

_KATEX_HEAD = (
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css"/>'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>'
    '<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js" '
    "onload=\"renderMathInElement(document.body,{delimiters:["
    "{left:'\\\\[',right:'\\\\]',display:true},"
    "{left:'\\\\(',right:'\\\\)',display:false}"
    ']})"></script>'
)


def export_html(
    model: DocumentModel,
    *,
    font_family: str | None = None,
    custom_css: str | None = None,
    include_toc: bool | None = None,
) -> str:
    safe_font = sanitize_font_family(font_family, settings.pdf_font_family)
    safe_css = sanitize_custom_css(custom_css if custom_css is not None else "")
    toc_on = settings.pdf_toc if include_toc is None else include_toc
    style = _BASE_CSS.format(font_family=safe_font) + ("\n" + safe_css if safe_css else "")
    toc = build_toc_html(model) if toc_on else ""
    body = render_body_html(model)
    katex = _KATEX_HEAD if settings.html_katex else ""
    return (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
        f"<title>{_esc_title(model)}</title><style>{style}</style>{katex}</head><body>\n"
        f"{toc}\n{body}\n</body></html>\n"
    )


def _esc_title(model: DocumentModel) -> str:
    import html as h

    return h.escape(model.title or "document")
