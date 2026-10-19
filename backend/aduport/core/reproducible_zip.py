"""ZIP timestamps that do not change between identical exports.

DOCX and EPUB are ZIP files. ``ZipInfo.date_time`` defaults to the local
clock, so two exports of the same document differ at local-header offset 10.
``SOURCE_DATE_EPOCH`` overrides the stamp when it is a year the ZIP format
can store (1980 or later). Otherwise the stamp is the ZIP epoch.
"""

from __future__ import annotations

import os
import zipfile
from datetime import datetime, timezone
from io import BytesIO

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def zip_date_time() -> tuple[int, int, int, int, int, int]:
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        try:
            stamp = int(raw)
            dt = datetime.fromtimestamp(stamp, tz=timezone.utc)
            if dt.year >= 1980:
                return (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
        except (ValueError, OSError, OverflowError):
            pass
    return _ZIP_EPOCH


def stabilize_zip_timestamps(data: bytes) -> bytes:
    """Rewrite a ZIP so member order and bytes stay, and DOS times do not."""
    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    when = zip_date_time()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            payload = src.read(info.filename)
            new = zipfile.ZipInfo(filename=info.filename, date_time=when)
            new.compress_type = info.compress_type
            new.external_attr = info.external_attr
            new.create_system = info.create_system
            new.flag_bits = info.flag_bits & ~0x8
            dst.writestr(new, payload)
    return out.getvalue()
