"""In-memory async job model (API_CONTRACT.md §3-4).

One in-process worker task consumes an asyncio queue — exactly one job runs
at a time (v1, single GPU). The store is a plain dict: jobs do not survive a
process restart (accepted for the walking skeleton, INFRA.md §5).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .config import Settings
from .pipeline import AnalysisPipeline, PipelineError
from .schemas import CreateServeRequest, JobErrorInfo, ServeResult, now_utc

log = logging.getLogger("servebot.jobs")


class JobQueueFull(Exception):
    """Raised by submit() when the queue depth cap is hit -> 429 busy."""


@dataclass
class Job:
    job_id: str
    request: CreateServeRequest
    created_at: datetime = field(default_factory=now_utc)
    status: str = "queued"  # queued | running | succeeded | failed
    stage: Optional[str] = None
    progress: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    result: Optional[ServeResult] = None
    mesh_object_key: Optional[str] = None
    error: Optional[JobErrorInfo] = None

    @property
    def user_label(self) -> Optional[str]:
        return self.request.client.user_label if self.request.client else None


class JobStore:
    """Insertion-ordered in-memory job registry."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def add(self, job: Job) -> None:
        self._jobs[job.job_id] = job

    def remove(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_recent(self, limit: int, status: Optional[str] = None) -> List[Job]:
        jobs = [
            j
            for j in reversed(self._jobs.values())
            if status is None or j.status == status
        ]
        return jobs[:limit]


class Worker:
    """Single background consumer — one job at a time."""

    def __init__(self, store: JobStore, pipeline: AnalysisPipeline, settings: Settings):
        self._store = store
        self._pipeline = pipeline
        self._settings = settings
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.queue_max_depth)
        self._task: Optional[asyncio.Task] = None

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    def submit(self, job: Job) -> None:
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise JobQueueFull() from None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="servebot-worker")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._run_job(job)
            finally:
                self._queue.task_done()

    async def _run_job(self, job: Job) -> None:
        job.status = "running"
        job.started_at = now_utc()
        job.progress = 0.02

        def report(stage: str, progress: float) -> None:
            job.stage = stage
            job.progress = progress

        try:
            output = await self._pipeline.run(job.job_id, job.request, report)
            job.result = output.result
            job.mesh_object_key = output.mesh_object_key
            job.status = "succeeded"
        except PipelineError as exc:
            log.warning("job %s failed at %s: %s", job.job_id, exc.stage, exc.message)
            job.status = "failed"
            job.error = JobErrorInfo(
                code=exc.code, message=exc.message, stage=exc.stage, retriable=exc.retriable
            )
        except Exception:  # noqa: BLE001 — never let the worker die
            log.exception("job %s: unexpected pipeline fault", job.job_id)
            job.status = "failed"
            job.error = JobErrorInfo(
                code="internal_error",
                message="Unexpected server fault during analysis.",
                stage=job.stage or "downloading",
                retriable=True,
            )
        finally:
            job.stage = None
            job.progress = 1.0
            job.finished_at = now_utc()
