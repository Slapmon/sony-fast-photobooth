"""Offline-tolerant upload worker (IMPLEMENTATION_PLAN.md T-4.4).

This is deliberately *not* a separate retry subsystem — it's a thin
`storage/queue.py`-backed handler. "Offline behaviour" falls out of using the
queue correctly: an upload job that fails (network down, target
unreachable) gets rescheduled by `JobQueue.fail()`'s exponential backoff
rather than lost; once connectivity returns, the next scheduled attempt
succeeds and the job completes. No "are we online" detection code exists or
is needed — a stalled network just looks like N consecutive failures
followed by a success, which the queue already handles generically.

Integration contract for the wave that wires this into `web/app.py`'s
`lifespan` (do NOT wire it here — see IMPLEMENTATION_PLAN.md T-4.4's task
notes):

Startup (inside `lifespan`, after `conn = storage_db.connect(...)`):

    from photobooth.delivery.backend import build_delivery_backend
    from photobooth.delivery.worker import DeliveryWorker

    delivery_backend = build_delivery_backend(settings.delivery)
    delivery_worker = DeliveryWorker(JobQueue(conn), delivery_backend)
    delivery_worker.start()
    app.state.delivery_worker = delivery_worker

Enqueuing a job (wherever `FullImageReady` is handled today, e.g.
`web/session.py` after a capture's full image lands on disk):

    JobQueue(conn).enqueue(
        kind="upload",
        payload={
            "local_path": str(full_path),
            "remote_key": f"{capture_id}.jpg",
            "capture_id": capture_id,
        },
    )

  — enqueue only, never call `backend.upload()` directly inline; that's what
  makes offline behaviour transparent (T-4.4).

Shutdown (inside `lifespan`'s `finally`, alongside the other `aclose()`
calls, before `conn.close()` since the worker still uses `conn` via the
queue until it has stopped):

    await delivery_worker.aclose()

`DeliveryWorker.start()` is idempotent-unsafe (starting twice creates two
poll loops) — call it exactly once per instance, matching `RenderPool`'s
"construct once, use, close once" lifecycle.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from photobooth.delivery.backend import DeliveryBackend
from photobooth.storage.queue import Job, JobQueue, run_worker

logger = structlog.get_logger(__name__)

UPLOAD_JOB_KIND = "upload"


class DeliveryWorker:
    """Wraps `storage.queue.run_worker()` for `kind="upload"` jobs.

    The handler pulls `local_path`/`remote_key` out of the job payload,
    calls the configured `DeliveryBackend.upload()`, and lets any exception
    propagate — `run_worker()` routes that straight to `JobQueue.fail()`,
    which is what gives this "offline behaviour" for free (see module
    docstring). On success the resulting guest-facing URL is logged; a
    future admin/gallery surface can read it back off the job or capture
    record rather than this worker holding onto it itself (this worker is
    intentionally stateless beyond the queue).
    """

    def __init__(
        self,
        queue: JobQueue,
        backend: DeliveryBackend,
        poll_interval_s: float = 2.0,
    ) -> None:
        self._queue = queue
        self._backend = backend
        self._poll_interval_s = poll_interval_s
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def _handle(self, job: Job) -> None:
        local_path = job.payload["local_path"]
        remote_key = job.payload["remote_key"]
        assert isinstance(local_path, str)
        assert isinstance(remote_key, str)

        url = await self._backend.upload(Path(local_path), remote_key)
        logger.info(
            "upload_complete",
            job_id=job.id,
            capture_id=job.payload.get("capture_id"),
            remote_key=remote_key,
            url=url,
        )

    def start(self) -> None:
        """Start the poll loop as a background asyncio task. Call once."""
        if self._task is not None:
            raise RuntimeError("DeliveryWorker.start() called more than once")
        self._task = asyncio.create_task(
            run_worker(
                self._queue,
                kind=UPLOAD_JOB_KIND,
                handler=self._handle,
                poll_interval_s=self._poll_interval_s,
                stop_event=self._stop_event,
            )
        )

    async def aclose(self) -> None:
        """Signal the poll loop to stop and wait for it to exit. Safe to
        call even if `start()` was never called."""
        self._stop_event.set()
        if self._task is not None:
            await self._task
            self._task = None
