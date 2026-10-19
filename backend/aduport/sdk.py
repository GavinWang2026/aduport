"""SDK runtime capability probe — the foundation of tiered licensing.

Tiering model (open-source / commercial boundary)
-------------------------------------------------
- **Pure Python (MIT, free)**: ``convert`` is fully functional, but PDF text
  layer and DOCX parsing use the in-tree Python implementations — slower and
  lower fidelity.
- **native acceleration (commercial)**: installing the prebuilt
  ``aduport-native`` wheel makes ``native=True``; the Rust anydoc engine then
  owns docx/xls/odt/rtf/… with materially better speed and fidelity.
- **Enterprise (per-machine License)**: OCR (mineru/paddle), embedding
  (fastembed), and docling high-fidelity parsing are additive capabilities.

Callers use :func:`capabilities` to detect the current tier and degrade or gate
features accordingly.
"""

from __future__ import annotations

from importlib.util import find_spec
from typing import Any


def _module_available(name: str) -> bool:
    """Return True when ``name`` can be imported — without actually importing it.

    ``find_spec`` avoids loading heavyweight optional deps (mineru / paddleocr /
    fastembed / docling) merely to report a capability.
    """
    try:
        return find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _pdf_engine_status() -> dict[str, Any]:
    """Report the PDF exporter that convert() will actually use."""
    try:
        import weasyprint

        return {
            "weasyprint": True,
            "weasyprint_version": str(getattr(weasyprint, "__version__", "") or ""),
            "pdf_engine": "weasyprint",
            "pdf_degraded": False,
            "pdf_engine_reason": "",
        }
    except Exception as exc:  # OSError when Pango/GObject is missing
        return {
            "weasyprint": False,
            "weasyprint_version": "",
            "pdf_engine": "pdf_simple",
            "pdf_degraded": True,
            "pdf_engine_reason": f"{type(exc).__name__}: {exc}",
        }


def capabilities() -> dict[str, Any]:
    """Return the installed runtime capability profile.

    All values are JSON-serializable. Keys:

    - ``version`` (str): AduPort version.
    - ``native`` (bool): compiled Rust anydoc engine present.
    - ``native_version`` (str | None): installed ``aduport-native`` version.
    - ``ocr_mineru`` / ``ocr_paddle`` (bool): OCR backends present.
    - ``embed`` (bool): fastembed vector backend present.
    - ``office_docling`` (bool): docling high-fidelity office parser present.
    - ``images_pillow`` (bool): Pillow image backend present.
    - ``pdf_engine`` / ``pdf_degraded`` / ``weasyprint``: which PDF writer is active.
    """
    from aduport import __version__

    caps: dict[str, Any] = {
        "version": __version__,
        "native": False,
        "native_version": None,
        "ocr_mineru": _module_available("mineru"),
        "ocr_paddle": _module_available("paddleocr"),
        "embed": _module_available("fastembed"),
        "office_docling": _module_available("docling"),
        "images_pillow": _module_available("PIL.Image"),
    }
    pdf = _pdf_engine_status()
    caps.update(pdf)

    # native engine has a dedicated availability check (handles AnydocUnavailable).
    try:
        from aduport.importers.anydoc_bridge import anydoc_available, anydoc_version

        caps["native"] = anydoc_available()
        caps["native_version"] = anydoc_version()
    except Exception:  # defensive: capability probing must never raise
        caps["native"] = False
        caps["native_version"] = None

    return caps


def tier() -> str:
    """Return the effective licensing tier: ``"enterprise"`` / ``"native"`` / ``"free"``.

    Heuristic only — not an authorization boundary. Real enforcement belongs to
    the license module (per-machine HMAC License), not to this probe.
    """
    caps = capabilities()
    if caps["native"] and (caps["ocr_mineru"] or caps["ocr_paddle"] or caps["embed"]):
        return "enterprise"
    if caps["native"]:
        return "native"
    return "free"
