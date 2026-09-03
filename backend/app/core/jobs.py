"""In-memory async conversion job queue with progress polling + disk spill."""

from __future__ import annotations

import contextvars
import hmac
import logging
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import settings
from app.core.convert_isolate import convert_isolated
from app.core.exceptions import MdBridgeError, MemoryBudgetError
from app.core.job_persist import load_job_snapshot, load_recent_snapshots, save_job_snapshot
from app.core.spill import SpillBuffer

_log = logging.getLogger("mdbridge.jobs")


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


@dataclass
class Job:
    id: str
    filename: str
    source_format: str
    target_format: str
    status: JobStatus = JobStatus.queued
    progress: float = 0.0
    error: str | None = None
    code: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result_bytes: bytes | None = None
    result_spill: SpillBuffer | None = None
    result_filename: str | None = None
    result_path: str | None = None
    bytes_in: int = 0
    tenant_id: str | None = None
    callback_url: str | None = None
    # One-time-share secret for GET /jobs/{id} and /result (not listed in list_jobs).
    access_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    # Bytes currently counted toward memory_budget_mb (input while running, result while retained).
    memory_charge: int = 0

    def read_result(self) -> bytes | None:
        if self.result_spill is not None:
            return self.result_spill.read()
        if self.result_bytes is not None:
            return self.result_bytes
        if self.result_path or self.id:
            from app.core.job_results import load_job_result

            data = load_job_result(self.id)
            if data is not None:
                self.result_bytes = data
            return data
        return None

    def to_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        out_len = 0
        if self.result_spill is not None:
            out_len = self.result_spill.size
        elif self.result_bytes is not None:
            out_len = len(self.result_bytes)
        payload: dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "source_format": self.source_format,
            "target_format": self.target_format,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "error": self.error,
            "code": self.code,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "bytes_in": self.bytes_in,
            "bytes_out": out_len,
            "result_filename": self.result_filename,
            "result_persisted": bool(self.result_path),
            "tenant_id": self.tenant_id,
        }
        if include_token and self.access_token:
            payload["access_token"] = self.access_token
        return payload

    def token_ok(self, provided: str | None) -> bool:
        expected = (self.access_token or "").strip()
        got = (provided or "").strip()
        if not expected:
            return True
        if not got:
            return False
        return hmac.compare_digest(expected, got)


