"""Print queue: per-session-limited job submission on top of the generic
durable job queue (storage/queue.py), plus the worker-side glue that hands
claimed jobs to a `PrinterBackend`. See IMPLEMENTATION_PLAN.md T-4.7.

Parallel in structure to whatever the delivery wave builds on top of the
same `storage.queue.JobQueue`/`run_worker` — this module owns none of the
retry/claim/dead-letter machinery itself, only the print-specific policy
(the per-session limit) and the handler that bridges a claimed `Job` to
`PrinterBackend.submit()`.

**Per-session limit — what counts.** `PrintQueue.submit()` checks the limit
*before* enqueueing, against every `kind='print'` job ever enqueued for that
session_id, regardless of its current status (pending/claimed/done/dead).
That means a job that later fails permanently (dead-lettered — e.g. the
printer was offline for the whole retry window) still counts against the
guest's allowance. This is the simpler, safer reading of "per-session
limit": the alternative (only counting `done` jobs) would let a guest who
keeps re-tapping "print" during a printer outage silently rack up an
unbounded number of queued jobs, all of which would suddenly fire the
moment the printer comes back online. `remaining_for_session()` uses the
same count, so the guest-facing "prints remaining" indicator and the
enforcement in `submit()` never disagree.

**Media tracking.** Deliberately not a persistence layer here — "media
tracking" (photobooth-plan.md §9) is satisfied by surfacing whatever
`PrinterBackend.status()` already reports (CUPS's `printer-state-reasons`,
or `NullPrinter`'s `simulate_out_of_media` knob) to admin/the print-button
gate. No separate counter of "sheets used" is tracked in SQLite.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from photobooth.printing.backend import PrinterBackend
from photobooth.storage.queue import Job, JobQueue


class PrintLimitExceededError(Exception):
    """Raised by `PrintQueue.submit()` when the session has already used up
    its `print_limit_per_session` allowance."""


class PrintQueue:
    def __init__(self, queue: JobQueue, print_limit_per_session: int) -> None:
        self._queue = queue
        self._print_limit_per_session = print_limit_per_session

    def submit(self, image_path: Path, session_id: str, max_attempts: int = 5) -> str:
        """Enqueue a print job for `session_id`, raising
        `PrintLimitExceededError` if the session is already at its limit.
        Returns the enqueued job id (not a CUPS/NullPrinter job id — that
        only exists once a worker claims and processes this job).
        """
        used = self._count_for_session(session_id)
        if used >= self._print_limit_per_session:
            raise PrintLimitExceededError(
                f"session {session_id!r} has reached its print limit "
                f"({used}/{self._print_limit_per_session})"
            )
        return self._queue.enqueue(
            "print",
            {"image_path": str(image_path), "session_id": session_id},
            max_attempts=max_attempts,
        )

    def remaining_for_session(self, session_id: str) -> int:
        return max(0, self._print_limit_per_session - self._count_for_session(session_id))

    def _count_for_session(self, session_id: str) -> int:
        # storage/queue.py's JobQueue exposes no query API beyond
        # list_pending/list_dead (both single-status), and we can't add one
        # there (owned by a sibling wave) — SQLite's built-in json_extract
        # over the jobs table's payload_json is the simplest way to count
        # every print job ever enqueued for a session regardless of status.
        # test_job_queue.py already reaches into `queue._db` directly for
        # equivalent ad-hoc assertions, so this matches an established
        # in-repo convention rather than inventing a new one.
        row = self._queue._db.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM jobs WHERE kind = 'print' "
            "AND json_extract(payload_json, '$.session_id') = ?",
            (session_id,),
        ).fetchone()
        return int(row[0]) if row is not None else 0


def make_print_handler(backend: PrinterBackend) -> Callable[[Job], Awaitable[None]]:
    """Builds the `run_worker` handler that submits a claimed print job to
    `backend`. A later wave's app startup wires this as:

        queue = JobQueue(db)
        printer = build_printer_backend(settings.printing)
        if printer is not None:
            handler = make_print_handler(printer)
            asyncio.create_task(run_worker(queue, "print", handler, stop_event=stop_event))

    and sets `stop_event` on shutdown to stop the poll loop.
    """

    async def handle(job: Job) -> None:
        image_path = Path(str(job.payload["image_path"]))
        session_id = str(job.payload["session_id"])
        await backend.submit(image_path, session_id)

    return handle
