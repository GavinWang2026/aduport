"""Version single-source checks for PyPI packaging."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aduport import __version__
from main import aduport


def test_version_matches_pyproject():
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in text


def test_health_and_root_version():
    client = TestClient(app)
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["version"] == __version__
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["version"] == __version__
