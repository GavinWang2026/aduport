"""Lightweight in-process metrics (Prometheus text exposition compatible)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Metrics:
    conversions_total: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conversions_failed: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conversions_by_tenant: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conversions_by_importer: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    anydoc_hits: int = 0
    anydoc_degrades: int = 0
    conversion_seconds: list[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.time)

    def record_success(
        self,
        source: str,
        target: str,
        seconds: float,
        *,
        tenant_id: str | None = None,
        importer: str = "",
    ) -> None:
        key = f"{source}_to_{target}"
        tid = tenant_id
        if tid is None:
            try:
                from app.core.request_context import current_tenant_id

                tid = current_tenant_id()
            except Exception:
                tid = None
        with self._lock:
            self.conversions_total[key] += 1
            if tid:
                self.conversions_by_tenant[tid] += 1
            if importer:
                self.conversions_by_importer[importer] += 1
            self.conversion_seconds.append(seconds)
            if len(self.conversion_seconds) > 5000:
                self.conversion_seconds = self.conversion_seconds[-2500:]

    def record_failure(self, source: str, target: str, *, tenant_id: str | None = None) -> None:
        key = f"{source}_to_{target}"
        tid = tenant_id
        if tid is None:
            try:
                from app.core.request_context import current_tenant_id

                tid = current_tenant_id()
            except Exception:
                tid = None
        with self._lock:
            self.conversions_failed[key] += 1
            if tid:
                self.conversions_by_tenant[f"{tid}:failed"] += 1

    def record_anydoc_hit(self) -> None:
        """A conversion was actually performed by the anydoc Rust parser."""
        with self._lock:
            self.anydoc_hits += 1

    def record_anydoc_degrade(self) -> None:
        """anydoc was the designated engine but fell back to a native importer."""
        with self._lock:
            self.anydoc_degrades += 1

    def p95_latency(self) -> float:
        with self._lock:
            return self._p95_unlocked()

    def _p95_unlocked(self) -> float:
        if not self.conversion_seconds:
            return 0.0
        xs = sorted(self.conversion_seconds)
        idx = min(len(xs) - 1, int(len(xs) * 0.95))
        return xs[idx]

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "conversions_total": dict(self.conversions_total),
                "conversions_failed": dict(self.conversions_failed),
                "conversions_by_tenant": dict(self.conversions_by_tenant),
                "conversions_by_importer": dict(self.conversions_by_importer),
                "anydoc_hits": self.anydoc_hits,
                "anydoc_degrades": self.anydoc_degrades,
                "latency_p95_seconds": round(self._p95_unlocked(), 4),
                "samples": len(self.conversion_seconds),
            }

    def prometheus_text(self) -> str:
        lines = [
            "# HELP mdbridge_conversions_total Successful conversions by route",
            "# TYPE mdbridge_conversions_total counter",
        ]
        with self._lock:
            for key, val in self.conversions_total.items():
                src, _, tgt = key.partition("_to_")
                lines.append(
                    f'mdbridge_conversions_total{{source="{src}",target="{tgt}"}} {val}'
                )
            lines.append("# HELP mdbridge_conversions_failed Failed conversions by route")
            lines.append("# TYPE mdbridge_conversions_failed counter")
            for key, val in self.conversions_failed.items():
                src, _, tgt = key.partition("_to_")
                lines.append(
                    f'mdbridge_conversions_failed{{source="{src}",target="{tgt}"}} {val}'
                )
            lines.append("# HELP mdbridge_conversions_by_tenant Tenant conversion counters")
            lines.append("# TYPE mdbridge_conversions_by_tenant counter")
            for tid, val in self.conversions_by_tenant.items():
                safe = str(tid).replace('"', "")
                lines.append(f'mdbridge_conversions_by_tenant{{tenant_id="{safe}"}} {val}')
            lines.append(
                "# HELP mdbridge_conversions_by_importer Successful conversions by import engine"
            )
            lines.append("# TYPE mdbridge_conversions_by_importer counter")
            for eng, val in self.conversions_by_importer.items():
                safe = str(eng).replace('"', "")
                lines.append(
                    f'mdbridge_conversions_by_importer{{importer="{safe}"}} {val}'
                )
            lines.append(
                "# HELP mdbridge_anydoc_hits Conversions performed by the anydoc native parser"
            )
            lines.append("# TYPE mdbridge_anydoc_hits counter")
            lines.append(f"mdbridge_anydoc_hits {self.anydoc_hits}")
            lines.append(
                "# HELP mdbridge_anydoc_degrades Conversions anydoc was designated for but fell back to native"
            )
            lines.append("# TYPE mdbridge_anydoc_degrades counter")
            lines.append(f"mdbridge_anydoc_degrades {self.anydoc_degrades}")
            p95 = self._p95_unlocked()
            lines.append("# HELP mdbridge_conversion_latency_p95_seconds P95 latency")
            lines.append("# TYPE mdbridge_conversion_latency_p95_seconds gauge")
            lines.append(f"mdbridge_conversion_latency_p95_seconds {p95:.6f}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
