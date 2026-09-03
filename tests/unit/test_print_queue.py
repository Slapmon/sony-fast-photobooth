"""Tests for printing/queue.py — PrintQueue's per-session limit enforcement
and the worker-side handler bridging claimed jobs to a PrinterBackend
(T-4.7).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from photobooth.printing.backend import PrinterBackend
from photobooth.printing.queue import (
    PrintLimitExceededError,
    PrintQueue,
    make_print_handler,
)
from photobooth.storage import db as storage_db
from photobooth.storage.queue import JobQueue, run_worker


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.executescript(storage_db.SCHEMA)
    return c


@pytest.fixture
def job_queue(conn: sqlite3.Connection) -> JobQueue:
    return JobQueue(conn)


def test_submit_enqueues_a_print_job(job_queue: JobQueue) -> None:
    print_queue = PrintQueue(job_queue, print_limit_per_session=2)
    job_id = print_queue.submit(Path("shot.jpg"), "session-1")

    assert isinstance(job_id, str) and job_id
    job = job_queue.claim("print")
    assert job is not None
    assert job.id == job_id
    assert job.payload == {"image_path": "shot.jpg", "session_id": "session-1"}


def test_remaining_for_session_counts_down(job_queue: JobQueue) -> None:
    print_queue = PrintQueue(job_queue, print_limit_per_session=2)
    assert print_queue.remaining_for_session("session-1") == 2

    print_queue.submit(Path("a.jpg"), "session-1")
    assert print_queue.remaining_for_session("session-1") == 1

    print_queue.submit(Path("b.jpg"), "session-1")
    assert print_queue.remaining_for_session("session-1") == 0


def test_submit_raises_past_the_limit(job_queue: JobQueue) -> None:
    print_queue = PrintQueue(job_queue, print_limit_per_session=1)
    print_queue.submit(Path("a.jpg"), "session-1")

    with pytest.raises(PrintLimitExceededError):
        print_queue.submit(Path("b.jpg"), "session-1")

    # Limit is per-session, not global.
    other_job_id = print_queue.submit(Path("c.jpg"), "session-2")
    assert isinstance(other_job_id, str) and other_job_id


def test_limit_counts_jobs_regardless_of_status(job_queue: JobQueue) -> None:
    """A dead-lettered job still used up one of the session's attempts —
    otherwise a guest could retry through an outage into an unbounded
    backlog of jobs that all fire once the printer returns."""
    print_queue = PrintQueue(job_queue, print_limit_per_session=1)
    job_id = print_queue.submit(Path("a.jpg"), "session-1", max_attempts=1)

    job = job_queue.claim("print")
    assert job is not None and job.id == job_id
    job_queue.fail(job_id, "printer offline")

    dead = job_queue.list_dead("print")
    assert len(dead) == 1

    assert print_queue.remaining_for_session("session-1") == 0
    with pytest.raises(PrintLimitExceededError):
        print_queue.submit(Path("b.jpg"), "session-1")


class _RecordingPrinter(PrinterBackend):
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    async def submit(self, image_path: Path, session_id: str) -> str:
        self.calls.append((image_path, session_id))
        return "fake-cups-job-1"

    async def status(self) -> dict[str, object]:
        return {"status": "green", "detail": "ok"}


async def test_worker_calls_printer_backend_submit_on_claimed_job(
    job_queue: JobQueue,
) -> None:
    print_queue = PrintQueue(job_queue, print_limit_per_session=5)
    print_queue.submit(Path("shot.jpg"), "session-1")

    printer = _RecordingPrinter()
    handler = make_print_handler(printer)
    stop_event = asyncio.Event()

    async def handle_and_stop(job: object) -> None:
        await handler(job)  # type: ignore[arg-type]
        stop_event.set()

    await asyncio.wait_for(
        run_worker(
            job_queue, "print", handle_and_stop, poll_interval_s=0.05, stop_event=stop_event
        ),
        timeout=5.0,
    )

    assert printer.calls == [(Path("shot.jpg"), "session-1")]
    assert job_queue.list_pending("print") == []


async def test_worker_dead_letters_on_printer_offline_error(job_queue: JobQueue) -> None:
    from photobooth.printing.backend import PrinterOfflineError

    class _OfflinePrinter(PrinterBackend):
        async def submit(self, image_path: Path, session_id: str) -> str:
            raise PrinterOfflineError("out of media")

        async def status(self) -> dict[str, object]:
            return {"status": "red", "detail": "out of media"}

    print_queue = PrintQueue(job_queue, print_limit_per_session=5)
    job_id = print_queue.submit(Path("shot.jpg"), "session-1", max_attempts=1)

    handler = make_print_handler(_OfflinePrinter())
    stop_event = asyncio.Event()

    async def handle_and_stop(job: object) -> None:
        try:
            await handler(job)  # type: ignore[arg-type]
        finally:
            stop_event.set()

    await asyncio.wait_for(
        run_worker(
            job_queue, "print", handle_and_stop, poll_interval_s=0.05, stop_event=stop_event
        ),
        timeout=5.0,
    )

    dead = job_queue.list_dead("print")
    assert len(dead) == 1
    assert dead[0].id == job_id
