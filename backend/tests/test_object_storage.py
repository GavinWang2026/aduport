"""Tests for object storage archive + usage API."""

from __future__ import annotations

from pathlib import Path

from aduport.core.object_storage import archive_job_object, storage_info


def test_object_storage_file_archive(tmp_path: Path):
    uri = f"file://{tmp_path}"
    from aduport.config import settings

    settings.object_storage_uri = uri
    out = archive_job_object("job-abc", b"payload", tenant_id="acme")
    assert out and out.startswith("file://")
    path = Path(out.replace("file://", ""))
    assert path.read_bytes() == b"payload"
    assert "acme" in str(path)
    info = storage_info()
    assert info.get("enabled") is True
    assert info.get("backend") == "file"
