"""Config validation + SecretStr helpers (v13 P3-5/P3-6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aduport.core.secrets import secret_plain


def test_secret_plain_handles_str_and_secretstr():
    from pydantic import SecretStr

    assert secret_plain(None) == ""
    assert secret_plain("") == ""
    assert secret_plain("plain") == "plain"
    assert secret_plain(SecretStr("s3cret")) == "s3cret"
    assert secret_plain(SecretStr("")) == ""


def test_settings_rejects_bad_pdf_page_size(monkeypatch):
    from aduport.config import Settings

    with pytest.raises(ValidationError):
        Settings(pdf_page_size="banana")  # type: ignore[arg-type]


def test_settings_rejects_negative_upload(monkeypatch):
    from aduport.config import Settings

    with pytest.raises(ValidationError):
        Settings(max_upload_mb=0)


def test_settings_secret_fields_redact_in_repr():
    from aduport.config import Settings

    s = Settings(api_key="super-secret-key-value")  # type: ignore[arg-type]
    text = repr(s)
    assert "super-secret-key-value" not in text
    assert secret_plain(s.api_key) == "super-secret-key-value"
