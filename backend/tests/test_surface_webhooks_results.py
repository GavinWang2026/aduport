"""Surface gaps: webhooks + job result disk persistence."""

from __future__ import annotations

import pytest

from aduport.core.job_results import delete_job_result, load_job_result, save_job_result
from aduport.core.webhooks import fire_job_webhook, validate_callback_url


def test_validate_callback_url_ok(monkeypatch):
    monkeypatch.setattr("aduport.core.webhooks.is_safe_url", lambda u: True)
    assert validate_callback_url("https://hooks.example/md") == "https://hooks.example/md"
    assert validate_callback_url("  ") is None
    assert validate_callback_url(None) is None


def test_validate_callback_url_rejects_bad_scheme():
    with pytest.raises(ValueError):
        validate_callback_url("ftp://x")
    with pytest.raises(ValueError):
        validate_callback_url("not-a-url")


def test_validate_callback_url_rejects_private(monkeypatch):
    monkeypatch.setattr("aduport.core.webhooks.is_safe_url", lambda u: False)
    with pytest.raises(ValueError, match="内网|不可达"):
        validate_callback_url("http://169.254.169.254/latest")


def test_job_result_roundtrip(tmp_path, monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "job_result_dir", str(tmp_path / "results"))
    jid = "abc123def"
    path = save_job_result(jid, b"hello-result")
    assert path
    assert load_job_result(jid) == b"hello-result"
    delete_job_result(jid)
    assert load_job_result(jid) is None


def test_job_result_tenant_read_write_symmetric(tmp_path, monkeypatch):
    """v14 P1-1: save under tenant_id must be readable with the same tenant_id."""
    from aduport.config import settings
    from aduport.core.job_results import (
        input_path_for,
        load_job_input,
        result_path_for,
        save_job_input,
    )

    monkeypatch.setattr(settings, "job_result_dir", str(tmp_path / "results"))
    jid = "tenantjob01"
    assert save_job_result(jid, b"out-bytes", tenant_id="default")
    assert save_job_input(jid, b"in-bytes", tenant_id="default")
    assert load_job_result(jid, tenant_id="default") == b"out-bytes"
    assert load_job_input(jid, tenant_id="default") == b"in-bytes"
    # Cross-tenant / missing tenant must not resolve the tenant-scoped blob.
    assert load_job_result(jid, tenant_id="other") is None
    rp = result_path_for(jid, "default")
    ip = input_path_for(jid, "default")
    assert rp and rp.parts[-2] == "default"
    assert ip and ip.parts[-2] == "default"


def test_safe_tenant_rejects_dot_dot(tmp_path, monkeypatch):
    from aduport.config import settings
    from aduport.core.job_results import _safe_tenant, result_path_for

    monkeypatch.setattr(settings, "job_result_dir", str(tmp_path / "results"))
    assert _safe_tenant("..") == "_default"
    assert "." not in _safe_tenant("a.b")
    p = result_path_for("jid1", "..")
    assert p is not None
    assert ".." not in p.parts


def test_fire_webhook_posts_json(monkeypatch):
    from unittest.mock import patch

    from aduport.config import settings

    monkeypatch.setattr(settings, "webhook_enabled", True)
    monkeypatch.setattr("aduport.core.webhooks.is_safe_url", lambda u: True)

    # P1-1: delivery goes through the IP-pinned POST helper (no second DNS
    # resolution on the send path), so assert against that seam instead of httpx.
    with patch("aduport.core.webhooks.post_json_pinned", return_value=204) as post:
        fire_job_webhook({"id": "j1", "status": "done"}, "https://example.com/hook")
        post.assert_called_once()
        args, kwargs = post.call_args
    assert args[0] == "https://example.com/hook"
    assert args[1]["event"] == "job.finished"
    assert "timeout" in kwargs


def test_fire_webhook_redacts_and_signs(monkeypatch):
    from unittest.mock import patch

    from aduport.config import settings
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "webhook_enabled", True)
    monkeypatch.setattr(settings, "webhook_retries", 0)
    monkeypatch.setattr(settings, "webhook_secret", SecretStr("test-secret-key"))
    monkeypatch.setattr("aduport.core.webhooks.is_safe_url", lambda u: True)
    with patch("aduport.core.webhooks.post_json_pinned", return_value=200) as post:
        fire_job_webhook(
            {
                "id": "j2",
                "status": "done",
                "access_token": "sekrit",
                "result_path": "/data/x.bin",
                "tenant_id": "t1",
            },
            "https://example.com/hook",
        )
        body = post.call_args.args[1]
        headers = post.call_args.kwargs.get("extra_headers") or {}
    assert "access_token" not in body["job"]
    assert "result_path" not in body["job"]
    assert "tenant_id" not in body["job"]
    assert headers.get("X-AduPort-Signature", "").startswith("sha256=")


def test_fire_webhook_rag_result_format_hint(monkeypatch):
    from unittest.mock import patch

    from aduport.config import settings

    monkeypatch.setattr(settings, "webhook_enabled", True)
    monkeypatch.setattr("aduport.core.webhooks.is_safe_url", lambda u: True)
    with patch("aduport.core.webhooks.post_json_pinned", return_value=204) as post:
        fire_job_webhook(
            {"id": "j-rag", "status": "done", "target_format": "rag"},
            "https://example.com/hook",
        )
        body = post.call_args.args[1]
    assert body["result_format"]["schema"] == "aduport.rag_elements.v1"
    assert body["result_format"]["schema_version"] == "1.1"