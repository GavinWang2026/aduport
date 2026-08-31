"""In-memory async conversion job queue with progress polling + disk spill."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import settings
from app.core.converter import convert
from app.core.exceptions import MdBridgeError, MemoryBudgetError
from app.core.spill import SpillBuffer


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
    bytes_in: int = 0

    def read_result(self) -> bytes | None:
        if self.result_spill is not None:
            return self.result_spill.read()
        return self.result_bytes

    def to_dict(self) -> dict[str, Any]:
        out_len = 0
        if self.result_spill is not None:
            out_len = self.result_spill.size
        elif self.result_bytes is not None:
            out_len = len(self.result_bytes)
        return {
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
        }


class JobStore:
    """Thread-safe job registry with bounded concurrency and memory budget."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._inflight_bytes = 0
        self._sem = threading.Semaphore(max(1, settings.batch_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, settings.batch_workers),
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
        client: str = "",
        result_filename: str = "",
    ) -> Job:
        spill = SpillBuffer(content, prefix="mdbridge-job-in-")
        # Only count in-RAM portion toward budget when spilled
        charge = 0 if spill.spilled else len(content)
        budget = settings.memory_budget_mb * 1024 * 1024
        with self._lock:
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
            )
            self._jobs[job.id] = job
            self._inflight_bytes += charge
            self._prune_unlocked()

        self._executor.submit(
            self._run,
            job.id,
            spill,
            charge,
            {
                "pdf_font_family": pdf_font_family,
                "pdf_custom_css": pdf_custom_css,
                "pdf_page_size": pdf_page_size,
                "pdf_orientation": pdf_orientation,
                "pdf_header": pdf_header,
                "pdf_footer": pdf_footer,
                "pdf_page_numbers": pdf_page_numbers,
                "pdf_toc": pdf_toc,
            },
            client,
        )
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_recent(self, limit: int = 50) -> list[Job]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return jobs[:limit]

    def _run(
        self,
        job_id: str,
        spill: SpillBuffer,
        charge: int,
        pdf_opts: dict[str, Any],
        client: str,
    ) -> None:
        job = self.get(job_id)
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
                out = convert(
                    content,
                    job.source_format,
                    job.target_format,
                    filename=job.filename,
                    client=client,
                    **pdf_opts,
                )
                out_spill = SpillBuffer(out, prefix="mdbridge-job-out-")
                with self._lock:
                    if out_spill.spilled:
                        job.result_spill = out_spill
                        job.result_bytes = None
                    else:
                        job.result_bytes = out
                        out_spill.release()
                    job.status = JobStatus.done
                    job.progress = 1.0
                    job.finished_at = time.time()
            except MdBridgeError as exc:
                with self._lock:
                    job.status = JobStatus.error
                    job.error = exc.message
                    job.code = exc.code
                    job.progress = 1.0
                    job.finished_at = time.time()
            except Exception as exc:
                with self._lock:
                    job.status = JobStatus.error
                    job.error = str(exc) if settings.debug else "转换失败"
                    job.code = "conversion_error"
                    job.progress = 1.0
                    job.finished_at = time.time()
        finally:
            spill.release()
            self._sem.release()
            with self._lock:
                self._inflight_bytes = max(0, self._inflight_bytes - charge)

    def _prune_unlocked(self) -> None:
        ttl = settings.job_ttl_seconds
        now = time.time()
        expired = [
            jid
            for jid, j in self._jobs.items()
            if j.finished_at and now - j.finished_at > ttl
        ]
        for jid in expired:
            job = self._jobs.pop(jid, None)
            if job and job.result_spill:
                job.result_spill.release()
        max_jobs = settings.job_max_entries
        if len(self._jobs) > max_jobs:
            finished = sorted(
                (j for j in self._jobs.values() if j.finished_at),
                key=lambda j: j.finished_at or 0,
            )
            overflow = len(self._jobs) - max_jobs
            for job in finished[:overflow]:
                if job.result_spill:
                    job.result_spill.release()
                self._jobs.pop(job.id, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            by_status = {s.value: 0 for s in JobStatus}
            for j in self._jobs.values():
                by_status[j.status.value] = by_status.get(j.status.value, 0) + 1
            return {
                "total": len(self._jobs),
                "inflight_bytes": self._inflight_bytes,
                **by_status,
            }


job_store = JobStore()
