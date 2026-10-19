"""In-memory async conversion job queue with progress polling + disk spill."""

from __future__ import annotations

import contextvars
import hashlib
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

from aduport.config import settings
from aduport.core.convert_isolate import convert_isolated
from aduport.core.exceptions import AduPortError, MemoryBudgetError
from aduport.core.job_persist import load_job_snapshot, load_recent_snapshots, save_job_snapshot
from aduport.core.spill import SpillBuffer

_log = logging.getLogger("aduport.jobs")


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
    stage: str = ""
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
    export_warnings: list[str] = field(default_factory=list)
    # One-time-share secret for GET /jobs/{id} and /result (not listed in list_jobs).
    access_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    access_token_hash: str = ""
    # Bytes currently counted toward memory_budget_mb (input while running, result while retained).
    memory_charge: int = 0
    heartbeat_at: float | None = None
    # Set when the input charge was released outside ``_run`` (schedule failure or queued timeout).
    budget_settled: bool = False
    # Resume metadata (§6.4) — must survive queued→running snapshots.
    convert_opts: dict[str, Any] = field(default_factory=dict)
    client_tag: str = ""
    input_path: str | None = None

    def read_result(self) -> bytes | None:
        if self.result_spill is not None:
            return self.result_spill.read()
        if self.result_bytes is not None:
            return self.result_bytes
        if self.result_path or self.id:
            from aduport.core.job_results import load_job_result

            data = load_job_result(self.id, tenant_id=self.tenant_id)
            if data is not None:
                self.result_bytes = data
            return data
        return None

    def input_available(self) -> bool:
        from pathlib import Path

        from aduport.core.job_results import input_path_for

        if self.input_path and Path(self.input_path).is_file():
            return True
        p = input_path_for(self.id, self.tenant_id)
        return bool(p and p.is_file())

    def to_dict(self, *, include_token: bool = False) -> dict[str, Any]:
        out_len = 0
        if self.result_spill is not None:
            out_len = self.result_spill.size
        elif self.result_bytes is not None:
            out_len = len(self.result_bytes)
        resumable = self.status == JobStatus.error and self.input_available()
        reprocessable = self.input_available() and self.status in (
            JobStatus.error,
            JobStatus.done,
        )
        payload: dict[str, Any] = {
            "id": self.id,
            "filename": self.filename,
            "source_format": self.source_format,
            "target_format": self.target_format,
            "status": self.status.value,
            "progress": round(self.progress, 3),
            "stage": self.stage or "",
            "error": self.error,
            "code": self.code,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "bytes_in": self.bytes_in,
            "bytes_out": out_len,
            "result_filename": self.result_filename,
            "result_persisted": bool(self.result_path),
            "tenant_id": self.tenant_id,
            "resumable": resumable,
            "reprocessable": reprocessable,
            "degraded": False,
            "degraded_reasons": [],
        }
        if isinstance(self.error, str) and self.error.strip():
            from aduport.core.i18n_errors import translate_message

            payload["error_en"] = translate_message(self.error, self.code)
        if self.export_warnings:
            from aduport.exporters.export_warnings import warning_marks_degraded

            reasons = [w for w in self.export_warnings if warning_marks_degraded(w)]
            payload["export_warnings"] = list(self.export_warnings)
            payload["degraded"] = bool(reasons)
            payload["degraded_reasons"] = reasons
        if include_token and self.access_token:
            payload["access_token"] = self.access_token
        return payload

    def token_ok(self, provided: str | None) -> bool:
        got = (provided or "").strip()
        expected = (self.access_token or "").strip()
        if expected:
            if not got:
                return False
            return hmac.compare_digest(expected, got)
        token_hash = (self.access_token_hash or "").strip()
        if token_hash:
            if not got:
                return False
            digest = hashlib.sha256(got.encode("utf-8")).hexdigest()
            return hmac.compare_digest(digest, token_hash)
        return not bool(getattr(settings, "job_token_required", True))

    @staticmethod
    def hash_access_token(token: str) -> str:
        return hashlib.sha256((token or "").encode("utf-8")).hexdigest()

    def persist_payload(self, *, include_token: bool = False) -> dict[str, Any]:
        payload = self.to_dict()
        if include_token and self.access_token:
            payload["access_token"] = self.access_token
        else:
            payload.pop("access_token", None)
        if self.access_token:
            payload["access_token_hash"] = self.hash_access_token(self.access_token)
        # Resume metadata must persist across queued/running/error snapshots.
        payload["convert_opts"] = dict(self.convert_opts or {})
        payload["client"] = self.client_tag or ""
        if self.input_path:
            payload["input_path"] = self.input_path
        return payload


