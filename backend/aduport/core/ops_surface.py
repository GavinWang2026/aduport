"""Shared ops used by HTTP, CLI, and MCP so the four surfaces stay aligned."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def usage_snapshot() -> dict[str, Any]:
    import time

    from aduport.core.metrics import metrics

    snap = metrics.snapshot()
    tid = "default"
    daily = snap.get("tenant_daily") or {}
    today = time.strftime("%Y-%m-%d", time.gmtime())
    by_tenant = snap.get("conversions_by_tenant") or {}
    return {
        "tenant_id": tid,
        "conversions_today": int(daily.get(f"{tid}:{today}", 0)),
        "conversions_total": int(by_tenant.get(tid, 0)),
        "conversions_failed": int(by_tenant.get(f"{tid}:failed", 0)),
    }


def license_snapshot(token: str = "") -> dict[str, Any]:
    from aduport.config import settings
    from aduport.core.license import machine_id, parse_and_verify
    from aduport.core.secrets import secret_plain

    raw = (token or secret_plain(settings.license_key) or "").strip()
    if not raw and settings.license_file:
        path = Path(settings.license_file)
        if path.is_file():
            raw = path.read_text(encoding="utf-8").strip()
    info = parse_and_verify(raw)
    return {
        "host_machine_id": machine_id(),
        "valid": bool(info.valid),
        "reason": info.reason,
        "customer": info.customer,
        "expires": info.expires or "",
        "seats": info.seats,
    }


def jobs_recent(limit: int = 20) -> dict[str, Any]:
    from aduport.core.jobs import job_store

    jobs = job_store.list_recent(max(1, min(100, int(limit))))
    return {"jobs": [job.to_dict(include_token=False) for job in jobs]}


def watch_once(
    input_dir: str,
    output_dir: str,
    target_format: str,
    source_format: str = "",
) -> dict[str, Any]:
    from aduport.core.watch import scan_inbox
    from aduport.mcp_server import _assert_path_allowed

    inbox = Path(input_dir).expanduser().resolve()
    out = Path(output_dir).expanduser().resolve()
    _assert_path_allowed(inbox)
    _assert_path_allowed(out)
    if not inbox.is_dir():
        raise FileNotFoundError(f"not a directory: {inbox}")
    out.mkdir(parents=True, exist_ok=True)
    written = scan_inbox(
        inbox,
        out,
        target_format,
        source_format=source_format or "",
    )
    return {"ok": True, "written": [str(path) for path in written]}