class JobStore:
    """Thread-safe job registry with bounded concurrency and memory budget."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._inflight_bytes = 0
        # Align with convert_pool CPU sizing — batch_workers alone capped at 3 and
        # left multi-core idle while isolate ProcessPool sat waiting for submits.
        import os

        auto = max(4, min(16, os.cpu_count() or 4))
        workers = int(settings.convert_pool_workers or auto)
        self._sem = threading.Semaphore(max(1, workers))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, workers),
            thread_name_prefix="mdbridge-job",
        )

    def submit(
        self,
        *,
        content: bytes,
        filename: str,
        source_format: str,
        target_format: str,
        pdf_font_family: str | None = None,
        pdf_custom_css: str | None = None,
        pdf_page_size: str | None = None,
        pdf_orientation: str | None = None,
        pdf_header: str | None = None,
        pdf_footer: str | None = None,
        pdf_page_numbers: bool | None = None,
        pdf_toc: bool | None = None,
        docx_theme: str | None = None,
        callback_url: str | None = None,
        client: str = "",
        result_filename: str = "",
        tenant_id: str | None = None,
    ) -> Job:
        from app.core.webhooks import validate_callback_url

        tenant = (tenant_id or "").strip() or None
        cb = validate_callback_url(callback_url)
        spill_prefix = f"mdbridge-job-in-{(tenant or 'anon')[:24]}-"
        spill = SpillBuffer(content, prefix=spill_prefix)
        # Count logical payload size (spilled jobs still consume worker RAM when read)
        charge = max(spill.size, len(content))
        budget = settings.memory_budget_mb * 1024 * 1024
        queue_max = max(1, int(getattr(settings, "job_queue_max", 64) or 64))
        with self._lock:
            active = sum(
                1
                for j in self._jobs.values()
                if j.status in (JobStatus.queued, JobStatus.running)
            )
            if active >= queue_max:
                spill.release()
                raise MemoryBudgetError(
                    f"任务队列已满（{active}/{queue_max}），请稍后重试",
                    code="job_queue_full",
                )
            if budget > 0 and self._inflight_bytes + charge > budget:
                spill.release()
                raise MemoryBudgetError(
                    f"内存预算不足：当前占用 {self._inflight_bytes} bytes，"
                    f"限制 {settings.memory_budget_mb} MB"
                )
            job = Job(
                id=uuid.uuid4().hex,
                filename=filename,
                source_format=source_format,
                target_format=target_format,
                bytes_in=len(content),
                result_filename=result_filename or filename,
                tenant_id=tenant,
                callback_url=cb,
            )
            self._jobs[job.id] = job
            job.memory_charge = charge
            self._inflight_bytes += charge
            self._prune_unlocked()

        opts = {
            "pdf_font_family": pdf_font_family,
            "pdf_custom_css": pdf_custom_css,
            "pdf_page_size": pdf_page_size,
            "pdf_orientation": pdf_orientation,
            "pdf_header": pdf_header,
            "pdf_footer": pdf_footer,
            "pdf_page_numbers": pdf_page_numbers,
            "pdf_toc": pdf_toc,
        }
        if docx_theme:
            opts["docx_theme"] = docx_theme
        # Propagate request ContextVars (tenant / audit) into worker thread
        ctx = contextvars.copy_context()
        self._executor.submit(
            ctx.run,
            self._run,
            job.id,
            spill,
            charge,
            opts,
            client,
        )
        return job

    def get(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        enforce_tenant: bool = True,
    ) -> Job | None:
        with self._lock:
            hit = self._jobs.get(job_id)
            if hit:
                if enforce_tenant and not self._tenant_allows(hit.tenant_id, tenant_id):
                    return None
                return hit
        snap = load_job_snapshot(job_id)
        if not snap:
            return None
        snap_tenant = snap.get("tenant_id")
        job_tenant = str(snap_tenant) if snap_tenant else None
        if enforce_tenant and not self._tenant_allows(job_tenant, tenant_id):
            return None
        # Reconstruct finished job; result bytes load lazily via read_result()
        from app.core.job_results import result_path_for

        rpath = snap.get("result_path")
        if not rpath and snap.get("result_persisted"):
            rp = result_path_for(str(snap["id"]))
            rpath = str(rp) if rp and rp.is_file() else None
        job = Job(
            id=snap["id"],
            filename=snap.get("filename") or "",
            source_format=snap.get("source_format") or "",
            target_format=snap.get("target_format") or "",
            status=JobStatus(snap.get("status") or "error"),
            progress=float(snap.get("progress") or 1.0),
            error=snap.get("error"),
            code=snap.get("code"),
            created_at=float(snap.get("created_at") or time.time()),
            finished_at=snap.get("finished_at"),
            result_filename=snap.get("result_filename"),
            result_path=rpath,
            bytes_in=int(snap.get("bytes_in") or 0),
            tenant_id=job_tenant,
            callback_url=snap.get("callback_url"),
            access_token=str(snap.get("access_token") or ""),
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def list_recent(
        self, limit: int = 50, *, tenant_id: str | None = None
    ) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            mem = [
                j
                for j in jobs
                if self._tenant_allows(j.tenant_id, tenant_id)
            ][:limit]
            have = {j.id for j in mem}
        if len(mem) >= limit:
            return mem
        for snap in load_recent_snapshots(limit * 2):
            jid = snap.get("id")
            if not jid or jid in have:
                continue
            snap_tenant = snap.get("tenant_id")
            if not self._tenant_allows(
                str(snap_tenant) if snap_tenant else None, tenant_id
            ):
                continue
            restored = self.get(str(jid), tenant_id=tenant_id)
            if restored:
                mem.append(restored)
                have.add(restored.id)
            if len(mem) >= limit:
                break
        return sorted(mem, key=lambda j: j.created_at, reverse=True)[:limit]

    def read_result_safe(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
    ) -> tuple[Job | None, bytes | None]:
        """Load job + result bytes under the store lock (avoids prune race)."""
        with self._lock:
            hit = self._jobs.get(job_id)
            if hit:
                if not self._tenant_allows(hit.tenant_id, tenant_id):
                    return None, None
                try:
                    return hit, hit.read_result()
                except FileNotFoundError:
                    return hit, None
        job = self.get(job_id, tenant_id=tenant_id)
        if not job:
            return None, None
        with self._lock:
            # Re-check membership; may have been pruned between get and lock
            live = self._jobs.get(job_id) or job
            try:
                return live, live.read_result()
            except FileNotFoundError:
                return live, None

    @staticmethod
    def _tenant_allows(job_tenant: str | None, viewer: str | None) -> bool:
        """When viewer is set (API key tenant), only show that tenant's jobs.

        Anonymous / unset viewer sees only jobs without a tenant_id (legacy/dev).
        """
        v = (viewer or "").strip() or None
        j = (job_tenant or "").strip() or None
        if v is None:
            return j is None
        return j == v

    def _run(
        self,
        job_id: str,
        spill: SpillBuffer,
        charge: int,
        pdf_opts: dict[str, Any],
        client: str,
    ) -> None:
        job = self.get(job_id, enforce_tenant=False)
        if not job:
            spill.release()
            return
        self._sem.acquire()
        try:
            with self._lock:
                job.status = JobStatus.running
                job.progress = 0.1
            try:
                content = spill.read()
                out = convert_isolated(
                    content,
                    job.source_format,
                    job.target_format,
                    filename=job.filename,
                    client=client,
                    **pdf_opts,
                )
                from app.core.job_results import save_job_result
                from app.core.webhooks import fire_job_webhook

                persisted = save_job_result(job.id, out)
                from app.core.object_storage import archive_job_object

                archive_uri = archive_job_object(job.id, out)
                out_spill = SpillBuffer(out, prefix="mdbridge-job-out-")
                with self._lock:
                    if out_spill.spilled:
                        job.result_spill = out_spill
                        job.result_bytes = None
                    else:
                        job.result_bytes = out
                        out_spill.release()
                    job.result_path = persisted or archive_uri
                    job.status = JobStatus.done
                    job.progress = 1.0
                    job.finished_at = time.time()
                payload = job.to_dict()
                payload["access_token"] = job.access_token
                if persisted:
                    payload["result_path"] = persisted
                if archive_uri:
                    payload["object_archive_uri"] = archive_uri
                # Post-done side effects must not roll status back to error
                try:
                    save_job_snapshot(job.id, payload)
                except Exception as snap_exc:
                    _log.warning("save_job_snapshot failed for %s: %s", job.id, snap_exc)
                try:
                    fire_job_webhook(payload, job.callback_url)
                except Exception as wh_exc:
                    _log.warning("fire_job_webhook failed for %s: %s", job.id, wh_exc)
            except MdBridgeError as exc:
                from app.core.webhooks import fire_job_webhook

                with self._lock:
                    if job.status == JobStatus.done:
                        _log.warning("ignoring error after done for %s: %s", job.id, exc)
                    else:
                        job.status = JobStatus.error
                        job.error = exc.message
                        job.code = exc.code
                        job.progress = 1.0
                        job.finished_at = time.time()
                        payload = job.to_dict()
                        payload["access_token"] = job.access_token
                        try:
                            save_job_snapshot(job.id, payload)
                        except Exception:
                            pass
                        try:
                            fire_job_webhook(payload, job.callback_url)
                        except Exception:
                            pass
            except Exception as exc:
                from app.core.webhooks import fire_job_webhook

                with self._lock:
                    if job.status == JobStatus.done:
                        _log.warning("ignoring error after done for %s: %s", job.id, exc)
                    else:
                        job.status = JobStatus.error
                        job.error = str(exc) if settings.debug else "转换失败"
                        job.code = "conversion_error"
                        job.progress = 1.0
                        job.finished_at = time.time()
                        payload = job.to_dict()
                        payload["access_token"] = job.access_token
                        try:
                            save_job_snapshot(job.id, payload)
                        except Exception:
                            pass
                        try:
                            fire_job_webhook(payload, job.callback_url)
                        except Exception:
                            pass
        finally:
            spill.release()
            self._sem.release()
            with self._lock:
                # Drop input charge; retain in-RAM result size until prune (T19).
                prev = charge
                retain = 0
                live = self._jobs.get(job_id)
                if live is not None and live.status == JobStatus.done:
                    if live.result_bytes is not None:
                        retain = len(live.result_bytes)
                    # Disk-spilled / archived results do not count toward RAM budget
                self._inflight_bytes = max(0, self._inflight_bytes - prev)
                if live is not None:
                    if retain:
                        self._inflight_bytes += retain
                    live.memory_charge = retain

    def reserve_bytes(self, nbytes: int) -> int:
        """Reserve sync-convert budget against the shared memory ceiling (T19)."""
        n = max(0, int(nbytes))
        if n <= 0:
            return 0
        budget = settings.memory_budget_mb * 1024 * 1024
        with self._lock:
            if budget > 0 and self._inflight_bytes + n > budget:
                raise MemoryBudgetError(
                    f"内存预算不足：当前占用 {self._inflight_bytes} bytes，"
                    f"限制 {settings.memory_budget_mb} MB"
                )
            self._inflight_bytes += n
        return n

    def release_bytes(self, nbytes: int) -> None:
        with self._lock:
            self._inflight_bytes = max(0, self._inflight_bytes - max(0, int(nbytes)))

    def _prune_unlocked(self) -> None:
        from app.core.job_results import delete_job_result

        ttl = settings.job_ttl_seconds
        now = time.time()
        expired = [
            jid
            for jid, j in self._jobs.items()
            if j.finished_at and now - j.finished_at > ttl
        ]
        for jid in expired:
            job = self._jobs.pop(jid, None)
            if job:
                self._inflight_bytes = max(0, self._inflight_bytes - int(job.memory_charge or 0))
                job.memory_charge = 0
                if job.result_spill:
                    job.result_spill.release()
                delete_job_result(job.id)
        max_jobs = settings.job_max_entries
        if len(self._jobs) > max_jobs:
            finished = sorted(
                (j for j in self._jobs.values() if j.finished_at),
                key=lambda j: j.finished_at or 0,
            )
            overflow = len(self._jobs) - max_jobs
            for job in finished[:overflow]:
                self._inflight_bytes = max(0, self._inflight_bytes - int(job.memory_charge or 0))
                job.memory_charge = 0
                if job.result_spill:
                    job.result_spill.release()
                delete_job_result(job.id)
                self._jobs.pop(job.id, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            by_status = {s.value: 0 for s in JobStatus}
            for j in self._jobs.values():
                by_status[j.status.value] = by_status.get(j.status.value, 0) + 1
            return {
                "total": len(self._jobs),
                "inflight_bytes": self._inflight_bytes,
                "queue_max": max(1, int(getattr(settings, "job_queue_max", 64) or 64)),
                **by_status,
            }


job_store = JobStore()
