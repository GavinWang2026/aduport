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
    conversion_seconds: list[float] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    started_at: float = field(default_factory=time.time)

    def record_success(self, source: str, target: str, seconds: float) -> None:
        key = f"{source}_to_{target}"
        with self._lock:
            self.conversions_total[key] += 1
            self.conversion_seconds.append(seconds)
            if len(self.conversion_seconds) > 5000:
                self.conversion_seconds = self.conversion_seconds[-2500:]

    def record_failure(self, source: str, target: str) -> None:
        key = f"{source}_to_{target}"
        with self._lock:
            self.conversions_failed[key] += 1

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
            p95 = self._p95_unlocked()
            lines.append("# HELP mdbridge_conversion_latency_p95_seconds P95 latency")
            lines.append("# TYPE mdbridge_conversion_latency_p95_seconds gauge")
            lines.append(f"mdbridge_conversion_latency_p95_seconds {p95:.6f}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
