"""Bounded zip member reads to mitigate zip bombs (T24 / P1 #20)."""

from __future__ import annotations

import zipfile
from contextlib import contextmanager
from contextvars import ContextVar
from typing import BinaryIO, Iterator

from app.config import settings
from app.core.exceptions import FileTooLargeError


def max_zip_inflate_bytes() -> int:
    """Cap inflated bytes across a document.

    v6/T1-#4: was ``base * 50`` (4.88 GiB at the default 100 MB upload cap),
    which let a 398 KB crafted docx expand to 400 MiB in resident memory.
    Real-world OOXML rarely exceeds 10:1, so 4x is still generous while
    cutting the worst-case blast radius by ~12x.
    """
    base = max(1, int(settings.max_upload_mb)) * 1024 * 1024
    return max(base * 4, 64 * 1024 * 1024)


# v6/T1-#4: a legitimately compressed OOXML part is well under 100:1. Anything
# past this is a zip bomb regardless of absolute size, so reject on ratio
# *before* allocating. This is what stops the 257:1 sample that a byte budget
# alone still admitted (400 MB inflated < 400 MB budget).
MAX_INFLATE_RATIO = 200
# v6/T1-#4: OOXML packages with tens of thousands of members are almost
# always zip-bomb / zip-slip scaffolding, not real documents.
MAX_ZIP_ENTRIES = 10_000

# Forward ref for ContextVar typing — class defined below.
_active_budget: ContextVar["ZipInflateBudget | None"] = ContextVar(
    "mdbridge_zip_budget", default=None
)


def _check_ratio(name: str, info: zipfile.ZipInfo) -> None:
    """Reject members whose advertised compression ratio looks weaponised."""
    if info.compress_size <= 0:
        return
    if info.file_size // info.compress_size > MAX_INFLATE_RATIO:
        raise FileTooLargeError(
            f"ZIP 压缩比异常（{name} ratio="
            f"{info.file_size // info.compress_size}:1 > {MAX_INFLATE_RATIO}:1）"
        )


def assert_zip_entry_budget(zf: zipfile.ZipFile, *, max_entries: int | None = None) -> None:
    """Reject archives with an absurd member count before walking them."""
    limit = MAX_ZIP_ENTRIES if max_entries is None else max_entries
    n = len(zf.namelist())
    if n > limit:
        raise FileTooLargeError(f"ZIP 条目过多（{n} > {limit}）")


def read_zip_member(
    zf: zipfile.ZipFile,
    name: str,
    *,
    max_bytes: int | None = None,
    chunk_size: int = 1024 * 1024,
) -> bytes:
    """Stream-read one zip member with an inflated-size ceiling.

    When inside :func:`zip_inflate_session`, all reads share one cumulative
    budget (N-01) instead of resetting to 4× upload per call.
    """
    budget = _active_budget.get()
    if budget is not None:
        remain = budget.remaining()
        limit = remain if max_bytes is None else min(max_bytes, remain)
    else:
        limit = max_bytes if max_bytes is not None else max_zip_inflate_bytes()
    if limit <= 0:
        raise FileTooLargeError(f"ZIP 解压预算已耗尽（{name}）")
    info = zf.getinfo(name)
    _check_ratio(name, info)
    if info.file_size > limit:
        raise FileTooLargeError(
            f"ZIP 成员过大（{name} uncompressed={info.file_size} > {limit}）"
        )
    out = bytearray()
    with zf.open(name, "r") as fh:  # type: ignore[assignment]
        stream: BinaryIO = fh  # type: ignore[assignment]
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            out.extend(chunk)
            if len(out) > limit:
                raise FileTooLargeError(
                    f"ZIP 解压超限（{name} > {limit} bytes）"
                )
    data = bytes(out)
    if budget is not None:
        budget.used += len(data)
    return data


class ZipInflateBudget:
    """Cumulative inflated-byte budget across many members of one archive."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = limit if limit is not None else max_zip_inflate_bytes()
        self.used = 0

    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def bind(self, zf: zipfile.ZipFile) -> "ZipInflateBudget":
        """Validate entry count once per archive before streaming members."""
        assert_zip_entry_budget(zf)
        return self

    def read(
        self,
        zf: zipfile.ZipFile,
        name: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> bytes:
        data = read_zip_member(
            zf, name, max_bytes=self.remaining(), chunk_size=chunk_size
        )
        # When a session is active, read_zip_member already charged ``used``.
        if _active_budget.get() is not self:
            self.used += len(data)
        return data


@contextmanager
def zip_inflate_session(limit: int | None = None) -> Iterator[ZipInflateBudget]:
    """Activate a cumulative inflate budget for nested ``read_zip_member`` calls."""
    budget = ZipInflateBudget(limit)
    token = _active_budget.set(budget)
    try:
        yield budget
    finally:
        _active_budget.reset(token)
