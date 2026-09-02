"""Target format breadth: every declared TARGET has a registered exporter."""

from __future__ import annotations

from app.core.converter import SUPPORTED_TARGET_FORMATS
from app.core.registry import ensure_builtin_plugins, registered_export_formats


def test_all_supported_targets_have_exporters():
    ensure_builtin_plugins()
    registered = set(registered_export_formats())
    missing = sorted(SUPPORTED_TARGET_FORMATS - registered)
    assert not missing, f"targets without exporter: {missing}"
    assert len(SUPPORTED_TARGET_FORMATS) == 16
