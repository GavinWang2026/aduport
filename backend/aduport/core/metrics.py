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
    # In-process convert threads left alive after job_timeout (daemon; prefer isolate).
    stuck_convert_threads: int = 0
    conversion_seconds: list[float] = field(default_factory=list)
    conversion_seconds_total: int = 0  # monotonic sample counter (survives ring trim)
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
                from aduport.core.request_context import current_tenant_id

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
            self.conversion_seconds_total += 1
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
                from aduport.core.request_context import current_tenant_id

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

    def record_stuck_convert_thread(self) -> None:
        """A convert thread was still alive after the job deadline (v7 §07 #5)."""
        with self._lock:
            self.stuck_convert_threads += 1

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
            from aduport.core.i18n_errors import translate_message

            note = f"失败率 {fail}/{total}"
            out.append(
                {
                    "level": "warn",
                    "code": "high_failure_rate",
                    "message": note,
                    "message_en": translate_message(note, "high_failure_rate"),
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
        if self.stuck_convert_threads > 0:
            out.append(
                {
                    "level": "warn",
                    "code": "stuck_convert_threads",
                    "message": (
                        f"stuck_convert_threads={self.stuck_convert_threads}; "
                        "prefer ADUPORT_CONVERT_ISOLATE"
                    ),
                }
            )
        return out

    def counter_snapshot(self) -> dict:
        """Picklable counter state for isolate worker deltas (report P2-11)."""
        with self._lock:
            return {
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
                "stuck_convert_threads": int(self.stuck_convert_threads),
                "conversion_seconds": list(self.conversion_seconds[-64:]),
                "conversion_seconds_total": int(self.conversion_seconds_total),
            }

    @staticmethod
    def delta(before: dict, after: dict) -> dict:
        """Return non-zero counter increments between two :meth:`counter_snapshot`s."""

        def _dict_delta(a: dict, b: dict) -> dict[str, int]:
            out: dict[str, int] = {}
            keys = set(a or ()) | set(b or ())
            for k in keys:
                d = int((b or {}).get(k, 0) or 0) - int((a or {}).get(k, 0) or 0)
                if d:
                    out[str(k)] = d
            return out

        before = before or {}
        after = after or {}
        before_secs = list(before.get("conversion_seconds") or [])
        after_secs = list(after.get("conversion_seconds") or [])
        before_total = int(before.get("conversion_seconds_total") or 0)
        after_total = int(after.get("conversion_seconds_total") or 0)
        # Prefer monotonic counter so a full 64-sample ring still yields new samples.
        if after_total > before_total:
            n_new = min(after_total - before_total, len(after_secs))
            new_secs = after_secs[-n_new:] if n_new > 0 else []
        elif len(after_secs) >= len(before_secs):
            new_secs = after_secs[len(before_secs) :]
        else:
            new_secs = after_secs
        return {
            "conversions_total": _dict_delta(
                before.get("conversions_total") or {}, after.get("conversions_total") or {}
            ),
            "conversions_failed": _dict_delta(
                before.get("conversions_failed") or {}, after.get("conversions_failed") or {}
            ),
            "conversions_failed_by_code": _dict_delta(
                before.get("conversions_failed_by_code") or {},
                after.get("conversions_failed_by_code") or {},
            ),
            "conversions_by_tenant": _dict_delta(
                before.get("conversions_by_tenant") or {},
                after.get("conversions_by_tenant") or {},
            ),
            "conversions_by_importer": _dict_delta(
                before.get("conversions_by_importer") or {},
                after.get("conversions_by_importer") or {},
            ),
            "identity_passthrough": _dict_delta(
                before.get("identity_passthrough") or {},
                after.get("identity_passthrough") or {},
            ),
            "tenant_daily": _dict_delta(
                before.get("tenant_daily") or {}, after.get("tenant_daily") or {}
            ),
            "anydoc_hits": int(after.get("anydoc_hits") or 0)
            - int(before.get("anydoc_hits") or 0),
            "anydoc_degrades": int(after.get("anydoc_degrades") or 0)
            - int(before.get("anydoc_degrades") or 0),
            "anydoc_hits_by_channel": _dict_delta(
                before.get("anydoc_hits_by_channel") or {},
                after.get("anydoc_hits_by_channel") or {},
            ),
            "stuck_convert_threads": int(after.get("stuck_convert_threads") or 0)
            - int(before.get("stuck_convert_threads") or 0),
            "conversion_seconds": [float(x) for x in new_secs],
        }

    def apply_delta(self, delta: dict) -> None:
        """Merge a worker :meth:`delta` into this (parent) metrics instance."""
        if not delta:
            return

        def _add_map(target: dict, src: dict | None) -> None:
            for k, v in (src or {}).items():
                target[str(k)] = int(target.get(str(k), 0) or 0) + int(v or 0)

        with self._lock:
            _add_map(self.conversions_total, delta.get("conversions_total"))
            _add_map(self.conversions_failed, delta.get("conversions_failed"))
            _add_map(self.conversions_failed_by_code, delta.get("conversions_failed_by_code"))
            _add_map(self.conversions_by_tenant, delta.get("conversions_by_tenant"))
            _add_map(self.conversions_by_importer, delta.get("conversions_by_importer"))
            _add_map(self.identity_passthrough, delta.get("identity_passthrough"))
            _add_map(self.tenant_daily, delta.get("tenant_daily"))
            self.anydoc_hits += int(delta.get("anydoc_hits") or 0)
            self.anydoc_degrades += int(delta.get("anydoc_degrades") or 0)
            _add_map(self.anydoc_hits_by_channel, delta.get("anydoc_hits_by_channel"))
            self.stuck_convert_threads += int(delta.get("stuck_convert_threads") or 0)
            for sec in delta.get("conversion_seconds") or []:
                try:
                    self.conversion_seconds.append(float(sec))
                    self.conversion_seconds_total += 1
                except (TypeError, ValueError):
                    continue
            if len(self.conversion_seconds) > 5000:
                self.conversion_seconds = self.conversion_seconds[-2500:]

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
                "stuck_convert_threads": int(self.stuck_convert_threads),
                "latency_p95_seconds": round(self._p95_unlocked(), 4),
                "samples": len(self.conversion_seconds),
                "alerts": self._alerts_unlocked(),
            }

    def _ocr_gauges(self) -> tuple[float, float]:
        """(ocr_enabled, ocr_ready) as 0/1 gauges for Prometheus dashboards."""
        try:
            from aduport.config import settings
            from aduport.importers.ocr_engine import list_ocr_engines

            enabled = 1.0 if settings.ocr_enabled else 0.0
            ready = 1.0 if list_ocr_engines().get("ready") else 0.0
            return enabled, ready
        except Exception:
            return 0.0, 0.0

    def _isolate_gauges(self) -> tuple[float, float, float]:
        """(enabled, started, workers) for the convert isolate pool."""
        try:
            from aduport.core.convert_isolate import isolate_pool_status

            status = isolate_pool_status()
            return (
                1.0 if status.get("enabled") else 0.0,
                1.0 if status.get("started") else 0.0,
                float(int(status.get("workers") or 0)),
            )
        except Exception:
            return 0.0, 0.0, 0.0

    def prometheus_text(self, *, include_tenants: bool = True) -> str:
        ocr_enabled, ocr_ready = self._ocr_gauges()
        iso_enabled, iso_started, iso_workers = self._isolate_gauges()
        lines = [
            "# HELP aduport_conversions_total Successful conversions by route",
            "# TYPE aduport_conversions_total counter",
        ]
        with self._lock:
            for key, val in self.conversions_total.items():
                src, _, tgt = key.partition("_to_")
                lines.append(
                    f'aduport_conversions_total{{source="{src}",target="{tgt}"}} {val}'
                )
            lines.append("# HELP aduport_conversions_failed Failed conversions by route")
            lines.append("# TYPE aduport_conversions_failed counter")
            for key, val in self.conversions_failed.items():
                src, _, tgt = key.partition("_to_")
                lines.append(
                    f'aduport_conversions_failed{{source="{src}",target="{tgt}"}} {val}'
                )
            lines.append(
                "# HELP aduport_conversions_failed_by_code Failed conversions by error code"
            )
            lines.append("# TYPE aduport_conversions_failed_by_code counter")
            for err_code, val in self.conversions_failed_by_code.items():
                safe = str(err_code).replace('"', "")
                lines.append(
                    f'aduport_conversions_failed_by_code{{code="{safe}"}} {val}'
                )
            if include_tenants:
                lines.append("# HELP aduport_conversions_by_tenant Tenant conversion counters")
                lines.append("# TYPE aduport_conversions_by_tenant counter")
                for tid, val in self.conversions_by_tenant.items():
                    safe = str(tid).replace('"', "")
                    lines.append(f'aduport_conversions_by_tenant{{tenant_id="{safe}"}} {val}')
            lines.append(
                "# HELP aduport_conversions_by_importer Successful conversions by import engine"
            )
            lines.append("# TYPE aduport_conversions_by_importer counter")
            for eng, val in self.conversions_by_importer.items():
                safe = str(eng).replace('"', "")
                lines.append(
                    f'aduport_conversions_by_importer{{importer="{safe}"}} {val}'
                )
            lines.append(
                "# HELP aduport_identity_passthrough Same-format passthrough "
                "(excluded from conversions_total)"
            )
            lines.append("# TYPE aduport_identity_passthrough counter")
            for fmt, val in self.identity_passthrough.items():
                safe = str(fmt).replace('"', "")
                lines.append(
                    f'aduport_identity_passthrough{{format="{safe}"}} {val}'
                )
            lines.append(
                "# HELP aduport_anydoc_hits Conversions performed by the anydoc native parser"
            )
            lines.append("# TYPE aduport_anydoc_hits counter")
            lines.append(f"aduport_anydoc_hits {self.anydoc_hits}")
            for ch, val in self.anydoc_hits_by_channel.items():
                safe = str(ch).replace('"', "")
                lines.append(
                    f'aduport_anydoc_hits_by_channel{{channel="{safe}"}} {val}'
                )
            lines.append(
                "# HELP aduport_anydoc_degrades Conversions anydoc was designated for but fell back to native"
            )
            lines.append("# TYPE aduport_anydoc_degrades counter")
            lines.append(f"aduport_anydoc_degrades {self.anydoc_degrades}")
            shares = self._share_unlocked()
            lines.append(
                "# HELP aduport_anydoc_hit_rate hits/(hits+degrades); NaN if none designated"
            )
            lines.append("# TYPE aduport_anydoc_hit_rate gauge")
            hr = shares["anydoc_hit_rate"]
            lines.append(
                f"aduport_anydoc_hit_rate {hr if hr is not None else 'Nan'}"
            )
            lines.append(
                "# HELP aduport_anydoc_channel_share "
                "share of conversions_by_importer with importer starting with anydoc"
            )
            lines.append("# TYPE aduport_anydoc_channel_share gauge")
            cs = shares["anydoc_channel_share"]
            lines.append(
                f"aduport_anydoc_channel_share {cs if cs is not None else 'Nan'}"
            )
            p95 = self._p95_unlocked()
            lines.append("# HELP aduport_conversion_latency_p95_seconds P95 latency")
            lines.append("# TYPE aduport_conversion_latency_p95_seconds gauge")
            lines.append(f"aduport_conversion_latency_p95_seconds {p95:.6f}")
            lines.append(
                "# HELP aduport_stuck_convert_threads "
                "Convert threads still alive after job_timeout "
                "(daemon; prefer ADUPORT_CONVERT_ISOLATE)"
            )
            lines.append("# TYPE aduport_stuck_convert_threads counter")
            lines.append(f"aduport_stuck_convert_threads {int(self.stuck_convert_threads)}")
        lines.append("# HELP aduport_ocr_enabled OCR feature flag (1=on)")
        lines.append("# TYPE aduport_ocr_enabled gauge")
        lines.append(f"aduport_ocr_enabled {ocr_enabled:.0f}")
        lines.append("# HELP aduport_ocr_ready At least one OCR engine available")
        lines.append("# TYPE aduport_ocr_ready gauge")
        lines.append(f"aduport_ocr_ready {ocr_ready:.0f}")
        lines.append(
            "# HELP aduport_convert_isolate_enabled "
            "ADUPORT_CONVERT_ISOLATE process pool configured (1=on)"
        )
        lines.append("# TYPE aduport_convert_isolate_enabled gauge")
        lines.append(f"aduport_convert_isolate_enabled {iso_enabled:.0f}")
        lines.append(
            "# HELP aduport_convert_isolate_started "
            "Long-lived convert isolate pool already started (1=yes)"
        )
        lines.append("# TYPE aduport_convert_isolate_started gauge")
        lines.append(f"aduport_convert_isolate_started {iso_started:.0f}")
        lines.append(
            "# HELP aduport_convert_isolate_workers "
            "Configured convert isolate worker count"
        )
        lines.append("# TYPE aduport_convert_isolate_workers gauge")
        lines.append(f"aduport_convert_isolate_workers {iso_workers:.0f}")
        return "\n".join(lines) + "\n"


metrics = Metrics()
