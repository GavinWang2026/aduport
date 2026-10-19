"""Built-in embedding, vector store, re-OCR opts, layout feedback."""

from __future__ import annotations

import json

import pytest

from aduport.core.convert_context import import_opts_scope
from aduport.core.document_model import DocumentModel, Heading, Paragraph
from aduport.core.embed import cosine_similarity, hash_embed
from aduport.exporters.rag_elements import export_rag_payload
from aduport.importers.layout_feedback import apply_layout_feedback_to_rag

def test_hash_embed_deterministic_and_normalized():
    a = hash_embed("Hello world 中文", dim=32)
    b = hash_embed("Hello world 中文", dim=32)
    assert a == b
    assert len(a) == 32
    norm = sum(x * x for x in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert cosine_similarity(a, a) > 0.99


def test_rag_embed_schema_1_2(tmp_path, monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "vector_dir", str(tmp_path / "vectors"))
    model = DocumentModel(
        title="emb-demo",
        nodes=[Heading(level=1, text="Title"), Paragraph(text="alpha beta gamma")],
    )
    payload = export_rag_payload(model, max_chars=80, embed=True, vector_persist=True)
    assert payload["schema_version"] == "1.2"
    assert payload["embedding"]["backend"] == "hash"
    assert payload["embedding"]["count"] >= 1
    assert all("embedding" in e for e in payload["elements"])
    assert payload.get("vector_store", {}).get("persisted") is True

    from aduport.core.vector_store import search

    hits = search("alpha", collection="default", top_k=3, tenant_id="default")
    assert hits["hits"]
    assert hits["hits"][0]["text"]
    # Tenant isolation: other tenant must not see these vectors
    assert not search("alpha", collection="default", top_k=3, tenant_id="other")["hits"]


def test_convert_embed_flag():
    from aduport.core.converter import convert

    out = convert(
        b"# Hello\n\nVector me please.\n",
        "md",
        "rag",
        filename="t.md",
        skip_magic=True,
        embed=True,
    )
    payload = json.loads(out.decode())
    assert payload["schema_version"] == "1.2"
    assert payload["elements"][0].get("embedding")


def test_layout_feedback_patches_rag():
    payload = {
        "schema": "aduport.rag_elements.v1",
        "schema_version": "1.1",
        "elements": [
            {
                "type": "NarrativeText",
                "text": "body",
                "metadata": {"element_id": "e1", "node_index": 0, "chunk_index": 0},
            }
        ],
    }
    out = apply_layout_feedback_to_rag(
        payload,
        [{"element_id": "e1", "page": 9, "bbox": [1, 2, 3, 4]}],
    )
    assert out["layout_feedback_patched"] == 1
    assert out["elements"][0]["metadata"]["page_number"] == 9
    assert out["elements"][0]["metadata"]["bbox"] == [1.0, 2.0, 3.0, 4.0]


def test_force_ocr_import_opts_skip_sidecar(monkeypatch):
    from aduport.importers import pdf_importer as pi

    calls: list[str] = []

    def fake_ocr(data, filename, force_engine=None):
        calls.append(force_engine or "auto")
        return DocumentModel(title="ocr", nodes=[Paragraph(text="from-ocr")], metadata={})

    monkeypatch.setattr(pi.settings, "pdf_engine", "auto")
    monkeypatch.setattr(
        "aduport.importers.ocr_engine.import_pdf_with_ocr",
        fake_ocr,
    )
    # Valid-looking header so import_pdf proceeds
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"

    with import_opts_scope(force_ocr=True, skip_pdf_sidecar=True):
        model = pi.import_pdf(pdf, "force.pdf")
    assert calls
    assert model.metadata.get("force_ocr") is True
    assert model.nodes and model.nodes[0].plain_text() == "from-ocr"


def test_cache_key_includes_force_ocr(monkeypatch):
    from aduport.core import converter as cv
    from aduport.core.cache import result_cache

    result_cache.clear()
    monkeypatch.setattr(cv.settings, "cache_enabled", True)
    seen = {"n": 0}

    real_import = cv._import

    def counting_import(*a, **k):
        seen["n"] += 1
        return real_import(*a, **k)

    monkeypatch.setattr(cv, "_import", counting_import)
    raw = b"# A\n\nbody\n"
    cv.convert(raw, "md", "txt", filename="c.md", skip_magic=True)
    cv.convert(raw, "md", "txt", filename="c.md", skip_magic=True)
    assert seen["n"] == 1
    cv.convert(raw, "md", "txt", filename="c.md", skip_magic=True, cache_bypass=True)
    assert seen["n"] == 2


def test_webhook_rag_hint_mentions_embed(monkeypatch):
    from aduport.core import webhooks as wh

    captured: dict = {}

    def fake_post(url, body, timeout=10.0, **kwargs):
        captured["body"] = body
        return 200

    monkeypatch.setattr(wh.settings, "webhook_enabled", True)
    monkeypatch.setattr(wh, "post_json_pinned", fake_post)
    monkeypatch.setattr(wh, "validate_callback_url", lambda u: u)
    wh.fire_job_webhook(
        {"id": "j1", "target_format": "rag", "status": "done"},
        "https://example.com/hook",
    )
    assert captured["body"]["result_format"]["schema_version_embed"] == "1.2"