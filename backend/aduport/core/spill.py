"""Spill large byte payloads to temp files to reduce peak RAM (bottleneck 2)."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from aduport.config import settings

_SAFE_TENANT = re.compile(r"[^a-zA-Z0-9._-]+")


def tenant_spill_dir(tenant_id: str | None = None) -> Path | None:
    """Optional per-tenant subdirectory under system temp (0600 files inside)."""
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    # Hash prefix avoids collisions from aggressive sanitization/truncation (v13 P3-4).
    import hashlib

    digest = hashlib.sha256(tid.encode("utf-8")).hexdigest()[:16]
    safe = _SAFE_TENANT.sub("_", tid)[:32] or "anon"
    root = Path(tempfile.gettempdir()) / "aduport-spill" / f"{digest}-{safe}"
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


class SpillBuffer:
    """Hold bytes in memory below threshold; otherwise write to a 0600 temp file."""

    def __init__(
        self,
        data: bytes,
        *,
        prefix: str = "aduport-spill-",
        tenant_id: str | None = None,
    ) -> None:
        threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
        self._path: str | None = None
        self._mem: bytes | None
        self._tenant_id = tenant_id
        self.size = len(data)
        if threshold > 0 and len(data) >= threshold:
            spill_dir = tenant_spill_dir(tenant_id)
            fd, path = tempfile.mkstemp(
                prefix=prefix,
                suffix=".bin",
                dir=str(spill_dir) if spill_dir else None,
            )
            try:
                os.write(fd, data)
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            self._path = path
            self._mem = None
        else:
            self._mem = data

    @property
    def spilled(self) -> bool:
        return self._path is not None

    @property
    def path(self) -> str | None:
        return self._path

    def read(self) -> bytes:
        if self._mem is not None:
            return self._mem
        assert self._path
        return Path(self._path).read_bytes()

    def append(self, data: bytes) -> None:
        """Append bytes in O(len(data)) — keep spill file open for sequential writes."""
        if not data:
            return
        self.size += len(data)
        if self._mem is not None:
            threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
            combined = self._mem + data
            if threshold > 0 and len(combined) >= threshold:
                spill_dir = tenant_spill_dir(self._tenant_id)
                fd, path = tempfile.mkstemp(
                    prefix="aduport-spill-append-",
                    suffix=".bin",
                    dir=str(spill_dir) if spill_dir else None,
                )
                try:
                    os.write(fd, combined)
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
                self._path = path
                self._mem = None
            else:
                self._mem = combined
            return
        assert self._path
        with open(self._path, "ab") as f:
            f.write(data)

    def release(self) -> None:
        if self._path:
            try:
                os.unlink(self._path)
            except OSError:
                pass
            self._path = None
        self._mem = None

    @classmethod
    def from_path(cls, path: str, size: int) -> SpillBuffer:
        buf = object.__new__(cls)
        buf._path = path
        buf._mem = None
        buf._tenant_id = None
        buf.size = size
        return buf

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
