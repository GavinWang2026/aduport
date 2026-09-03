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
    conversions_failed_by_code: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conversions_by_tenant: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    conversions_by_importer: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    identity_passthrough: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    tenant_daily: dict[str, int] = field(default_factory=lambda: defaultdict(int))  # "{tid}:{YYYY-MM-DD}"
    anydoc_hits: int = 0
    anydoc_degrades: int = 0
    anydoc_hits_by_channel: dict[str, int] = field(default_factory=lambda: defaultdict(int))
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
                day = time.strftime("%Y-%m-%d", time.gmtime())
                self.tenant_daily[f"{tid}:{day}"] += 1
                self._prune_tenant_daily_unlocked(day)
            if importer:
                self.conversions_by_importer[importer] += 1
            self.conversion_seconds.append(seconds)
            if len(self.conversion_seconds) > 5000:
                self.conversion_seconds = self.conversion_seconds[-2500:]

    def record_identity_passthrough(self, fmt: str) -> None:
        """Same-format passthrough — tracked separately from real conversions."""
        key = (fmt or "").strip().lower() or "unknown"
        with self._lock:
            self.identity_passthrough[key] += 1

    def record_failure(
        self,
        source: str,
        target: str,
        *,
        tenant_id: str | None = None,
        code: str | None = None,
    ) -> None:
        key = f"{source}_to_{target}"
        tid = tenant_id
        if tid is None:
            try:
                from app.core.request_context import current_tenant_id

                tid = current_tenant_id()
            except Exception:
                tid = None
        err_code = (code or "conversion_error").strip() or "conversion_error"
        with self._lock:
            self.conversions_failed[key] += 1
            self.conversions_failed_by_code[err_code] += 1
            if tid:
                self.conversions_by_tenant[f"{tid}:failed"] += 1

    def record_anydoc_hit(self, channel: str = "model") -> None:
        """A conversion was actually performed by the anydoc Rust parser.

        channel: ``fast_md`` (to_markdown_bytes) or ``model`` (to_document).
        """
        ch = (channel or "model").strip() or "model"
        with self._lock:
            self.anydoc_hits += 1
            self.anydoc_hits_by_channel[ch] += 1

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
        xs = self.conversion_seconds
        # Approximate p95 without full sort on every health poll (P1 #14)
        n = len(xs)
        if n <= 64:
            ordered = sorted(xs)
            idx = min(n - 1, int(n * 0.95))
            return ordered[idx]
        # Sample every k-th sample + tail for large buffers
        step = max(1, n // 256)
        sample = xs[::step]
        if xs[-1] not in sample:
            sample.append(xs[-1])
        ordered = sorted(sample)
        idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return ordered[idx]

    def _prune_tenant_daily_unlocked(self, today: str) -> None:
        """Drop tenant_daily keys older than yesterday (UTC day roll)."""
        if len(self.tenant_daily) < 64:
            return
        drop = [
            k
            for k in self.tenant_daily
            if ":" in k and not k.endswith(f":{today}")
            and not k.endswith(
                f":{time.strftime('%Y-%m-%d', time.gmtime(time.time() - 86400))}"
            )
        ]
        for k in drop:
            self.tenant_daily.pop(k, None)

    def _share_unlocked(self) -> dict[str, float | None]:
        """Rust-channel ratios for dashboards (None when denominator is 0)."""
        hits = self.anydoc_hits
        degrades = self.anydoc_degrades
        designated = hits + degrades
        hit_rate = (hits / designated) if designated else None

        by_imp = self.conversions_by_importer
        total_imp = sum(by_imp.values())
        rust_imp = sum(v for k, v in by_imp.items() if str(k).startswith("anydoc"))
        channel_share = (rust_imp / total_imp) if total_imp else None

        return {
            "anydoc_hit_rate": hit_rate,
            "anydoc_channel_share": channel_share,
        }

    def tenant_daily_count(self, tenant_id: str) -> int:
        day = time.strftime("%Y-%m-%d", time.gmtime())
        key = f"{tenant_id}:{day}"
        with self._lock:
            return int(self.tenant_daily.get(key, 0))

    def alerts(self) -> list[dict]:
        """Lightweight alert hooks for ops dashboards (no push channel)."""
        with self._lock:
            return self._alerts_unlocked()

    def _alerts_unlocked(self) -> list[dict]:
        out: list[dict] = []
        ok = sum(self.conversions_total.values())
        fail = sum(self.conversions_failed.values())
        total = ok + fail
        if total >= 20 and fail / total >= 0.25:
            out.append(
                {
                    "level": "warn",
                    "code": "high_failure_rate",
                    "message": f"失败率 {fail}/{total}",
                }
            )
        p95 = self._p95_unlocked()
        if p95 >= 60:
            out.append(
                {
                    "level": "warn",
                    "code": "high_latency_p95",
                    "message": f"p95={p95:.1f}s",
                }
            )
        return out

    def snapshot(self) -> dict:
        with self._lock:
            shares = self._share_unlocked()
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "conversions_total": dict(self.conversions_total),
                "conversions_failed": dict(self.conversions_failed),
                "conversions_failed_by_code": dict(self.conversions_failed_by_code),
                "conversions_by_tenant": dict(self.conversions_by_tenant),
                "conversions_by_importer": dict(self.conversions_by_importer),
                "identity_passthrough": dict(self.identity_passthrough),
                "tenant_daily": dict(self.tenant_daily),
                "anydoc_hits": self.anydoc_hits,
                "anydoc_degrades": self.anydoc_degrades,
                "anydoc_hits_by_channel": dict(self.anydoc_hits_by_channel),
                "anydoc_hit_rate": shares["anydoc_hit_rate"],
                "anydoc_channel_share": shares["anydoc_channel_share"],
                "latency_p95_seconds": round(self._p95_unlocked(), 4),
                "samples": len(self.conversion_seconds),
                "alerts": self._alerts_unlocked(),
            }

    def _ocr_gauges(self) -> tuple[float, float]:
        """(ocr_enabled, ocr_ready) as 0/1 gauges for Prometheus dashboards."""
        try:
            from app.config import settings
            from app.importers.ocr_engine import list_ocr_engines

            enabled = 1.0 if settings.ocr_enabled else 0.0
            ready = 1.0 if list_ocr_engines().get("ready") else 0.0
            return enabled, ready
        except Exception:
            return 0.0, 0.0

    def prometheus_text(self) -> str:
        ocr_enabled, ocr_ready = self._ocr_gauges()
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
            lines.append(
                "# HELP mdbridge_conversions_failed_by_code Failed conversions by error code"
            )
            lines.append("# TYPE mdbridge_conversions_failed_by_code counter")
            for err_code, val in self.conversions_failed_by_code.items():
                safe = str(err_code).replace('"', "")
                lines.append(
                    f'mdbridge_conversions_failed_by_code{{code="{safe}"}} {val}'
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
                "# HELP mdbridge_identity_passthrough Same-format passthrough "
                "(excluded from conversions_total)"
            )
            lines.append("# TYPE mdbridge_identity_passthrough counter")
            for fmt, val in self.identity_passthrough.items():
                safe = str(fmt).replace('"', "")
                lines.append(
                    f'mdbridge_identity_passthrough{{format="{safe}"}} {val}'
                )
            lines.append(
                "# HELP mdbridge_anydoc_hits Conversions performed by the anydoc native parser"
            )
            lines.append("# TYPE mdbridge_anydoc_hits counter")
            lines.append(f"mdbridge_anydoc_hits {self.anydoc_hits}")
            for ch, val in self.anydoc_hits_by_channel.items():
                safe = str(ch).replace('"', "")
                lines.append(
                    f'mdbridge_anydoc_hits_by_channel{{channel="{safe}"}} {val}'
                )
            lines.append(
                "# HELP mdbridge_anydoc_degrades Conversions anydoc was designated for but fell back to native"
            )
            lines.append("# TYPE mdbridge_anydoc_degrades counter")
            lines.append(f"mdbridge_anydoc_degrades {self.anydoc_degrades}")
            shares = self._share_unlocked()
            lines.append(
                "# HELP mdbridge_anydoc_hit_rate hits/(hits+degrades); NaN if none designated"
            )
            lines.append("# TYPE mdbridge_anydoc_hit_rate gauge")
            hr = shares["anydoc_hit_rate"]
            lines.append(
                f"mdbridge_anydoc_hit_rate {hr if hr is not None else 'Nan'}"
            )
            lines.append(
                "# HELP mdbridge_anydoc_channel_share "
                "share of conversions_by_importer with importer starting with anydoc"
            )
            lines.append("# TYPE mdbridge_anydoc_channel_share gauge")
            cs = shares["anydoc_channel_share"]
            lines.append(
                f"mdbridge_anydoc_channel_share {cs if cs is not None else 'Nan'}"
            )
            p95 = self._p95_unlocked()
            lines.append("# HELP mdbridge_conversion_latency_p95_seconds P95 latency")
            lines.append("# TYPE mdbridge_conversion_latency_p95_seconds gauge")
            lines.append(f"mdbridge_conversion_latency_p95_seconds {p95:.6f}")
        lines.append("# HELP mdbridge_ocr_enabled OCR feature flag (1=on)")
        lines.append("# TYPE mdbridge_ocr_enabled gauge")
        lines.append(f"mdbridge_ocr_enabled {ocr_enabled:.0f}")
        lines.append("# HELP mdbridge_ocr_ready At least one OCR engine available")
        lines.append("# TYPE mdbridge_ocr_ready gauge")
        lines.append(f"mdbridge_ocr_ready {ocr_ready:.0f}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
