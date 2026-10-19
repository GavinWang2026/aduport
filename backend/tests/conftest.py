"""Shared pytest fixtures — keep local suite runnable without API keys."""

from __future__ import annotations

import pytest

from aduport.config import settings


@pytest.fixture(autouse=True)
def _allow_anonymous_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production default is require_auth=True; tests open the gate unless they assert auth.

    Do NOT force ``convert_isolate=False`` here (v15 P2): that zeroed coverage of
    the process-pool path and broke default-value gates. Instead reset any isolate
    pool after each test so cross-file pollution cannot hang TestClient.
    """
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "job_token_required", False)
    monkeypatch.setattr(settings, "native_required", False)
    yield
    try:
        from aduport.core import convert_isolate as iso

        with iso._pool_lock:
            iso._reset_pool_locked()
    except Exception:
        pass


@pytest.fixture
def ab_benchmark(monkeypatch: pytest.MonkeyPatch):
    """A/B behavior compare: disable cache; callers still size payloads above
    stream/spill thresholds and exercise the real code path under test.
    """
    monkeypatch.setattr(settings, "cache_enabled", False)
    return settings
