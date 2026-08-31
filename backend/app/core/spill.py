"""Spill large byte payloads to temp files to reduce peak RAM (bottleneck 2)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.config import settings


class SpillBuffer:
    """Hold bytes in memory below threshold; otherwise write to a 0600 temp file."""

    def __init__(self, data: bytes, *, prefix: str = "mdbridge-spill-") -> None:
        threshold = max(0, settings.spill_threshold_mb) * 1024 * 1024
        self._path: str | None = None
        self._mem: bytes | None
        self.size = len(data)
        if threshold > 0 and len(data) >= threshold:
            fd, path = tempfile.mkstemp(prefix=prefix, suffix=".bin")
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

    def read(self) -> bytes:
        if self._mem is not None:
            return self._mem
        assert self._path
        return Path(self._path).read_bytes()

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
        buf.size = size
        return buf

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
