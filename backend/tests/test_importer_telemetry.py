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


def test_metrics_failure_by_code_and_ocr_gauges():
    before = metrics.conversions_failed_by_code.get("ocr_unavailable", 0)
    metrics.record_failure("pdf", "md", code="ocr_unavailable")
    snap = metrics.snapshot()
    assert snap["conversions_failed_by_code"].get("ocr_unavailable", 0) == before + 1
    text = metrics.prometheus_text()
    assert "mdbridge_conversions_failed_by_code{code=\"ocr_unavailable\"}" in text
    assert "mdbridge_ocr_enabled" in text
    assert "mdbridge_ocr_ready" in text


def test_metrics_anydoc_share_and_channels():
    before_hits = metrics.anydoc_hits
    before_deg = metrics.anydoc_degrades
    metrics.record_anydoc_hit(channel="fast_md")
    metrics.record_anydoc_hit(channel="model")
    metrics.record_anydoc_degrade()
    metrics.record_success("docx", "md", 0.01, importer="anydoc_fast_md")
    metrics.record_success("pdf", "md", 0.02, importer="native")
    snap = metrics.snapshot()
    assert snap["anydoc_hits"] >= before_hits + 2
    assert snap["anydoc_degrades"] >= before_deg + 1
    assert snap["anydoc_hits_by_channel"].get("fast_md", 0) >= 1
    assert snap["anydoc_hits_by_channel"].get("model", 0) >= 1
    assert snap["anydoc_hit_rate"] is not None
    assert 0.0 <= snap["anydoc_hit_rate"] <= 1.0
    assert snap["anydoc_channel_share"] is not None
    text = metrics.prometheus_text()
    assert "mdbridge_anydoc_hit_rate" in text
    assert "mdbridge_anydoc_channel_share" in text
    assert 'mdbridge_anydoc_hits_by_channel{channel="fast_md"}' in text


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


def test_anydoc_fallback_and_sniff_settings_exist():
    from app.config import settings

    assert hasattr(settings, "anydoc_fallback")
    assert hasattr(settings, "anydoc_sniff")
    assert isinstance(settings.anydoc_fallback, bool)
    assert isinstance(settings.anydoc_sniff, bool)


def test_format_detect_respects_anydoc_sniff_off(monkeypatch):
    from app.config import settings
    from app.core import format_detect as fd

    monkeypatch.setattr(settings, "anydoc_sniff", False)
    monkeypatch.setattr(settings, "anydoc_enabled", True)
    called = {"n": 0}

    def boom(_data: bytes):
        called["n"] += 1
        return "ppt"

    monkeypatch.setattr(
        "app.importers.anydoc_bridge.sniff_format", boom, raising=False
    )
    # No extension + unknown light magic → would consult anydoc when sniff on
    assert fd.detect_source_format("blob.bin", b"\x00\x01\x02\x03") is None
    assert called["n"] == 0
