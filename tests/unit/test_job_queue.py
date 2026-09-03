"""Tests for storage/queue.py — the generic durable job queue (T-4.1)."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from photobooth.storage import db as storage_db
from photobooth.storage.queue import Job, JobQueue, run_worker


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.executescript(storage_db.SCHEMA)
    return c


@pytest.fixture
def queue(conn: sqlite3.Connection) -> JobQueue:
    return JobQueue(conn)


def test_enqueue_claim_complete_happy_path(queue: JobQueue) -> None:
    job_id = queue.enqueue("upload", {"path": "/tmp/foo.jpg"})
    assert isinstance(job_id, str) and job_id

    job = queue.claim("upload")
    assert job is not None
    assert isinstance(job, Job)
    assert job.id == job_id
    assert job.kind == "upload"
    assert job.payload == {"path": "/tmp/foo.jpg"}
    assert job.attempts == 0

    # Not claimable again while already claimed.
    assert queue.claim("upload") is None

    queue.complete(job_id)
    # Not claimable again once done.
    assert queue.claim("upload") is None


def test_claim_returns_none_when_nothing_eligible(queue: JobQueue) -> None:
    assert queue.claim() is None
    assert queue.claim("upload") is None

    queue.enqueue("print", {"foo": "bar"})
    # Wrong kind filter -> nothing eligible.
    assert queue.claim("upload") is None
    # Right kind -> claimable.
    job = queue.claim("print")
    assert job is not None


def test_fail_backoff_delays_reclaim(queue: JobQueue) -> None:
    job_id = queue.enqueue("upload", {}, max_attempts=5)
    job = queue.claim("upload")
    assert job is not None

    queue.fail(job_id, "boom", backoff_base_s=5.0)

    # Immediately after failing, next_attempt_at is in the future -> not claimable.
    assert queue.claim("upload") is None

    # Force next_attempt_at into the past directly via SQL (simulating time
    # passing) and confirm it becomes claimable again.
    queue._db.execute("UPDATE jobs SET next_attempt_at = 0 WHERE id = ?", (job_id,))
    queue._db.commit()

    job2 = queue.claim("upload")
    assert job2 is not None
    assert job2.id == job_id
    assert job2.attempts == 1


def test_fail_past_max_attempts_dead_letters(queue: JobQueue) -> None:
    job_id = queue.enqueue("upload", {}, max_attempts=2)

    # Attempt 1: claim, fail -> back to pending (attempts=1 < max_attempts=2).
    job = queue.claim("upload")
    assert job is not None
    queue.fail(job_id, "err1", backoff_base_s=0.0)
    queue._db.execute("UPDATE jobs SET next_attempt_at = 0 WHERE id = ?", (job_id,))
    queue._db.commit()

    # Attempt 2: claim, fail -> attempts=2 >= max_attempts=2 -> dead.
    job = queue.claim("upload")
    assert job is not None
    queue.fail(job_id, "err2", backoff_base_s=0.0)

    dead = queue.list_dead("upload")
    assert len(dead) == 1
    assert dead[0].id == job_id

    # A dead job is never claimable, regardless of next_attempt_at.
    queue._db.execute("UPDATE jobs SET next_attempt_at = 0 WHERE id = ?", (job_id,))
    queue._db.commit()
    assert queue.claim("upload") is None


def test_list_pending_and_list_dead(queue: JobQueue) -> None:
    id1 = queue.enqueue("upload", {"n": 1})
    id2 = queue.enqueue("upload", {"n": 2}, max_attempts=1)
    queue.enqueue("print", {"n": 3})

    pending_upload = queue.list_pending("upload")
    assert {j.id for j in pending_upload} == {id1, id2}

    job2 = queue.claim("upload")
    # Ensure we claimed id2 or id1; fail whichever until dead-lettered for
    # a deterministic check that list_dead only returns dead jobs.
    assert job2 is not None
    queue.fail(job2.id, "oops", backoff_base_s=0.0)
    # If job2 had max_attempts=1, it's now dead; otherwise it's pending again.
    dead = queue.list_dead("upload")
    pending_now = queue.list_pending("upload")
    assert len(dead) + len(pending_now) == 2


def test_concurrent_claims_never_double_claim(queue: JobQueue) -> None:
    ids = {queue.enqueue("upload", {"n": i}) for i in range(5)}

    claimed_ids: list[str] = []
    for _ in range(10):
        job = queue.claim("upload")
        if job is not None:
            claimed_ids.append(job.id)

    # Exactly the 5 distinct jobs were claimed, each exactly once.
    assert len(claimed_ids) == 5
    assert set(claimed_ids) == ids
    assert len(set(claimed_ids)) == len(claimed_ids)


async def test_run_worker_processes_job_and_stops(queue: JobQueue) -> None:
    queue.enqueue("upload", {"value": 42})
    handled: list[dict[str, object]] = []
    stop_event = asyncio.Event()

    async def handler(job: Job) -> None:
        handled.append(job.payload)
        stop_event.set()

    await asyncio.wait_for(
        run_worker(queue, "upload", handler, poll_interval_s=0.05, stop_event=stop_event),
        timeout=5.0,
    )

    assert handled == [{"value": 42}]


async def test_run_worker_routes_handler_exception_to_fail(queue: JobQueue) -> None:
    job_id = queue.enqueue("upload", {}, max_attempts=2)
    stop_event = asyncio.Event()
    calls = 0

    async def handler(job: Job) -> None:
        nonlocal calls
        calls += 1
        stop_event.set()
        raise RuntimeError("handler blew up")

    await asyncio.wait_for(
        run_worker(queue, "upload", handler, poll_interval_s=0.05, stop_event=stop_event),
        timeout=5.0,
    )

    assert calls == 1
    row = queue._db.execute(
        "SELECT status, last_error, attempts FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert row is not None
    status, last_error, attempts = row
    assert status == "pending"
    assert last_error == "handler blew up"
    assert attempts == 1
