"""One probe per registered /api route.

This is the route census, not the 35 × 5 input classes × 2 isolate modes
matrix. Each probe is either a cheap success or a catalogued error. A new
route fails this file until it is added here.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.exceptions import ERROR_CODES
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "require_auth", False)
    return TestClient(app)


def _live_routes() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def _take(route) -> None:
        path = str(getattr(route, "path", "") or "")
        if not path.startswith("/api"):
            return
        methods = set(getattr(route, "methods", None) or ()) - {"HEAD", "OPTIONS"}
        for method in methods:
            found.add((method, path))

    for route in app.routes:
        _take(route)
        context = getattr(route, "include_context", None)
        included = getattr(context, "included_router", None)
        for nested in getattr(included, "routes", ()) or ():
            _take(nested)
    return found


def _assert_catalogued(response) -> None:
    if response.status_code < 400:
        return
    code = str(response.json().get("code") or "")
    assert code in ERROR_CODES, response.text[:400]
    assert response.status_code == ERROR_CODES[code]


def test_every_api_route_has_one_probe(client):
    probes = {
        ("GET", "/api/health"): lambda: client.get("/api/health"),
        ("GET", "/api/health/ready"): lambda: client.get("/api/health/ready"),
        ("GET", "/api/health/status"): lambda: client.get("/api/health/status"),
        ("GET", "/api/health/detail"): lambda: client.get("/api/health/detail"),
        ("GET", "/api/usage"): lambda: client.get("/api/usage"),
        ("GET", "/api/tenants"): lambda: client.get("/api/tenants"),
        ("GET", "/api/license"): lambda: client.get("/api/license"),
        ("GET", "/api/formats"): lambda: client.get("/api/formats"),
        ("GET", "/api/metrics"): lambda: client.get("/api/metrics"),
        ("GET", "/api/jobs"): lambda: client.get("/api/jobs"),
        ("GET", "/api/jobs/{job_id}"): lambda: client.get("/api/jobs/missing-job"),
        ("GET", "/api/jobs/{job_id}/events"): lambda: client.get(
            "/api/jobs/missing-job/events"
        ),
        ("GET", "/api/jobs/{job_id}/result"): lambda: client.get(
            "/api/jobs/missing-job/result"
        ),
        ("POST", "/api/auth/session"): lambda: client.post("/api/auth/session"),
        ("DELETE", "/api/auth/session"): lambda: client.delete("/api/auth/session"),
        ("POST", "/api/jobs"): lambda: client.post("/api/jobs"),
        ("POST", "/api/jobs/{job_id}/resume"): lambda: client.post(
            "/api/jobs/missing-job/resume"
        ),
        ("POST", "/api/jobs/{job_id}/reprocess"): lambda: client.post(
            "/api/jobs/missing-job/reprocess"
        ),
        ("POST", "/api/rag/layout_feedback"): lambda: client.post(
            "/api/rag/layout_feedback", json={}
        ),
        ("POST", "/api/vectors/search"): lambda: client.post(
            "/api/vectors/search", json={}
        ),
        ("POST", "/api/convert"): lambda: client.post(
            "/api/convert",
            files={"file": ("empty.md", b"", "text/markdown")},
            data={"source_format": "md", "target_format": "html"},
        ),
        ("POST", "/api/convert/batch"): lambda: client.post(
            "/api/convert/batch",
            data={"target_format": "html"},
        ),
        ("POST", "/api/watch/scan"): lambda: client.post(
            "/api/watch/scan",
            data={
                "input_dir": "/no/such/aduport-watch-in",
                "output_dir": "/no/such/aduport-watch-out",
                "target_format": "md",
            },
        ),
        ("POST", "/api/fidelity/probe"): lambda: client.post("/api/fidelity/probe"),
        ("POST", "/api/pdf/merge"): lambda: client.post(
            "/api/pdf/merge",
            files=[("files", ("only.pdf", b"%PDF-1.4\n", "application/pdf"))],
        ),
        ("POST", "/api/pdf/extract"): lambda: client.post(
            "/api/pdf/extract",
            files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
            data={"pages": "1"},
        ),
        ("POST", "/api/pdf/rotate"): lambda: client.post(
            "/api/pdf/rotate",
            files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
            data={"degrees": "90"},
        ),
        ("POST", "/api/pdf/split"): lambda: client.post(
            "/api/pdf/split",
            files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
        ),
        ("POST", "/api/redact"): lambda: client.post(
            "/api/redact",
            files={"file": ("note.bin", b"\xff\xfe", "application/octet-stream")},
        ),
        ("POST", "/api/diff"): lambda: client.post(
            "/api/diff",
            files={
                "left": ("left", b"a", "application/octet-stream"),
                "right": ("right", b"b", "application/octet-stream"),
            },
        ),
        ("POST", "/api/pdf/form/extract"): lambda: client.post(
            "/api/pdf/form/extract",
            files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
        ),
        ("POST", "/api/pdf/form/fill"): lambda: client.post(
            "/api/pdf/form/fill",
            files={"file": ("bad.pdf", b"not-a-pdf", "application/pdf")},
            data={"values": "not-json"},
        ),
        ("POST", "/api/sign"): lambda: client.post(
            "/api/sign",
            files={"file": ("a.txt", b"hello", "text/plain")},
            data={"key": "x"},
        ),
        ("POST", "/api/verify"): lambda: client.post(
            "/api/verify",
            files={"file": ("a.txt", b"hello", "text/plain")},
            data={"key": "x", "mac": "ab"},
        ),
    }
    live = _live_routes()
    assert live == set(probes), (
        f"missing probes: {sorted(live - set(probes))}; "
        f"stale probes: {sorted(set(probes) - live)}"
    )
    for key in sorted(probes):
        response = probes[key]()
        assert response.status_code != 500, f"{key} -> {response.text[:300]}"
        _assert_catalogued(response)


@pytest.mark.parametrize("isolate", [False, True])
def test_convert_both_isolate_modes_cover_ok_and_empty(client, monkeypatch, isolate):
    monkeypatch.setattr(settings, "convert_isolate", isolate)
    ok = client.post(
        "/api/convert",
        files={"file": ("a.md", b"# T\n\nHello.\n", "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert ok.status_code == 200, ok.text[:300]
    assert b"Hello" in ok.content
    empty = client.post(
        "/api/convert",
        files={"file": ("empty.md", b"", "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert empty.status_code == ERROR_CODES["empty_file"]
    assert empty.json()["code"] == "empty_file"
    assert "message_en" in empty.json()
    assert not re.search(r"[\u4e00-\u9fff]", empty.json()["message_en"])


def test_second_input_class_on_file_routes(client):
    """Empty where the census used garbage, and two garbage PDFs for merge."""
    responses = [
        client.post(
            "/api/pdf/extract",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"pages": "1"},
        ),
        client.post(
            "/api/pdf/rotate",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"degrees": "90"},
        ),
        client.post(
            "/api/pdf/split",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        ),
        client.post(
            "/api/pdf/form/extract",
            files={"file": ("empty.pdf", b"", "application/pdf")},
        ),
        client.post(
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", b"not-a-pdf", "application/pdf")),
                ("files", ("b.pdf", b"also-not", "application/pdf")),
            ],
        ),
        client.post(
            "/api/jobs",
            files={"file": ("empty.md", b"", "text/markdown")},
            data={"source_format": "md", "target_format": "html"},
        ),
    ]
    for response in responses:
        assert response.status_code >= 400, response.text[:300]
        assert response.status_code != 500
        _assert_catalogued(response)


def test_every_write_route_rejects_an_oversized_content_length(client, monkeypatch):
    """Content-Length above the route ceiling is 413 on every write, before the handler."""
    monkeypatch.setattr("aduport.core.upload_limit.body_ceiling", lambda _path: 0)
    for method, path in sorted(_live_routes()):
        url = path.replace("{job_id}", "missing-job")
        if method in {"GET", "DELETE"}:
            response = client.request(method, url)
            assert response.status_code != 413, (method, path)
            continue
        response = client.request(method, url, content=b"0123456789")
        assert response.status_code == ERROR_CODES["file_too_large"], (
            method,
            path,
            response.text[:240],
        )
        body = response.json()
        assert body["code"] == "file_too_large"
        assert not re.search(r"[\u4e00-\u9fff]", body["message_en"])
        assert "10" in body["message_en"]


def test_every_upload_route_stops_reading_past_the_file_limit(client, monkeypatch):
    """A body under the Content-Length slack still dies in the per-file reader."""
    monkeypatch.setattr("aduport.core.upload_limit.upload_byte_limit", lambda: 8)
    monkeypatch.setattr("aduport.core.upload_limit.batch_byte_limit", lambda: 8)
    blob = b"0123456789abcdef"
    common = {"source_format": "md", "target_format": "html"}
    calls = [
        client.post(
            "/api/convert",
            files={"file": ("a.md", blob, "text/markdown")},
            data=common,
        ),
        client.post(
            "/api/jobs",
            files={"file": ("a.md", blob, "text/markdown")},
            data=common,
        ),
        client.post(
            "/api/convert/batch",
            files=[("files", ("a.md", blob, "text/markdown"))],
            data={"target_format": "html"},
        ),
        client.post("/api/fidelity/probe", data={"text": blob.decode()}),
        client.post(
            "/api/pdf/merge",
            files=[
                ("files", ("a.pdf", blob, "application/pdf")),
                ("files", ("b.pdf", blob, "application/pdf")),
            ],
        ),
        client.post(
            "/api/pdf/extract",
            files={"file": ("a.pdf", blob, "application/pdf")},
            data={"pages": "1"},
        ),
        client.post(
            "/api/pdf/rotate",
            files={"file": ("a.pdf", blob, "application/pdf")},
            data={"degrees": "90"},
        ),
        client.post(
            "/api/pdf/split",
            files={"file": ("a.pdf", blob, "application/pdf")},
        ),
        client.post(
            "/api/redact",
            files={"file": ("a.txt", blob, "text/plain")},
        ),
        client.post(
            "/api/diff",
            files={
                "left": ("a.md", blob, "text/markdown"),
                "right": ("b.md", blob, "text/markdown"),
            },
        ),
        client.post(
            "/api/pdf/form/extract",
            files={"file": ("a.pdf", blob, "application/pdf")},
        ),
        client.post(
            "/api/pdf/form/fill",
            files={"file": ("a.pdf", blob, "application/pdf")},
            data={"values": "{}"},
        ),
        client.post(
            "/api/sign",
            files={"file": ("a.txt", blob, "text/plain")},
            data={"key": "0123456789abcdef"},
        ),
        client.post(
            "/api/verify",
            files={"file": ("a.txt", blob, "text/plain")},
            data={"key": "0123456789abcdef", "mac": "ab"},
        ),
    ]
    assert len(calls) == 14
    for response in calls:
        assert response.status_code == ERROR_CODES["file_too_large"], response.text[:240]
        assert response.json()["code"] == "file_too_large"


def test_warning_headers_on_convert_batch_and_partial_redact(client):
    payload = b"# T\n\nHello warning.\n"
    data = {"source_format": "md", "target_format": "pdf", "pdf_engine": "simple"}
    converted = client.post(
        "/api/convert",
        files={"file": ("a.md", payload, "text/markdown")},
        data=data,
    )
    assert converted.status_code == 200, converted.text[:300]
    assert converted.headers.get("x-aduport-export-degraded") == "true"
    batch = client.post(
        "/api/convert/batch",
        files=[("files", ("a.md", payload, "text/markdown"))],
        data=data,
    )
    assert batch.status_code == 200, batch.text[:300]
    assert batch.headers.get("x-aduport-export-degraded") == "true"
    redacted = client.post(
        "/api/redact",
        files={"file": ("a.pdf", b"%PDF-1.4\n% FlateDecode\n", "application/pdf")},
    )
    assert redacted.status_code == 200, redacted.text[:300]
    assert redacted.headers.get("x-aduport-redact-partial") == "true"
    assert redacted.headers.get("x-aduport-export-degraded") == "true"
