"""Aggregate child-process side effects back into the API process (report P2-11).

Process-pool / Popen workers keep private ``metrics`` copies and would otherwise
write audit JSONL without coordination. Workers return a compact payload; the
parent merges counters and appends audit lines under a file lock.
"""

from __future__ import annotations

from typing import Any


def capture_worker_effects_start() -> dict[str, Any]:
    """Snapshot metrics + open an audit event buffer for one convert job."""
    from aduport.core.audit import begin_audit_buffer
    from aduport.core.metrics import metrics

    token = begin_audit_buffer()
    return {"metrics_before": metrics.counter_snapshot(), "audit_token": token}


def capture_worker_effects_finish(state: dict[str, Any]) -> dict[str, Any]:
    """Build the IPC payload fragment after convert completes (ok or error)."""
    from aduport.core.audit import take_audit_buffer
    from aduport.core.metrics import metrics
    from aduport.exporters.export_warnings import get_last_export_warnings

    before = state.get("metrics_before") or {}
    after = metrics.counter_snapshot()
    return {
        "export_warnings": get_last_export_warnings(),
        "metrics_delta": metrics.delta(before, after),
        "audit_events": take_audit_buffer(state.get("audit_token")),
    }


def apply_worker_effects(payload: dict[str, Any] | None) -> None:
    """Merge worker metrics/audit/warnings into the parent process."""
    if not payload:
        return
    from aduport.core.audit import replay_audit_events
    from aduport.core.metrics import metrics
    from aduport.exporters.export_warnings import set_last_export_warnings

    warns = payload.get("export_warnings")
    if isinstance(warns, list):
        set_last_export_warnings([str(w) for w in warns if str(w).strip()])
    delta = payload.get("metrics_delta")
    if isinstance(delta, dict) and delta:
        metrics.apply_delta(delta)
    events = payload.get("audit_events")
    if isinstance(events, list) and events:
        replay_audit_events(events)
