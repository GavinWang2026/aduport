"""Constant-time secret comparison without length side-channels (report P3-3)."""

from __future__ import annotations

import hashlib
import hmac


def secure_compare(a: str, b: str) -> bool:
    """Compare two strings via SHA-256 digests (fixed 32-byte compare)."""
    da = hashlib.sha256((a or "").encode("utf-8")).digest()
    db = hashlib.sha256((b or "").encode("utf-8")).digest()
    return hmac.compare_digest(da, db)
