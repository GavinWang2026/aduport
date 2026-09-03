"""Shared pytest fixtures — keep local suite runnable without API keys."""

from __future__ import annotations

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def _allow_anonymous_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production default is require_auth=True; tests open the gate unless they assert auth."""
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "job_token_required", False)
    monkeypatch.setattr(settings, "native_required", False)


@pytest.fixture
def ab_benchmark(monkeypatch: pytest.MonkeyPatch):
    """enterprise/AB_TEST_CONTRACT.md — prerequisite ① for A/B behavior compare.

    Callers must still size payloads above stream/spill thresholds (②) and
    exercise the real code path under test (③).
    """
    monkeypatch.setattr(settings, "cache_enabled", False)
    return settings