class JobStore:
    """Thread-safe job registry with bounded concurrency and memory budget."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._inflight_bytes = 0
        # In-process converts that outlived the job deadline (daemon threads).
        self._stuck_convert_threads = 0
        # Align with convert_pool CPU sizing — batch_workers alone capped at 3 and
        # left multi-core idle while isolate ProcessPool sat waiting for submits.
        import os

        auto = max(4, min(16, os.cpu_count() or 4))
        workers = int(settings.convert_pool_workers or auto)
        self._sem = threading.Semaphore(max(1, workers))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, workers),
            thread_name_prefix="aduport-job",
        )
        self._recover_jobs_on_startup()
        try:
            from aduport.core.spill_cleanup import cleanup_orphan_spills

            cleanup_orphan_spills()
        except Exception as exc:
            _log.warning("orphan spill cleanup skipped: %s", exc)

    def _mark_interrupted(self, snap: dict[str, Any], *, jid: str) -> None:
        from aduport.core.job_persist import save_job_snapshot

        snap["status"] = "error"
        snap["error"] = "服务重启，任务已中断"
        snap["code"] = "job_interrupted"
        snap["progress"] = 1.0
        snap["finished_at"] = time.time()
        snap.pop("access_token", None)
        # Keep input blob for POST /api/jobs/{id}/resume (§6.4 断点续转)
        try:
            save_job_snapshot(jid, snap)
        except Exception as exc:
            _log.warning("failed to mark interrupted job %s: %s", jid, exc)

    def resume(
        self,
        job_id: str,
        *,
        tenant_id: str | None = None,
        client: str = "",
    ) -> Job:
        """Re-queue an interrupted job whose input blob still exists (§6.4)."""
        from aduport.core.exceptions import AduPortError
        from aduport.core.job_persist import load_job_snapshot, save_job_snapshot
        from aduport.core.job_results import load_job_input

        snap = load_job_snapshot(job_id) or {}
        live = self.get(job_id, tenant_id=tenant_id, enforce_tenant=True)
        self._require_visible(live, snap, tenant_id)
        if live and live.status in (JobStatus.queued, JobStatus.running):
            raise AduPortError("任务仍在执行中", code="job_still_active", status_code=409)

        status = (live.status.value if live else str(snap.get("status") or ""))
        if status != "error":
            raise AduPortError(
                "仅可续转已中断或失败且仍保留输入的任务",
                code="job_not_resumable",
                status_code=409,
            )

        content = load_job_input(job_id, tenant_id=tenant_id)
        if not content:
            raise AduPortError(
                "任务输入已过期，无法续转",
                code="job_input_missing",
                status_code=410,
            )

        # Rebuild snap for replay
        base = dict(snap) if snap else {}
        if live:
            base.update(live.persist_payload())
        base["id"] = job_id
        base["status"] = "queued"
        base["error"] = None
        base["code"] = None
        base["progress"] = 0.0
        base["finished_at"] = None
        if client:
            base["client"] = client
        # The timeout clock starts at this attempt. An interrupted job from
        # hours ago must not become job_timeout the moment it is re-queued.
        base["created_at"] = time.time()
        base["heartbeat_at"] = None
        base["budget_settled"] = False
        # Clear prior result state
        base.pop("result_path", None)
        try:
            save_job_snapshot(job_id, base)
        except Exception as exc:
            _log.warning("save job snapshot %s failed: %s", job_id, exc)

        with self._lock:
            self._jobs.pop(job_id, None)
        self._replay_queued_job(base)
        job = self.get(job_id, tenant_id=tenant_id, enforce_tenant=False)
        if job is None:
            raise AduPortError("续转失败", code="job_resume_failed", status_code=500)
        return job

    def reprocess(
        self,
        job_id: str,
        *,
        opts_update: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        client: str = "",
    ) -> Job:
        """Re-queue a finished/failed job with optional convert-opt overrides (re-OCR)."""
        from aduport.core.exceptions import AduPortError
        from aduport.core.job_persist import load_job_snapshot, save_job_snapshot
        from aduport.core.job_results import load_job_input

        snap = load_job_snapshot(job_id) or {}
        live = self.get(job_id, tenant_id=tenant_id, enforce_tenant=True)
        self._require_visible(live, snap, tenant_id)
        if live and live.status in (JobStatus.queued, JobStatus.running):
            raise AduPortError("任务仍在执行中", code="job_still_active", status_code=409)

        content = load_job_input(job_id, tenant_id=tenant_id)
        if not content:
            raise AduPortError(
                "任务输入已过期，无法重跑",
                code="job_input_missing",
                status_code=410,
            )

        base = dict(snap) if snap else {}
        if live:
            base.update(live.persist_payload())
        merged_opts = dict(base.get("convert_opts") or {})
        if live and live.convert_opts:
            merged_opts.update(dict(live.convert_opts))
        if opts_update:
            for k, v in opts_update.items():
                if v is not None and v != "":
                    merged_opts[k] = v
        # Re-OCR / reprocess should not hit stale cache by default
        merged_opts.setdefault("cache_bypass", True)
        if merged_opts.get("force_ocr"):
            merged_opts.setdefault("skip_pdf_sidecar", True)

        base["id"] = job_id
        base["status"] = "queued"
        base["error"] = None
        base["code"] = None
        base["progress"] = 0.0
        base["finished_at"] = None
        base["convert_opts"] = merged_opts
        if client:
            base["client"] = client
        base["created_at"] = time.time()
        base["heartbeat_at"] = None
        base["budget_settled"] = False
        base.pop("result_path", None)
        try:
            save_job_snapshot(job_id, base)
        except Exception as exc:
            _log.warning("save job snapshot %s failed: %s", job_id, exc)

        with self._lock:
            self._jobs.pop(job_id, None)
        self._replay_queued_job(base)
        job = self.get(job_id, tenant_id=tenant_id, enforce_tenant=False)
        if job is None:
            raise AduPortError("重跑失败", code="job_reprocess_failed", status_code=500)
        return job

    def _recover_jobs_on_startup(self) -> None:
        """Replay persisted queued jobs; mark running ones interrupted (P1-9)."""
        from aduport.core.job_persist import load_recent_snapshots

        for snap in load_recent_snapshots(limit=500):
            status = str(snap.get("status") or "")
            if status not in ("queued", "running"):
                continue
            jid = str(snap.get("id") or "")
            if not jid:
                continue
            if status == "running":
                self._mark_interrupted(snap, jid=jid)
                continue
            self._replay_queued_job(snap)

    def _replay_queued_job(self, snap: dict[str, Any]) -> None:
        """Re-enqueue a queued snapshot whose input blob survived restart."""
        from aduport.core.job_persist import save_job_snapshot
        from aduport.core.job_results import load_job_input

        jid = str(snap.get("id") or "")
        tenant_raw = snap.get("tenant_id")
        tenant = str(tenant_raw) if tenant_raw else None
        content = load_job_input(jid, tenant_id=tenant)
        if not content:
            self._mark_interrupted(snap, jid=jid)
            return

        charge = max(len(content), int(snap.get("bytes_in") or 0))
        budget = settings.memory_budget_mb * 1024 * 1024
        queue_max = max(1, int(getattr(settings, "job_queue_max", 64) or 64))

        with self._lock:
            active = sum(
                1
                for j in self._jobs.values()
                if j.status in (JobStatus.queued, JobStatus.running)
            )
            if active >= queue_max or (budget > 0 and self._inflight_bytes + charge > budget):
                _log.warning("skip replay for %s: queue or memory budget full", jid)
                self._mark_interrupted(snap, jid=jid)
                return

            job = Job(
                id=jid,
                filename=str(snap.get("filename") or ""),
                source_format=str(snap.get("source_format") or ""),
                target_format=str(snap.get("target_format") or ""),
                status=JobStatus.queued,
                progress=float(snap.get("progress") or 0.0),
                created_at=float(snap.get("created_at") or time.time()),
                bytes_in=int(snap.get("bytes_in") or len(content)),
                result_filename=str(snap.get("result_filename") or snap.get("filename") or ""),
                tenant_id=tenant,
                callback_url=snap.get("callback_url"),
                access_token="",
                access_token_hash=str(snap.get("access_token_hash") or ""),
                convert_opts=dict(snap.get("convert_opts") or {}),
                client_tag=str(snap.get("client") or ""),
                input_path=str(snap.get("input_path") or "") or None,
            )
            job.memory_charge = charge
            self._jobs[jid] = job
            self._inflight_bytes += charge

        spill_prefix = f"aduport-job-in-{(tenant or 'anon')[:24]}-"
        spill = SpillBuffer(content, prefix=spill_prefix, tenant_id=tenant)
        opts = dict(snap.get("convert_opts") or {})
        client = str(snap.get("client") or "")
        try:
            payload = job.persist_payload()
            payload["input_path"] = snap.get("input_path")
            payload["convert_opts"] = opts
            payload["client"] = client
            save_job_snapshot(jid, payload)
        except Exception as exc:
            _log.warning("save replay snapshot failed for %s: %s", jid, exc)

        self._schedule(job, spill, charge, opts, client, reraise=False)
        _log.info("replayed queued job %s after restart", jid)

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
        rag_max_chars: int | None = None,
        rag_chunk_overlap: int | None = None,
        embed: bool | None = None,
        embed_backend: str | None = None,
        embed_model: str | None = None,
        vector_persist: bool | None = None,
        vector_collection: str | None = None,
        pdf_engine: str | None = None,
        ocr_engine: str | None = None,
        force_ocr: bool | None = None,
        ocr_max_pages: int | None = None,
        skip_pdf_sidecar: bool | None = None,
        cache_bypass: bool | None = None,
        docx_auto_toc: bool | None = None,
        latex_plain_comments: bool | None = None,
        csv_table_only: bool | None = None,
        callback_url: str | None = None,
        client: str = "",
        result_filename: str = "",
        tenant_id: str | None = None,
    ) -> Job:
        from aduport.core.webhooks import validate_callback_url

        tenant = (tenant_id or "").strip() or None
        cb = validate_callback_url(callback_url)
        spill_prefix = f"aduport-job-in-{(tenant or 'anon')[:24]}-"
        spill = SpillBuffer(content, prefix=spill_prefix, tenant_id=tenant)
        # Count logical payload size (spilled jobs still consume worker RAM when read)
        charge = max(spill.size, len(content))
        opts: dict[str, Any] = {
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
        if rag_max_chars is not None:
            opts["rag_max_chars"] = int(rag_max_chars)
        if rag_chunk_overlap is not None:
            opts["rag_chunk_overlap"] = int(rag_chunk_overlap)
        if embed is not None:
            opts["embed"] = bool(embed)
        if embed_backend:
            opts["embed_backend"] = str(embed_backend)
        if embed_model:
            opts["embed_model"] = str(embed_model)
        if vector_persist is not None:
            opts["vector_persist"] = bool(vector_persist)
        if vector_collection:
            opts["vector_collection"] = str(vector_collection)
        if pdf_engine:
            opts["pdf_engine"] = str(pdf_engine).strip().lower()
        if ocr_engine:
            opts["ocr_engine"] = str(ocr_engine).strip().lower()
        if force_ocr is not None:
            opts["force_ocr"] = bool(force_ocr)
        if ocr_max_pages is not None:
            opts["ocr_max_pages"] = int(ocr_max_pages)
        if skip_pdf_sidecar is not None:
            opts["skip_pdf_sidecar"] = bool(skip_pdf_sidecar)
        if cache_bypass is not None:
            opts["cache_bypass"] = bool(cache_bypass)
        if docx_auto_toc is not None:
            opts["docx_auto_toc"] = bool(docx_auto_toc)
        if latex_plain_comments is not None:
            opts["latex_plain_comments"] = bool(latex_plain_comments)
        if csv_table_only is not None:
            opts["csv_table_only"] = bool(csv_table_only)
        budget = settings.memory_budget_mb * 1024 * 1024
        queue_max = max(1, int(getattr(settings, "job_queue_max", 64) or 64))
        with self._lock:
            self._prune_unlocked()
            if budget > 0 and self._inflight_bytes + charge > budget:
                # Finished jobs keep result bytes until TTL. Drop that RAM
                # (the result is already on disk) before refusing a new job.
                self._release_finished_ram_unlocked()
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
                convert_opts=dict(opts),
                client_tag=client or "",
            )
            self._jobs[job.id] = job
            job.memory_charge = charge
            self._inflight_bytes += charge
            from aduport.core.job_results import save_job_input

            input_path = save_job_input(job.id, content, tenant_id=tenant)
            job.input_path = input_path
            try:
                save_job_snapshot(job.id, job.persist_payload())
            except Exception as exc:
                _log.warning("save queued snapshot failed for %s: %s", job.id, exc)
        # Propagate request ContextVars (tenant / audit) into worker thread
        self._schedule(job, spill, charge, opts, client)
        return job

    def _schedule(
        self,
        job: Job,
        spill: SpillBuffer,
        charge: int,
        opts: dict[str, Any],
        client: str,
        *,
        reraise: bool = True,
    ) -> None:
        """Submit ``_run``. A shut-down pool must not leave the job queued or running."""
        ctx = contextvars.copy_context()
        try:
            self._executor.submit(ctx.run, self._run, job.id, spill, charge, opts, client)
        except RuntimeError as exc:
            _log.warning("job %s schedule failed: %s", job.id, exc)
            spill.release()
            with self._lock:
                job.status = JobStatus.error
                job.stage = "error"
                job.code = "job_schedule_failed"
                job.error = "任务调度失败"
                job.progress = 1.0
                job.finished_at = time.time()
                self._release_charge_unlocked(job)
            try:
                save_job_snapshot(job.id, job.persist_payload())
            except Exception as snap_exc:
                _log.warning("save schedule-failed snapshot %s: %s", job.id, snap_exc)
            if not reraise:
                return
            raise AduPortError(
                "任务调度失败，转换池已关闭",
                code="job_schedule_failed",
                status_code=503,
            ) from exc

    def _invoke_with_deadline(self, fn):
        """Run ``fn`` with a wall-clock deadline for the job state machine.

        When ``ADUPORT_CONVERT_ISOLATE`` is on, ``convert_isolated`` already
        times out and SIGTERMs the worker (or kills the popen session). Call
        it on this thread so a deadline does not abandon an extra daemon.
        The in-process path cannot kill a stuck convert; that path still
        uses a daemon thread and counts ``stuck_convert_threads``.
        """
        if settings.convert_isolate:
            return fn()

        timeout_s = max(1, int(settings.convert_timeout_seconds))
        holder: dict[str, Any] = {}

        def run() -> None:
            try:
                holder["value"] = fn()
            except Exception as exc:
                holder["error"] = exc

        thread = threading.Thread(target=run, name="aduport-job-call", daemon=True)
        thread.start()
        thread.join(timeout_s)
        if thread.is_alive():
            thread.join(0.25)
        if thread.is_alive():
            with self._lock:
                self._stuck_convert_threads += 1
                stuck = self._stuck_convert_threads
            try:
                from aduport.core.metrics import metrics

                metrics.record_stuck_convert_thread()
            except Exception:
                pass
            _log.error(
                "job convert thread still alive after deadline "
                "(stuck_convert_threads=%s); prefer ADUPORT_CONVERT_ISOLATE "
                "so timeouts recycle workers instead of abandoning threads",
                stuck,
            )
            raise AduPortError("任务超时", code="job_timeout")
        if "error" in holder:
            raise holder["error"]
        return holder.get("value")

    def sweep_deadlines(self) -> list[str]:
        """Expire queued and running jobs past the deadline. No reader required."""
        expired: list[Job] = []
        with self._lock:
            for job in list(self._jobs.values()):
                if job.code == "job_timeout":
                    continue
                self._expire_overdue_unlocked(job)
                if job.code == "job_timeout":
                    expired.append(job)
        for job in expired:
            try:
                save_job_snapshot(job.id, job.persist_payload())
            except Exception as exc:
                _log.warning("save deadline snapshot %s: %s", job.id, exc)
        return [job.id for job in expired]

    def start_deadline_sweeper(self) -> None:
        """Background exit for jobs nobody polls. One daemon per process."""
        if getattr(self, "_deadline_started", False):
            return
        self._deadline_started = True

        def loop() -> None:
            while True:
                time.sleep(1.0)
                try:
                    self.sweep_deadlines()
                except Exception:
                    _log.exception("job deadline sweep failed")

        threading.Thread(target=loop, name="aduport-job-deadline", daemon=True).start()

    def _release_charge_unlocked(self, job: Job) -> None:
        if job.budget_settled:
            return
        held = int(job.memory_charge or 0)
        if held:
            self._inflight_bytes = max(0, self._inflight_bytes - held)
        job.memory_charge = 0
        job.budget_settled = True

    def _expire_overdue_unlocked(self, job: Job) -> None:
        """Move a job past ``convert_timeout_seconds`` to ``job_timeout``.

        Called from ``get`` / ``list_recent`` and from ``sweep_deadlines``, so a
        stuck queued or running job leaves that state even when nobody is polling.
        ``_run`` will not overwrite this error with done.
        """
        if job.status not in (JobStatus.queued, JobStatus.running):
            return
        limit = max(1, int(settings.convert_timeout_seconds))
        anchor = job.heartbeat_at or job.created_at
        if time.time() - anchor <= limit:
            return
        job.status = JobStatus.error
        job.stage = "error"
        job.code = "job_timeout"
        job.error = "任务超时"
        job.progress = 1.0
        job.finished_at = time.time()
        # Release even when the worker is still inside convert. ``budget_settled``
        # keeps ``_run``'s finally from subtracting the same charge again.
        self._release_charge_unlocked(job)

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
                self._expire_overdue_unlocked(hit)
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
        from aduport.core.job_results import result_path_for

        rpath = snap.get("result_path")
        if not rpath and snap.get("result_persisted"):
            rp = result_path_for(str(snap["id"]), job_tenant)
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
            access_token_hash=str(snap.get("access_token_hash") or ""),
            export_warnings=list(snap.get("export_warnings") or []),
            convert_opts=dict(snap.get("convert_opts") or {}),
            client_tag=str(snap.get("client") or ""),
            input_path=str(snap.get("input_path") or "") or None,
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def list_recent(
        self, limit: int = 50, *, tenant_id: str | None = None
    ) -> list[Job]:
        with self._lock:
            for job in self._jobs.values():
                self._expire_overdue_unlocked(job)
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            mem = [
                j
                for j in jobs
                if self._tenant_allows(j.tenant_id, tenant_id)
            ][:limit]
            have = {j.id for j in mem}
        if len(mem) >= limit:
            return mem
        for snap in load_recent_snapshots(limit * 2, tenant_id=tenant_id):
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

    def _require_visible(self, live: Job | None, snap: dict, tenant_id: str | None) -> None:
        """Refuse cross-tenant resume/reprocess even when a disk snapshot exists."""
        from aduport.core.exceptions import AduPortError

        if live is not None:
            return
        snap_tenant = (snap or {}).get("tenant_id")
        snap_tenant_s = str(snap_tenant).strip() if snap_tenant else None
        if not snap or not self._tenant_allows(snap_tenant_s, tenant_id):
            raise AduPortError("任务不存在或已过期", code="job_not_found", status_code=404)

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
        # Ensure resume metadata is on the live Job object for all later snapshots.
        job.convert_opts = dict(pdf_opts or job.convert_opts or {})
        job.client_tag = client or job.client_tag or ""
        self._sem.acquire()
        try:
            with self._lock:
                self._expire_overdue_unlocked(job)
                if job.code == "job_timeout":
                    return
                job.heartbeat_at = time.time()
                ocrish = bool((pdf_opts or {}).get("force_ocr")) or job.source_format in {
                    "pdf",
                    "png",
                    "jpg",
                    "jpeg",
                }
                job.status = JobStatus.running
                job.stage = "ocr" if ocrish else "convert"
                job.progress = 0.4 if ocrish else 0.2
            try:
                save_job_snapshot(job.id, job.persist_payload())
            except Exception as exc:
                _log.warning("save running snapshot failed for %s: %s", job.id, exc)
            try:
                content = spill.read()
                out = self._invoke_with_deadline(
                    lambda: convert_isolated(
                        content,
                        job.source_format,
                        job.target_format,
                        filename=job.filename,
                        client=client,
                        **pdf_opts,
                    )
                )
                from aduport.exporters.export_warnings import get_last_export_warnings

                warns = get_last_export_warnings()
                from aduport.core.job_results import save_job_result
                from aduport.core.webhooks import fire_job_webhook

                persisted = save_job_result(job.id, out, tenant_id=job.tenant_id)
                from aduport.core.object_storage import archive_job_object

                archive_uri = archive_job_object(job.id, out, tenant_id=job.tenant_id)
                out_spill = SpillBuffer(out, prefix="aduport-job-out-", tenant_id=job.tenant_id)
                completed = False
                with self._lock:
                    if job.code == "job_timeout":
                        out_spill.release()
                    else:
                        if out_spill.spilled:
                            job.result_spill = out_spill
                            job.result_bytes = None
                        else:
                            job.result_bytes = out
                            out_spill.release()
                        job.result_path = persisted or archive_uri
                        job.status = JobStatus.done
                        job.stage = "done"
                        job.progress = 1.0
                        job.finished_at = time.time()
                        job.export_warnings = warns
                        completed = True
                if not completed:
                    return
                payload = job.persist_payload()
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
            except AduPortError as exc:
                from aduport.core.webhooks import fire_job_webhook

                with self._lock:
                    if job.code == "job_timeout":
                        pass
                    elif job.status == JobStatus.done:
                        _log.warning("ignoring error after done for %s: %s", job.id, exc)
                    else:
                        job.status = JobStatus.error
                        job.stage = "error"
                        job.error = exc.message
                        job.code = exc.code
                        job.progress = 1.0
                        job.finished_at = time.time()
                        payload = job.persist_payload()
                        try:
                            save_job_snapshot(job.id, payload)
                        except Exception as exc:
                            _log.warning("save job snapshot %s failed: %s", job.id, exc)
                        try:
                            fire_job_webhook(payload, job.callback_url)
                        except Exception as exc:
                            _log.warning("webhook for %s failed: %s", job.id, exc)
            except Exception as exc:
                from aduport.core.webhooks import fire_job_webhook

                with self._lock:
                    if job.code == "job_timeout":
                        pass
                    elif job.status == JobStatus.done:
                        _log.warning("ignoring error after done for %s: %s", job.id, exc)
                    else:
                        job.status = JobStatus.error
                        job.stage = "error"
                        _log.exception("job %s failed", job.id)
                        job.error = "转换失败"
                        job.code = "conversion_error"
                        job.progress = 1.0
                        job.finished_at = time.time()
                        payload = job.persist_payload()
                        try:
                            save_job_snapshot(job.id, payload)
                        except Exception as exc:
                            _log.warning("save job snapshot %s failed: %s", job.id, exc)
                        try:
                            fire_job_webhook(payload, job.callback_url)
                        except Exception as exc:
                            _log.warning("webhook for %s failed: %s", job.id, exc)
        finally:
            spill.release()
            from aduport.core.job_results import delete_job_input

            # Keep input for error/interrupted resume until TTL prune.
            # Optionally retain after success so POST /jobs/{id}/reprocess can re-OCR.
            live = self._jobs.get(job_id)
            retain_done = bool(getattr(settings, "job_retain_input_on_done", True))
            if live is not None and live.status == JobStatus.done and not retain_done:
                delete_job_input(job_id)
                live.input_path = None
            self._sem.release()
            with self._lock:
                live = self._jobs.get(job_id)
                if live is None or not live.budget_settled:
                    # Drop input charge; retain in-RAM result size until prune (T19).
                    prev = charge
                    retain = 0
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
                self._release_finished_ram_unlocked()
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

    def _release_finished_ram_unlocked(self) -> None:
        finished = sorted(
            (
                job
                for job in self._jobs.values()
                if job.finished_at and job.status in (JobStatus.done, JobStatus.error)
            ),
            key=lambda job: job.finished_at or 0,
        )
        for job in finished:
            self._drop_job_ram_unlocked(job)

    def _drop_job_ram_unlocked(self, job: Job) -> None:
        if job.result_spill is not None:
            job.result_spill.release()
            job.result_spill = None
        job.result_bytes = None
        held = int(job.memory_charge or 0)
        if held:
            self._inflight_bytes = max(0, self._inflight_bytes - held)
            job.memory_charge = 0

    def _prune_unlocked(self) -> None:
        from aduport.core.job_results import delete_job_input, delete_job_result

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
                delete_job_input(job.id)
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
                delete_job_input(job.id)
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
                "stuck_convert_threads": int(self._stuck_convert_threads),
                **by_status,
            }


job_store = JobStore()
job_store.start_deadline_sweeper()
