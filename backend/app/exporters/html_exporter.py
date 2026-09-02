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
.pdf-layout-preview {{ max-width: 100%; margin: 1.5rem 0; }}
.pdf-layout-preview .pdf-page {{ position: relative; border: 1px solid #ddd; margin: 1rem 0; background: #fff; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
.pdf-layout-preview .pdf-block {{ white-space: pre-wrap; }}
.content-control {{ background: #f0f4ff; padding: 2px 6px; border-radius: 4px; }}
.text-box {{ background: #f8f9fc; border-left: 3px solid #6b8; padding: 0.5em 1em; margin: 0.8em 0; }}
.section-props {{ font-size: 0.9em; color: #555; margin: 0.8em 0; }}
"""


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
    if (model.metadata or {}).get("pdf_layout", {}).get("blocks"):
        style += "body { max-width: 56rem; }\n"
    toc = build_toc_html(model) if toc_on else ""
    body = render_body_html(model)
    katex_head = ""
    if settings.html_katex:
        katex_head = (
            "<link rel='stylesheet' href='https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css'>"
            "<script defer src='https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js'></script>"
            "<script defer src='https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js'></script>"
        )
    return (
        "<!DOCTYPE html>\n<html><head><meta charset='utf-8'>"
        f"<title>{_esc_title(model)}</title>{katex_head}<style>{style}</style></head><body>\n"
        f"{toc}\n{body}\n</body></html>\n"
    )


def _esc_title(model: DocumentModel) -> str:
    import html as h

    return h.escape(model.title or "document")
