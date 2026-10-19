"""R-07 / R-05 / R-09 gates: ProcessPool defaults, Dockerfile pango, warmup keys."""

from __future__ import annotations

from pathlib import Path

from aduport.config import settings
from aduport.core import warmup as warmup_mod


_ROOT = Path(__file__).resolve().parents[2]


def test_process_pool_defaults_supersede_gil_thread_pool_narrative():
    """Audit-day GIL 0.90× was ThreadPool; production default is isolate ProcessPool."""
    assert settings.convert_isolate is True
    assert settings.convert_isolate_fork is False
    # 0 = auto CPU workers for long-lived pool
    assert int(settings.convert_pool_workers) == 0


def test_dockerfile_ships_pango_for_weasy():
    docker = (_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "libpango" in docker
    assert "libpangocairo" in docker or "pango" in docker.lower()
    slim = _ROOT / "backend" / "Dockerfile.slim"
    if slim.is_file():
        text = slim.read_text(encoding="utf-8")
        # slim may omit Weasy; full image must keep pango
        assert "libpango" in docker


def test_warmup_status_includes_isolate_and_pdf_keys(monkeypatch):
    monkeypatch.setattr(settings, "warmup_force", True)
    monkeypatch.setattr(settings, "convert_isolate", False)
    monkeypatch.setattr(warmup_mod, "_done", False)
    status = warmup_mod.warmup_sync()
    assert status.get("status") == "done"
    assert "weasyprint" in status
    assert "md_pdf" in status or "hot_paths" in status
    assert "pdf_simple_font" in status


def test_warmup_primes_isolate_pool_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "warmup_force", True)
    monkeypatch.setattr(settings, "convert_isolate", True)
    monkeypatch.setattr(settings, "convert_isolate_fork", False)
    monkeypatch.setattr(warmup_mod, "_done", False)

    calls: list[str] = []

    def _fake_pool():
        calls.append("pool")
        return object()

    def _fake_iso(*_a, **_k):
        calls.append("prime")
        return b"ok"

    monkeypatch.setattr(
        "aduport.core.convert_isolate.ensure_isolate_pool", _fake_pool
    )
    monkeypatch.setattr(
        "aduport.core.convert_isolate.convert_isolated", _fake_iso
    )
    # Avoid heavy real converts in this unit gate
    monkeypatch.setattr(
        "aduport.core.converter.convert",
        lambda *a, **k: b"<p>x</p>" if k.get("target_format") == "html" or (len(a) > 2 and a[2] == "html") else b"x",
    )
    status = warmup_mod.warmup_sync()
    assert status.get("isolate_pool") == "ok"
    assert status.get("isolate_prime") == "ok"
    assert "pool" in calls and "prime" in calls
