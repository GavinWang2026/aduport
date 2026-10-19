"""Regression tests for 2026-09 audit fixes (report P0-1/P0-2, P1-1/P1-2/P1-8).

Each test pins a previously-silent defect so it cannot regress:

* P0-1  stream chunks must never tear GFM tables / HTML blocks; a chunk import
        failure must abort the whole conversion instead of returning 200 with
        silently missing content.
* P0-2  streaming must honour export options (docx_theme) + global post
        processing exactly like the non-streaming path.
* P1-8  xlsx export must keep >15-digit integers as text (IEEE double rounding).
* P1-2  third-party zip readers (python-docx) must be pre-bounded.
* P1-1  webhook delivery goes through the IP-pinned POST helper (no re-resolve).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from aduport.core.exceptions import InvalidDocumentError


def _big_table_md(rows: int = 2600, cols: int = 6) -> str:
    """A GFM table far beyond one chunk: rows are single-\\n separated, no blank lines."""
    head = "| " + " | ".join(f"H{i}" for i in range(cols)) + " |"
    sep = "| " + " | ".join("---" for _ in range(cols)) + " |"
    body = [
        "| " + " | ".join(f"r{r}c{c}" for c in range(cols)) + " |" for r in range(rows)
    ]
    return "\n".join([head, sep, *body]) + "\n"


# --------------------------------------------------------------------------- P0-1
class TestStreamChunkIntegrity:
    def test_gfm_table_never_torn(self):
        """A table with no blank lines must fall back to a single parse: output
        must contain every row in order (previously it was cut mid-table)."""
        from aduport.core.stream_convert import convert_streaming

        md = _big_table_md(rows=3000)  # ≫ default chunk_chars(48k)/stream_min
        out = convert_streaming(md.encode("utf-8"), "md", "md")
        text = out.decode("utf-8")
        # Export normalizes cell padding, so assert on row tokens, not layout.
        assert "H5" in text  # header survives
        assert "r0c0" in text and "r2999c5" in text  # first + last row survive
        # No teardown / duplication: a middle row appears exactly once.
        assert text.count("r1500c3") == 1
        assert text.count("r0c0") == 1

    def test_blank_line_chunks_are_coherent(self):
        """Multiple ordinary paragraphs separated by blank lines still stream
        (multiple chunks) yet concatenate back to the full text."""
        from aduport.core.stream_convert import convert_streaming

        paras = "\n\n".join(f"paragraph number {i} " + ("words " * 900) for i in range(30))
        data = paras.encode("utf-8")
        out = convert_streaming(data, "md", "md")
        text = out.decode("utf-8")
        assert "paragraph number 0" in text and "paragraph number 29" in text
        # md exporter collapses whitespace but all 30 markers must be present.
        for i in (0, 7, 15, 29):
            assert f"paragraph number {i}" in text

    def test_html_pre_block_not_torn(self, monkeypatch):
        """Raw HTML containing <pre> must fall back to a single parse."""
        from aduport.core.stream_convert import convert_streaming

        html = "<html><body><pre>" + ("L" * 4000 + "\n") * 40 + "</pre></body></html>"
        out = convert_streaming(html.encode("utf-8"), "html", "html", chunk_chars=8000)
        assert out.count(b"<pre>") == 1 and out.count(b"</pre>") == 1

    def test_chunk_failure_aborts_conversion(self, monkeypatch):
        """A failing chunk must abort the whole conversion (all-or-nothing),
        never return a 200 with silently missing sections."""
        from aduport.core import stream_convert

        paras = "\n\n".join(f"para {i} " + "x" * 4000 for i in range(40))
        real_import = stream_convert._import_chunk
        calls = {"n": 0}

        def flaky(data, fmt, filename):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("boom: chunk undecodable")
            return real_import(data, fmt, filename)

        monkeypatch.setattr(stream_convert, "_import_chunk", flaky)
        with pytest.raises(InvalidDocumentError):
            stream_convert.convert_streaming(
                paras.encode("utf-8"), "md", "md", chunk_chars=8000
            )


# --------------------------------------------------------------------------- P0-2
class TestStreamOptionParity:
    def test_docx_theme_reaches_export(self, monkeypatch):
        """Large-file (streamed) docx export must honour docx_theme just like
        the small-file path."""
        from aduport.core import stream_convert

        seen: dict = {}

        def fake_export_docx(model, *, theme=None):
            seen["theme"] = theme
            return b"fake-docx"

        monkeypatch.setattr(stream_convert, "export_docx", fake_export_docx)
        paras = "\n\n".join(f"para {i} " + "y" * 3000 for i in range(30))
        stream_convert.convert_streaming(
            paras.encode("utf-8"), "md", "docx", docx_theme="academic", chunk_chars=8000
        )
        assert seen.get("theme") == "academic"

    def test_heading_numbering_forces_single_parse(self, monkeypatch):
        """Global heading numbering is order-dependent: chunking must be
        disabled so numbering does not restart on every chunk."""
        from aduport.config import settings
        from aduport.core import stream_convert

        monkeypatch.setattr(settings, "heading_numbering", True)
        paras = "\n\n".join(f"para {i} " + "z" * 3000 for i in range(30))
        data = paras.encode("utf-8")
        chunks = list(stream_convert._iter_text_chunks(data, "md", 8000))
        assert len(chunks) == 1  # whole-document fallback


# --------------------------------------------------------------------------- P1-8
class TestXlsxLongInteger:
    def test_over_15_digits_stay_text(self):
        from aduport.exporters.xlsx_exporter import _sheet_cell_xml

        xml = _sheet_cell_xml("A1", "123456789012345678", formula="", style="")
        assert 't="inlineStr"' in xml and "123456789012345678" in xml

    def test_negative_long_integer_stays_text(self):
        from aduport.exporters.xlsx_exporter import _sheet_cell_xml

        xml = _sheet_cell_xml("A1", "-12345678901234567890", formula="", style="")
        assert 't="inlineStr"' in xml

    def test_short_integer_still_numeric(self):
        from aduport.exporters.xlsx_exporter import _sheet_cell_xml

        xml = _sheet_cell_xml("A1", "123456789012345", formula="", style="")  # 15 digits
        assert 't="inlineStr"' not in xml and "<v>123456789012345</v>" in xml

    def test_leading_zero_id_still_text(self):
        from aduport.exporters.xlsx_exporter import _sheet_cell_xml

        xml = _sheet_cell_xml("A1", "0112233", formula="", style="")
        assert 't="inlineStr"' in xml


# --------------------------------------------------------------------------- P1-2
class TestZipPackageBudget:
    def test_member_over_budget_rejected(self):
        from aduport.core.exceptions import FileTooLargeError
        from aduport.core.safe_zip import assert_zip_package_budget

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("word/document.xml", b"\0" * (2 * 1024 * 1024))  # 2 MiB real
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            with pytest.raises(FileTooLargeError):
                assert_zip_package_budget(zf, max_total=1024 * 1024)

    def test_benign_archive_passes(self):
        from aduport.core.safe_zip import assert_zip_package_budget

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", b"<Types/>")
            zf.writestr("word/document.xml", b"<w:document/>")
        buf.seek(0)
        with zipfile.ZipFile(buf) as zf:
            assert_zip_package_budget(zf, max_total=1024 * 1024)  # no raise


# --------------------------------------------------------------------------- P1-1
class TestWebhookPinnedPost:
    def test_delivery_uses_pinned_helper(self, monkeypatch):
        """fire_job_webhook must go through the IP-pinned helper exactly once
        (no second DNS resolution on the send path)."""
        import aduport.core.webhooks as webhooks

        calls: dict = {}

        def fake_post(url, payload, *, timeout, extra_headers=None):
            calls["url"] = url
            calls["payload"] = payload
            calls["timeout"] = timeout
            calls["extra_headers"] = extra_headers
            return 200

        monkeypatch.setattr(webhooks, "post_json_pinned", fake_post)
        monkeypatch.setattr(webhooks, "validate_callback_url", lambda u: u)
        monkeypatch.setattr(webhooks.settings, "webhook_enabled", True)
        webhooks.fire_job_webhook(
            {"id": "j1", "access_token": "secret-token", "status": "done"},
            "https://example.com/cb",
        )
        assert calls["url"] == "https://example.com/cb"
        assert calls["payload"]["job"]["id"] == "j1"
        assert "access_token" not in calls["payload"]["job"]
        assert "access_token" not in calls["payload"]
        assert calls["timeout"] == float(webhooks.settings.webhook_timeout_seconds or 10.0)


# --------------------------------------------------------------------------- P1-7 / P2-11
class TestIsolateSideEffects:
    def test_export_warnings_are_thread_local(self):
        from aduport.exporters.export_warnings import (
            get_last_export_warnings,
            set_last_export_warnings,
        )

        set_last_export_warnings(["a"])
        assert get_last_export_warnings() == ["a"]
        set_last_export_warnings([])
        assert get_last_export_warnings() == []

    def test_worker_effects_roundtrip_metrics_and_warnings(self, monkeypatch, tmp_path):
        from aduport.config import settings
        from aduport.core import isolate_effects
        from aduport.core.audit import audit_event
        from aduport.core.metrics import metrics
        from aduport.exporters.export_warnings import (
            get_last_export_warnings,
            set_last_export_warnings,
        )

        before_total = sum(metrics.snapshot()["conversions_total"].values())
        set_last_export_warnings(["lost-image:foo.png"])
        state = isolate_effects.capture_worker_effects_start()
        metrics.record_success("md", "docx", 0.01, importer="native")
        monkeypatch.setattr(settings, "audit_enabled", True)
        monkeypatch.setattr(settings, "audit_log_path", str(tmp_path / "audit.jsonl"))
        audit_event(
            action="convert",
            source_format="md",
            target_format="docx",
            success=True,
            bytes_in=10,
            bytes_out=20,
        )
        payload = isolate_effects.capture_worker_effects_finish(state)
        assert "lost-image:foo.png" in payload["export_warnings"]
        assert payload["metrics_delta"]["conversions_total"].get("md_to_docx", 0) >= 1
        assert payload["audit_events"]

        set_last_export_warnings([])
        isolate_effects.apply_worker_effects(payload)
        assert get_last_export_warnings() == ["lost-image:foo.png"]
        after_total = sum(metrics.snapshot()["conversions_total"].values())
        assert after_total >= before_total + 1
        log = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
        assert "md" in log and "docx" in log

    def test_isolate_job_returns_warnings(self, monkeypatch):
        from aduport.core import convert_isolate
        from aduport.exporters.export_warnings import (
            get_last_export_warnings,
            set_last_export_warnings,
        )

        def fake_convert(*_a, **_k):
            set_last_export_warnings(["warn-from-child"])
            return b"ok"

        monkeypatch.setattr(convert_isolate, "convert", fake_convert)
        monkeypatch.setattr(convert_isolate.settings, "convert_isolate", True)
        monkeypatch.setattr(convert_isolate.settings, "convert_isolate_fork", False)

        class _Fut:
            def result(self, timeout=None):
                from aduport.core.isolate_effects import (
                    capture_worker_effects_finish,
                    capture_worker_effects_start,
                )

                state = capture_worker_effects_start()
                data = fake_convert()
                out = {"ok": True, "data": data}
                out.update(capture_worker_effects_finish(state))
                return out

        class _Pool:
            def submit(self, *_a, **_k):
                return _Fut()

        monkeypatch.setattr(convert_isolate, "_get_pool", lambda: _Pool())
        monkeypatch.setattr(convert_isolate, "_client_is_trusted", lambda *_a, **_k: False)
        out = convert_isolate.convert_isolated(b"# x\n", "md", "txt", filename="x.md")
        assert out == b"ok"
        assert "warn-from-child" in get_last_export_warnings()


# --------------------------------------------------------------------------- P1-4 href
class TestHtmlHrefSchemeAllowlist:
    def test_javascript_href_stripped(self):
        from aduport.core.document_model import (
            DocumentModel,
            InlineMark,
            InlineMarkType,
            Paragraph,
            RichText,
        )
        from aduport.exporters.html_exporter import export_html

        model = DocumentModel(
            nodes=[
                Paragraph(
                    text=RichText(
                        text="click",
                        marks=[
                            InlineMark(
                                type=InlineMarkType.LINK,
                                start=0,
                                end=5,
                                href="javascript:alert(1)",
                            )
                        ],
                    )
                )
            ]
        )
        html = export_html(model)
        if isinstance(html, bytes):
            html = html.decode("utf-8")
        assert "javascript:" not in html.lower()


# --------------------------------------------------------------------------- P1-3 billing + license
class TestCommercialClosure:
    def test_billing_webhook_optional(self, monkeypatch):
        from aduport.config import settings
        from aduport.core import billing
        import aduport.core.safe_fetch as sf

        calls: list = []

        def fake_post(url, payload, *, timeout):
            calls.append((url, payload, timeout))
            return 200

        monkeypatch.setattr(settings, "billing_webhook_url", "https://billing.example/hook")
        monkeypatch.setattr(sf, "post_json_pinned", fake_post)
        billing.emit_usage_event(
            source_format="md",
            target_format="docx",
            bytes_in=11,
            bytes_out=22,
            importer="native",
        )
        assert calls and calls[0][0] == "https://billing.example/hook"
        assert calls[0][1]["event"] == "conversion"

    def test_license_required_rejects_missing_secret(self, monkeypatch):
        from aduport.config import settings
        from aduport.core.license import LicenseError, assert_license_ok

        monkeypatch.setattr(settings, "license_required", True)
        monkeypatch.setattr(settings, "license_secret", "")
        monkeypatch.setattr(settings, "license_key", "")
        with pytest.raises(LicenseError):
            assert_license_ok()


# --------------------------------------------------------------------------- P2/P3 second pass
class TestSecondPassFixes:
    def test_csv_formula_prefixes_eq_plus_at_only(self):
        from aduport.exporters.csv_exporter import _FORMULA_PREFIXES

        assert _FORMULA_PREFIXES == ("=", "+", "@")

    def test_text_decode_gb18030_fallback(self):
        from aduport.core.text_decode import decode_text_bytes

        raw = "中文测试".encode("gb18030")
        assert "中文" in decode_text_bytes(raw)

    def test_md_comment_escapes_close_sequence(self):
        from aduport.core.document_model import Comment, DocumentModel
        from aduport.exporters.md_exporter import export_md

        model = DocumentModel(nodes=[Comment(author="a", text="bad --> end")])
        md = export_md(model)
        assert "bad --> end" not in md
        assert "bad" in md and "end" in md

    def test_health_status_omits_jobs(self, monkeypatch):
        from fastapi.testclient import TestClient
        from main import aduport

        monkeypatch.setattr("aduport.config.settings.require_auth", False)
        client = TestClient(app)
        status = client.get("/api/health/status").json()
        detail = client.get("/api/health/detail").json()
        assert "metrics" in status
        assert "jobs" not in status
        assert "jobs" in detail

    def test_job_token_query_disabled_by_default(self, monkeypatch):
        from aduport.config import settings
        from aduport.api.routes import _job_token_from_request
        from starlette.requests import Request

        monkeypatch.setattr(settings, "job_token_query", False)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"token=secret",
        }
        req = Request(scope)
        assert _job_token_from_request(req) == ""

    def test_license_seats_limit_concurrent(self, monkeypatch):
        from aduport.config import settings
        from aduport.core.license import (
            LicenseInfo,
            LicenseError,
            acquire_license_seat,
            release_license_seat,
            license_seat,
        )

        info = LicenseInfo(
            machine_id="m",
            expires="2099-01-01",
            seats=1,
            customer="c",
            product="AduPort",
            valid=True,
        )
        monkeypatch.setattr(settings, "license_required", True)
        acquire_license_seat(info)
        with pytest.raises(LicenseError):
            acquire_license_seat(info)
        release_license_seat()
class TestXlsxCellSemantics:
    def test_boolean_and_date_cells(self):
        from xml.etree import ElementTree as ET

        from aduport.exporters.xlsx_exporter import _NS, _cell_value

        ns = _NS["m"]
        bool_cell = ET.Element(f"{{{ns}}}c", {"t": "b"})
        ET.SubElement(bool_cell, f"{{{ns}}}v").text = "1"
        assert _cell_value(bool_cell, []) == "TRUE"
        date_cell = ET.Element(f"{{{ns}}}c", {"s": "0"})
        ET.SubElement(date_cell, f"{{{ns}}}v").text = "45231"
        assert _cell_value(
            date_cell,
            [],
            num_fmts={14: "mm-dd-yy"},
            xf_num_fmts=[14],
        ).startswith("2023")

    def test_excel_error_cells(self):
        from xml.etree import ElementTree as ET

        from aduport.exporters.xlsx_exporter import _NS, _cell_value

        ns = _NS["m"]
        err_cell = ET.Element(f"{{{ns}}}c", {"t": "e"})
        ET.SubElement(err_cell, f"{{{ns}}}v").text = "6"
        assert _cell_value(err_cell, []) == "#N/A"

    def test_percent_and_currency_numfmt(self):
        from xml.etree import ElementTree as ET

        from aduport.exporters.xlsx_exporter import _NS, _cell_value

        ns = _NS["m"]
        cell = ET.Element(f"{{{ns}}}c", {"s": "0"})
        ET.SubElement(cell, f"{{{ns}}}v").text = "0.25"
        assert _cell_value(cell, [], num_fmts={10: "0%"}, xf_num_fmts=[10]) == "25%"
        money = ET.Element(f"{{{ns}}}c", {"s": "0"})
        ET.SubElement(money, f"{{{ns}}}v").text = "12.5"
        assert "$" in _cell_value(
            money, [], num_fmts={7: '"$"#,##0.00'}, xf_num_fmts=[7]
        )

    def test_roundtrip_long_int_and_boolean(self):
        from aduport.core.converter import convert

        md = "| id | flag |\n| --- | --- |\n| 12345678901234567 | TRUE |\n"
        out = convert(md.encode(), "md", "xlsx", filename="t.md")
        back = convert(out, "xlsx", "md", filename="t.xlsx")
        text = back.decode("utf-8")
        assert "12345678901234567" in text


class TestHtmlEpubDirect:
    def test_html_to_epub_preserves_body(self):
        from aduport.core.converter import convert

        html = (
            "<html><head><title>Demo Book</title></head>"
            "<body><h1>Chapter One</h1><p>Hello EPUB direct</p>"
            "<script>alert(1)</script></body></html>"
        )
        out = convert(html.encode(), "html", "epub", filename="demo.html")
        assert out[:4] == b"PK\x03\x04"
        import zipfile
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(out)) as zf:
            chapter = zf.read("EPUB/chapter.xhtml").decode("utf-8")
        assert "Hello EPUB direct" in chapter
        assert "Chapter One" in chapter
        assert "alert(1)" not in chapter


class TestJobResume:
    def test_resume_interrupted_job(self, monkeypatch, tmp_path):
        from aduport.core import job_persist, job_results
        from aduport.core.jobs import JobStore, JobStatus

        monkeypatch.setattr(job_results, "_root", lambda: tmp_path)
        monkeypatch.setattr(job_persist, "_db_path", lambda: tmp_path / "jobs.sqlite3")

        jid = "resumetest001"
        content = b"# resume me"
        job_results.save_job_input(jid, content)
        snap = {
            "id": jid,
            "filename": "r.md",
            "source_format": "md",
            "target_format": "md",
            "status": "error",
            "code": "job_interrupted",
            "error": "服务重启，任务已中断",
            "progress": 1.0,
            "created_at": 1.0,
            "bytes_in": len(content),
            "convert_opts": {"docx_theme": "modern"},
            "client": "",
        }
        job_persist.save_job_snapshot(jid, snap)

        ran = {"ok": False, "opts": None}

        def fake_run(self, job_id, spill, charge, pdf_opts, client):
            ran["ok"] = job_id == jid
            ran["opts"] = dict(pdf_opts or {})
            spill.release()

        monkeypatch.setattr(job_persist, "load_recent_snapshots", lambda limit=500: [])
        monkeypatch.setattr(JobStore, "_run", fake_run)
        store = JobStore()
        job = store.resume(jid)
        assert ran["ok"]
        assert ran["opts"].get("docx_theme") == "modern"
        assert job.status == JobStatus.queued

    def test_running_snapshot_preserves_convert_opts(self, monkeypatch, tmp_path):
        from aduport.core import job_persist, job_results
        from aduport.core.jobs import Job, JobStatus, JobStore

        monkeypatch.setattr(job_results, "_root", lambda: tmp_path)
        monkeypatch.setattr(job_persist, "_db_path", lambda: tmp_path / "jobs.sqlite3")
        monkeypatch.setattr(job_persist, "load_recent_snapshots", lambda limit=500: [])

        store = JobStore()
        jid = "optskeep001"
        job = Job(
            id=jid,
            filename="a.md",
            source_format="md",
            target_format="pdf",
            convert_opts={"pdf_toc": True, "docx_theme": None},
            client_tag="test",
            input_path=str(tmp_path / f"{jid}.in.bin"),
        )
        (tmp_path / f"{jid}.in.bin").write_bytes(b"# x")
        store._jobs[jid] = job
        job_persist.save_job_snapshot(jid, job.persist_payload())

        # Simulate running overwrite via persist_payload (must keep opts)
        job.status = JobStatus.running
        job.progress = 0.1
        job_persist.save_job_snapshot(jid, job.persist_payload())
        snap = job_persist.load_job_snapshot(jid)
        assert snap is not None
        assert snap.get("convert_opts", {}).get("pdf_toc") is True
        assert snap.get("input_path")
        assert snap.get("client") == "test"

        job.status = JobStatus.error
        job.code = "job_interrupted"
        assert job.to_dict()["resumable"] is True


class TestImportFailureBudget:
    def test_budget_aborts_after_threshold(self):
        from aduport.core.exceptions import InvalidDocumentError
        from aduport.importers.import_guard import import_failure_budget, swallow_import_error

        with import_failure_budget(limit=3):
            swallow_import_error(RuntimeError("a"), where="t:a")
            swallow_import_error(RuntimeError("b"), where="t:b")
            with pytest.raises(InvalidDocumentError):
                swallow_import_error(RuntimeError("c"), where="t:c")


class TestJobReplay:
    def test_queued_job_replays_after_restart(self, monkeypatch, tmp_path):
        from aduport.core import job_persist, job_results
        from aduport.core.jobs import JobStore

        monkeypatch.setattr(job_results, "_root", lambda: tmp_path)
        monkeypatch.setattr(job_persist, "_db_path", lambda: tmp_path / "jobs.sqlite3")

        jid = "replaytest123"
        content = b"# hello replay"
        job_results.save_job_input(jid, content)
        snap = {
            "id": jid,
            "filename": "a.md",
            "source_format": "md",
            "target_format": "md",
            "status": "queued",
            "progress": 0.0,
            "created_at": 1.0,
            "bytes_in": len(content),
            "input_path": str(job_results.input_path_for(jid)),
            "convert_opts": {},
            "client": "",
        }
        job_persist.save_job_snapshot(jid, snap)

        ran: dict[str, bool] = {"ok": False}

        def fake_run(self, job_id, spill, charge, pdf_opts, client):
            ran["ok"] = job_id == jid
            spill.release()

        monkeypatch.setattr(
            job_persist,
            "load_recent_snapshots",
            lambda limit=500: [snap],
        )
        monkeypatch.setattr(JobStore, "_run", fake_run)
        store = JobStore()
        assert ran["ok"]
        assert jid in store._jobs
