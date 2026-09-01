"""Enterprise license unit tests."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.license import (
    LicenseError,
    assert_license_ok,
    issue_license,
    machine_id,
    parse_and_verify,
)


def test_machine_id_stable():
    a = machine_id()
    b = machine_id()
    assert a == b
    assert len(a) == 32


def test_issue_and_verify_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret")
    mid = machine_id()
    token = issue_license(machine_id_value=mid, expires="2099-12-31", seats=2, customer="Acme")
    info = parse_and_verify(token)
    assert info.valid
    assert info.reason == "ok"
    assert info.customer == "Acme"
    assert info.seats == 2
    assert info.expires == "2099-12-31"


def test_machine_mismatch(monkeypatch):
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret")
    token = issue_license(machine_id_value="ffffffffffffffffffffffffffffffff", expires="")
    info = parse_and_verify(token)
    assert not info.valid
    assert info.reason == "machine_mismatch"


def test_expired(monkeypatch):
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret")
    token = issue_license(machine_id_value=machine_id(), expires="2000-01-01")
    info = parse_and_verify(token)
    assert not info.valid
    assert info.reason == "expired"


def test_bad_signature(monkeypatch):
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret")
    token = issue_license(machine_id_value=machine_id(), expires="")
    monkeypatch.setattr(settings, "license_secret", "other-secret")
    info = parse_and_verify(token)
    assert not info.valid
    assert info.reason == "bad_signature"


def test_assert_required(monkeypatch):
    monkeypatch.setattr(settings, "license_required", True)
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret-long")
    monkeypatch.setattr(settings, "license_key", "")
    monkeypatch.setattr(settings, "license_file", "")
    with pytest.raises(LicenseError):
        assert_license_ok()


def test_assert_rejects_weak_secret(monkeypatch):
    monkeypatch.setattr(settings, "license_required", True)
    monkeypatch.setattr(settings, "license_secret", "change-me")
    monkeypatch.setattr(settings, "license_key", "dummy")
    with pytest.raises(LicenseError) as ei:
        assert_license_ok()
    assert ei.value.code == "license_secret_weak"


def test_assert_ok_when_required(monkeypatch):
    monkeypatch.setattr(settings, "license_secret", "unit-test-secret-long")
    monkeypatch.setattr(settings, "license_required", True)
    token = issue_license(machine_id_value=machine_id(), expires="2099-01-01")
    monkeypatch.setattr(settings, "license_key", token)
    info = assert_license_ok()
    assert info is not None and info.valid
