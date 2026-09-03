"""Multi-tenant API key registry (file / env JSON / compact map)."""

from __future__ import annotations

import hmac
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import settings

_log = logging.getLogger("mdbridge.tenants")

# Soft TTL so key rotation / file edits pick up without process restart (P1 #19)
_CACHE_TTL_SECONDS = 30.0
_cached_keys: tuple["TenantKey", ...] | None = None
_cached_at: float = 0.0
_cached_mtime: float | None = None
_cached_raw: str | None = None


@dataclass(frozen=True)
class TenantKey:
    tenant_id: str
    api_key: str
    rate_limit: int | None = None  # None → use global settings.rate_limit_per_minute
    daily_quota: int | None = None  # conversions per UTC day; None = unlimited


def _parse_compact(raw: str) -> list[TenantKey]:
    """Parse ``tenant:key,tenant2:key2`` (keys may contain ``:`` after first)."""
    out: list[TenantKey] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tid, key = part.split(":", 1)
        tid, key = tid.strip(), key.strip()
        if tid and key:
            out.append(TenantKey(tenant_id=tid, api_key=key))
    return out


def _parse_json_list(data: object) -> list[TenantKey]:
    if not isinstance(data, list):
        return []
    out: list[TenantKey] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tenant_id") or item.get("id") or "").strip()
        key = str(item.get("api_key") or item.get("key") or "").strip()
        if not tid or not key:
            continue
        rl = item.get("rate_limit")
        rate = int(rl) if rl is not None and str(rl).strip() != "" else None
        dq = item.get("daily_quota")
        quota = int(dq) if dq is not None and str(dq).strip() != "" else None
        out.append(TenantKey(tenant_id=tid, api_key=key, rate_limit=rate, daily_quota=quota))
    return out


def _load_keys_uncached() -> tuple[TenantKey, ...]:
    raw = (settings.api_keys or "").strip()
    keys: list[TenantKey] = []

    if raw:
        path = Path(raw).expanduser()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                keys = _parse_json_list(data)
            except (OSError, json.JSONDecodeError) as exc:
                _log.warning("failed to load api_keys file %s: %s", path, exc)
        elif raw.startswith("["):
            try:
                keys = _parse_json_list(json.loads(raw))
            except json.JSONDecodeError as exc:
                _log.warning("invalid api_keys JSON: %s", exc)
        else:
            keys = _parse_compact(raw)

    # Legacy single key → default tenant
    single = (settings.api_key or "").strip()
    if single and not any(hmac.compare_digest(k.api_key, single) for k in keys):
        keys.insert(0, TenantKey(tenant_id="default", api_key=single))

    return tuple(keys)


def load_tenant_keys() -> tuple[TenantKey, ...]:
    global _cached_keys, _cached_at, _cached_mtime, _cached_raw
    now = time.monotonic()
    raw = (settings.api_keys or "").strip()
    path = Path(raw).expanduser() if raw else None
    mtime: float | None = None
    if path is not None and path.is_file():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None

    fresh = (
        _cached_keys is not None
        and (now - _cached_at) < _CACHE_TTL_SECONDS
        and _cached_raw == raw
        and _cached_mtime == mtime
    )
    if fresh:
        return _cached_keys

    keys = _load_keys_uncached()
    _cached_keys = keys
    _cached_at = now
    _cached_mtime = mtime
    _cached_raw = raw
    return keys


def clear_tenant_cache() -> None:
    global _cached_keys, _cached_at, _cached_mtime, _cached_raw
    _cached_keys = None
    _cached_at = 0.0
    _cached_mtime = None
    _cached_raw = None


def find_tenant_by_key(provided: str) -> TenantKey | None:
    if not provided:
        return None
    hit: TenantKey | None = None
    # Constant-time: always scan all keys (no early return on match)
    for item in load_tenant_keys():
        if hmac.compare_digest(item.api_key, provided):
            hit = item
    return hit


def tenants_configured() -> int:
    return len(load_tenant_keys())
