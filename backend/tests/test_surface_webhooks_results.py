"""Surface gaps: webhooks + job result disk persistence."""

from __future__ import annotations

import pytest

from app.core.job_results import delete_job_result, load_job_result, save_job_result
from app.core.webhooks import fire_job_webhook, validate_callback_url


def test_validate_callback_url_ok():
    assert validate_callback_url("https://hooks.example/md") == "https://hooks.example/md"
    assert validate_callback_url("  ") is None
    assert validate_callback_url(None) is None


def test_validate_callback_url_rejects_bad_scheme():
    with pytest.raises(ValueError):
        validate_callback_url("ftp://x")
    with pytest.raises(ValueError):
        validate_callback_url("not-a-url")


def test_job_result_roundtrip(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "job_result_dir", str(tmp_path / "results"))
    jid = "abc123def"
    path = save_job_result(jid, b"hello-result")
    assert path
    assert load_job_result(jid) == b"hello-result"
    delete_job_result(jid)
    assert load_job_result(jid) is None


def test_fire_webhook_posts_json(monkeypatch):
    from unittest.mock import MagicMock, patch

    from app.config import settings

    monkeypatch.setattr(settings, "webhook_enabled", True)
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    with patch("app.core.webhooks.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.return_value = mock_resp
        fire_job_webhook({"id": "j1", "status": "done"}, "https://example.test/hook")
        client.post.assert_called_once()
        args, kwargs = client.post.call_args
        assert args[0] == "https://example.test/hook"
        assert kwargs["json"]["event"] == "job.finished"
