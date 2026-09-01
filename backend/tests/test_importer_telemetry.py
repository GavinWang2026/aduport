"""Importer hit telemetry: audit + metrics expose anydoc vs native path."""

from __future__ import annotations

from app.core.audit import audit_event
from app.core.metrics import metrics


def test_metrics_track_importer_bucket():
    before = metrics.conversions_by_importer.get("anydoc_fast_md", 0)
    metrics.record_success("docx", "md", 0.01, importer="anydoc_fast_md")
    snap = metrics.snapshot()
    assert snap["conversions_by_importer"].get("anydoc_fast_md", 0) == before + 1
    text = metrics.prometheus_text()
    assert 'mdbridge_conversions_by_importer{importer="anydoc_fast_md"}' in text


def test_audit_event_accepts_importer(tmp_path, monkeypatch):
    from app.config import settings
    from app.core import audit as audit_mod

    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "audit_enabled", True)
    monkeypatch.setattr(settings, "audit_log_path", str(log))
    audit_mod._logger.handlers.clear()
    audit_event(
        action="convert",
        source_format="xlsb",
        target_format="md",
        filename="a.xlsb",
        success=True,
        importer="anydoc_fast_md",
        bytes_in=10,
        bytes_out=20,
    )
    line = log.read_text(encoding="utf-8").strip().splitlines()[-1]
    assert '"importer": "anydoc_fast_md"' in line


def test_registry_marks_native_fallback(monkeypatch):
    from app.core.document_model import DocumentModel, Paragraph, RichText
    from app.core.registry import _anydoc_first
    from app.importers import anydoc_bridge as bridge

    monkeypatch.setattr(bridge, "should_handle", lambda fmt: True)

    def boom(*_a, **_k):
        raise bridge.AnydocFallback("decline", code="unsupported")

    monkeypatch.setattr(bridge, "import_via_anydoc", boom)

    def native_imp(data: bytes, filename: str = "") -> DocumentModel:
        return DocumentModel(nodes=[Paragraph(text=RichText(text="n"))])

    wrapped = _anydoc_first("docx", native_imp)
    model = wrapped(b"x", "a.docx")
    assert model.metadata.get("importer") == "native_fallback"
