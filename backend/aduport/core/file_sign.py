"""Detached HMAC-SHA256 signatures for local file integrity.

This is not an Acrobat certificate signature and it does not modify PDF bytes.
The caller supplies the key; the signature file stores only the algorithm,
a non-secret key id, and the MAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

ALG = "HMAC-SHA256"
_MIN_KEY_CHARS = 8


class FileSignError(ValueError):
    """Missing key or a signature file that is not ours."""


def _key_bytes(key: str) -> bytes:
    text = key or ""
    if len(text) < _MIN_KEY_CHARS:
        raise FileSignError(f"签名密钥至少 {_MIN_KEY_CHARS} 个字符")
    return text.encode("utf-8")


def sign_bytes(data: bytes, key: str, key_id: str = "") -> dict[str, str]:
    mac = hmac.new(_key_bytes(key), data, hashlib.sha256).hexdigest()
    return {"alg": ALG, "key_id": (key_id or "").strip(), "mac": mac}


def verify_bytes(data: bytes, key: str, mac: str) -> bool:
    expected = sign_bytes(data, key)["mac"]
    supplied = (mac or "").strip().lower()
    if len(supplied) != len(expected):
        return False
    return hmac.compare_digest(expected, supplied)


def dump_signature(payload: dict[str, str]) -> str:
    return json.dumps(
        {"alg": payload["alg"], "key_id": payload.get("key_id") or "", "mac": payload["mac"]},
        ensure_ascii=False,
        indent=2,
    )


def load_signature(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FileSignError("签名文件不是 JSON") from exc
    if not isinstance(data, dict) or data.get("alg") != ALG or not data.get("mac"):
        raise FileSignError("签名文件不是 HMAC-SHA256 分离签名")
    return data
