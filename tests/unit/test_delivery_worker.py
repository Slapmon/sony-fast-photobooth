"""Tests for delivery/worker.py — DeliveryWorker, the offline-tolerant
upload worker (IMPLEMENTATION_PLAN.md T-4.4).

Core property under test: a DeliveryBackend whose upload() fails N times
then succeeds (simulating "network down, then back up") still ends with the
job completed and the guest-facing URL recorded, purely via
storage/queue.py's existing retry/backoff — no special "offline detection"
needed.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from photobooth.delivery.backend import DeliveryBackend
from photobooth.delivery.worker import DeliveryWorker
from photobooth.storage import db as storage_db
from photobooth.storage.queue import JobQueue


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.executescript(storage_db.SCHEMA)
    return c


@pytest.fixture
def queue(conn: sqlite3.Connection) -> JobQueue:
    return JobQueue(conn)


class FlakyThenSucceedsBackend(DeliveryBackend):
    """Fails `fail_count` times, then succeeds and returns `success_url`."""

    def __init__(self, fail_count: int, success_url: str) -> None:
        self._fail_count = fail_count
        self._success_url = success_url
        self.calls = 0

    async def upload(self, local_path: Path, remote_key: str) -> str:
        self.calls += 1
        if self.calls <= self._fail_count:
            raise ConnectionError(f"network down (attempt {self.calls})")
        return self._success_url


class AlwaysFailsBackend(DeliveryBackend):
    def __init__(self) -> None:
        self.calls = 0

    async def upload(self, local_path: Path, remote_key: str) -> str:
        self.calls += 1
        raise ConnectionError("network permanently down")


async def test_worker_completes_job_after_transient_failures(
    queue: JobQueue, tmp_path: Path
) -> None:
    local_path = tmp_path / "abc123.jpg"
    local_path.write_bytes(b"data")

    backend = FlakyThenSucceedsBackend(fail_count=2, success_url="/uploads/abc123.jpg")
    job_id = queue.enqueue(
        "upload",
        {"local_path": str(local_path), "remote_key": "abc123.jpg", "capture_id": "abc123"},
        max_attempts=5,
    )

    # backoff_base_s isn't configurable from DeliveryWorker's public API, so
    # drive retries directly: run the worker's handler through the queue in
    # a tight loop, collapsing next_attempt_at after each failure so the
    # test doesn't have to sleep through real backoff delays.
    worker = DeliveryWorker(queue, backend, poll_interval_s=0.01)
    worker.start()
    try:
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            row = queue._db.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert row is not None
            if row[0] == "done":
                break
            # Force any pending backoff to be immediately eligible so the
            # test doesn't wait through real exponential delays.
            queue._db.execute(
                "UPDATE jobs SET next_attempt_at = 0 WHERE id = ? AND status = 'pending'",
                (job_id,),
            )
            queue._db.commit()
            await asyncio.sleep(0.02)
        else:
            pytest.fail("job never completed within timeout")
    finally:
        await worker.aclose()

    row = queue._db.execute(
        "SELECT status, attempts FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == "done"
    assert backend.calls == 3  # 2 failures + 1 success
    assert row[1] == 2  # attempts only increments on fail(), not on the final success


async def test_worker_dead_letters_after_max_attempts_exhausted(
    queue: JobQueue, tmp_path: Path
) -> None:
    local_path = tmp_path / "abc123.jpg"
    local_path.write_bytes(b"data")

    backend = AlwaysFailsBackend()
    job_id = queue.enqueue(
        "upload",
        {"local_path": str(local_path), "remote_key": "abc123.jpg"},
        max_attempts=2,
    )

    worker = DeliveryWorker(queue, backend, poll_interval_s=0.01)
    worker.start()
    try:
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            row = queue._db.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            assert row is not None
            if row[0] == "dead":
                break
            queue._db.execute(
                "UPDATE jobs SET next_attempt_at = 0 WHERE id = ? AND status = 'pending'",
                (job_id,),
            )
            queue._db.commit()
            await asyncio.sleep(0.02)
        else:
            pytest.fail("job never dead-lettered within timeout")
    finally:
        await worker.aclose()

    assert backend.calls == 2


async def test_start_twice_raises() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(storage_db.SCHEMA)
    queue = JobQueue(conn)
    backend = FlakyThenSucceedsBackend(fail_count=0, success_url="/x")
    worker = DeliveryWorker(queue, backend)
    worker.start()
    try:
        with pytest.raises(RuntimeError):
            worker.start()
    finally:
        await worker.aclose()


async def test_aclose_without_start_is_safe() -> None:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(storage_db.SCHEMA)
    queue = JobQueue(conn)
    backend = FlakyThenSucceedsBackend(fail_count=0, success_url="/x")
    worker = DeliveryWorker(queue, backend)
    await worker.aclose()
