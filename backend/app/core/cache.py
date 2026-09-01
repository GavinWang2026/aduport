"""In-memory conversion result cache keyed by content hash (optional gzip)."""

from __future__ import annotations

import gzip
import hashlib
import threading
import time
from collections import OrderedDict

from app.config import settings

_GZIP_MAGIC = b"\x1f\x8b"


class ResultCache:
    def __init__(self, max_entries: int = 128, ttl_seconds: int = 3600) -> None:
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._data: OrderedDict[str, tuple[float, bytes, bool]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.bytes_stored = 0

    @staticmethod
    def make_key(source_bytes: bytes, source_format: str, target_format: str, extra: str = "") -> str:
        h = hashlib.sha256()
        h.update(source_format.encode())
        h.update(b"|")
        h.update(target_format.encode())
        h.update(b"|")
        h.update(extra.encode())
        h.update(b"|")
        h.update(source_bytes)
        return h.hexdigest()

    def get(self, key: str) -> bytes | None:
        if not settings.cache_enabled:
            return None
        with self._lock:
            item = self._data.get(key)
            if not item:
                self.misses += 1
                return None
            ts, value, compressed = item
            if time.time() - ts > self.ttl_seconds:
                self.bytes_stored = max(0, self.bytes_stored - len(value))
                del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            raw = value
        if compressed:
            return gzip.decompress(raw)
        return raw

    def put(self, key: str, value: bytes) -> None:
        if not settings.cache_enabled:
            return
        store = value
        compressed = False
        threshold = max(0, settings.cache_gzip_min_bytes)
        if settings.cache_gzip and threshold >= 0 and len(value) >= threshold:
            store = gzip.compress(value, compresslevel=6)
            compressed = True
        with self._lock:
            old = self._data.get(key)
            if old:
                self.bytes_stored = max(0, self.bytes_stored - len(old[1]))
            self._data[key] = (time.time(), store, compressed)
            self.bytes_stored += len(store)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                _, (_, blob, _) = self._data.popitem(last=False)
                self.bytes_stored = max(0, self.bytes_stored - len(blob))

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.bytes_stored = 0

    def stats(self) -> dict[str, int | float | bool]:
        with self._lock:
            return {
                "enabled": settings.cache_enabled,
                "gzip": settings.cache_gzip,
                "entries": len(self._data),
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / (self.hits + self.misses))
                if (self.hits + self.misses)
                else 0.0,
                "bytes_stored": self.bytes_stored,
            }


result_cache = ResultCache(
    max_entries=settings.cache_max_entries,
    ttl_seconds=settings.cache_ttl_seconds,
)
