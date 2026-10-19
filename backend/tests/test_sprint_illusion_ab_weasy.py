"""Sprint: illusion defaults off + A/B roundtrip matrix + Weasy calibration gate.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.document_model import DocumentModel, Heading, Paragraph, Revision, Table
from aduport.core.metrics import metrics
from aduport.exporters.pdf_exporter import export_pdf
from aduport.exporters.pdf_simple import export_pdf_simple, extract_ast_sidecar
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx


def test_defaults_illusion_flags_off():
    assert settings.pdf_ast_sidecar is False
    assert settings.xlsx_ast_sheet is False


def test_pdf_sidecar_off_by_default():
    model = DocumentModel(nodes=[Paragraph(text="NO_SIDECAR_BODY")])
    pdf = export_pdf_simple(model)
    assert b"ADUPORT-AST-v1" not in pdf
    assert extract_ast_sidecar(pdf) is None


def test_pdf_sidecar_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "pdf_ast_sidecar", True)
    model = DocumentModel(nodes=[Heading(level=1, text="OPT_IN"), Paragraph(text="body")])
    pdf = export_pdf_simple(model)
    assert b"ADUPORT-AST-v1" in pdf
    back = extract_ast_sidecar(pdf)
    assert back is not None
    text = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    assert "OPT_IN" in text


def test_xlsx_ast_sheet_off_by_default():
    model = DocumentModel(
        nodes=[
            Table(rows=[["a", "b"]], name="Data"),
            Revision(change="insert", author="alice", text="rev"),
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        wb = zf.read("xl/workbook.xml").decode()
    assert "_AST" not in wb
    back = import_xlsx(raw, "out.xlsx")
    assert not any(isinstance(n, Revision) for n in back.nodes)


def test_xlsx_ast_sheet_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "xlsx_ast_sheet", True)
    model = DocumentModel(
        nodes=[
            Table(rows=[["a"]], name="Data"),
            Revision(change="insert", author="bob", text="keep"),
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        assert "_AST" in zf.read("xl/workbook.xml").decode()
    back = import_xlsx(raw, "out.xlsx")
    revs = [n for n in back.nodes if isinstance(n, Revision)]
    assert len(revs) == 1 and revs[0].author == "bob"


def test_identity_excluded_from_conversions_total():
    before_total = sum(metrics.conversions_total.values())
    before_id = dict(metrics.identity_passthrough)
    data = b"# identity\n"
    assert convert(data, "md", "md", skip_magic=True) == data
    assert sum(metrics.conversions_total.values()) == before_total
    assert metrics.identity_passthrough.get("md", 0) >= before_id.get("md", 0) + 1
    snap = metrics.snapshot()
    assert "identity_passthrough" in snap
    prom = metrics.prometheus_text()
    assert "aduport_identity_passthrough" in prom


@pytest.mark.parametrize(
    "target",
    ["html", "json", "txt", "docx", "odt", "ods", "odp", "rtf", "latex", "csv"],
)
def test_ab_roundtrip_md_via_target(ab_benchmark, target: str):
    """Real md → target → md; exclude identity; no PDF/xlsx sidecar crutches."""
    assert settings.cache_enabled is False
    assert target != "md"
    sentinel = "ROUNDTRIP_SENTINEL_αβγ"
    payload = f"# Roundtrip\n\n{sentinel}\n\n**bold** and *italic*\n".encode()
    mid = convert(payload, "md", target, filename="rt.md", skip_magic=True)
    assert mid and mid != payload
    back = convert(mid, target, "md", filename=f"rt.{target}", skip_magic=True)
    text = back.decode("utf-8", errors="ignore")
    assert sentinel in text or "ROUNDTRIP_SENTINEL" in text


def _weasy_available() -> tuple[bool, str]:
    try:
        from weasyprint import HTML

        HTML(string="<html><body><p>cal</p></body></html>").write_pdf()
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160]


def test_weasy_calibration_gate():
    """Document effective PDF engine; when Weasy works, assert no sidecar crutch."""
    ok, detail = _weasy_available()
    if not ok:
        pytest.skip(f"WeasyPrint/pango unavailable: {detail}")
    model = DocumentModel(nodes=[Paragraph(text="WEASY_CALIB_SENTINEL")])
    pdf = export_pdf(model)
    assert pdf.startswith(b"%PDF")
    assert (model.metadata or {}).get("pdf_engine") == "weasyprint"
    assert b"ADUPORT-AST-v1" not in pdf
    assert len(pdf) > 200


def test_ab_roundtrip_over_stream_threshold(ab_benchmark):
    """A/B ②: payload ≥ stream_min_bytes so stream path is eligible; still real roundtrip."""
    assert settings.cache_enabled is False
    min_b = max(int(settings.stream_min_bytes), 256_000)
    line = b"para line with ROUNDTRIP_BIG_SENTINEL\n\n"
    body = line * ((min_b // len(line)) + 8)
    assert len(body) >= min_b
    mid = convert(body, "md", "html", filename="big.md", skip_magic=True)
    assert mid and len(mid) > 1000
    back = convert(mid, "html", "md", filename="big.html", skip_magic=True)
    assert b"ROUNDTRIP_BIG_SENTINEL" in back


def test_health_exposes_illusion_and_weasy_flags():
    from fastapi.testclient import TestClient

    from main import app

    client = TestClient(app)
    r = client.get("/api/health/detail")
    assert r.status_code == 200
    body = r.json()
    assert body.get("xlsx_ast_sheet") is False
    pdf_export = body.get("pdf_export") or {}
    assert pdf_export.get("pdf_ast_sidecar") is False
    assert "weasyprint_available" in pdf_export
    assert pdf_export.get("effective") in ("weasyprint", "pdf_simple")
