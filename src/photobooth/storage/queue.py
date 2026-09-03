"""Generic durable job queue (IMPLEMENTATION_PLAN.md T-4.1).

SQLite-backed claim/retry/dead-letter queue, kind-agnostic: the queue itself
knows nothing about what a "kind" means, it's purely a payload discriminator
for whoever calls `claim()`. Built as the shared substrate for the upload
backend's retry queue (T-4.2) and the print queue (T-4.7) — see
delivery/backend.py's module docstring, which already names this file.

Concurrency model matches storage/db.py's `connect()`: one shared
`sqlite3.Connection` (`check_same_thread=False`), accessed by async workers
that take turns via `await` rather than true OS-thread parallelism. `claim()`
is still written as an atomic single-statement UPDATE (rather than
SELECT-then-UPDATE) so it stays correct even if that assumption ever changes
(e.g. a future worker pool using threads), and so two `claim()` calls issued
back-to-back can never return the same job.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import msgspec

# Cap on backoff delay between retries — a flaky job shouldn't wait longer
# than this between attempts, or offline-drain behaviour (T-4.4) would feel
# broken even once connectivity returns.
MAX_BACKOFF_S = 3600.0


class Job(msgspec.Struct):
    id: str
    kind: str
    payload: dict[str, object]
    attempts: int


class JobQueue:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def enqueue(self, kind: str, payload: dict[str, object], max_attempts: int = 5) -> str:
        job_id = uuid.uuid4().hex
        now = time.time()
        now_iso = datetime.now(UTC).isoformat()
        self._db.execute(
            "INSERT INTO jobs "
            "(id, kind, payload_json, status, attempts, max_attempts, "
            " next_attempt_at, claimed_at, created_at, updated_at, last_error) "
            "VALUES (?, ?, ?, 'pending', 0, ?, ?, NULL, ?, ?, NULL)",
            (job_id, kind, json.dumps(payload), max_attempts, now, now_iso, now_iso),
        )
        self._db.commit()
        return job_id

    def claim(self, kind: str | None = None) -> Job | None:
        """Atomically claim the single oldest eligible pending job.

        Fetches candidate ids (oldest first) then, for each, tries a
        conditional UPDATE keyed on that row's primary key AND
        `status = 'pending'`. `cursor.rowcount` after that UPDATE tells us
        whether *this* call won the claim on *that specific row*: if another
        caller claimed it first, `status` no longer matches, rowcount is 0,
        and we move to the next candidate. This avoids any ambiguity about
        *which* row got claimed (a plain `UPDATE ... WHERE id = (SELECT ...)`
        confirms only that one row was updated, not which one, which matters
        once two calls can land in the same time.time() tick) while staying
        race-free: the UPDATE's WHERE clause is what SQLite serializes on.
        """
        now = time.time()
        now_iso = datetime.now(UTC).isoformat()
        if kind is None:
            candidates = self._db.execute(
                "SELECT id FROM jobs WHERE status = 'pending' AND next_attempt_at <= ? "
                "ORDER BY created_at",
                (now,),
            ).fetchall()
        else:
            candidates = self._db.execute(
                "SELECT id FROM jobs WHERE status = 'pending' AND next_attempt_at <= ? "
                "AND kind = ? ORDER BY created_at",
                (now, kind),
            ).fetchall()

        for (job_id,) in candidates:
            cur = self._db.execute(
                "UPDATE jobs SET status = 'claimed', claimed_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now, now_iso, job_id),
            )
            self._db.commit()
            if cur.rowcount == 1:
                row = self._db.execute(
                    "SELECT id, kind, payload_json, attempts FROM jobs WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    return None
                r_id, r_kind, payload_json, attempts = row
                return Job(
                    id=r_id, kind=r_kind, payload=json.loads(payload_json), attempts=attempts
                )
        return None

    def complete(self, job_id: str) -> None:
        now_iso = datetime.now(UTC).isoformat()
        self._db.execute(
            "UPDATE jobs SET status = 'done', updated_at = ? WHERE id = ?",
            (now_iso, job_id),
        )
        self._db.commit()

    def fail(self, job_id: str, error: str, backoff_base_s: float = 5.0) -> None:
        """Record a failed attempt.

        Backoff formula: `backoff_base_s * 2 ** (attempts - 1)`, capped at
        `MAX_BACKOFF_S`, where `attempts` is the post-increment count (so the
        first failure waits `backoff_base_s`, the second `2 *
        backoff_base_s`, etc). Once `attempts >= max_attempts` the job is
        dead-lettered instead of rescheduled — a human needs to look at it
        (future admin surface), it's no longer eligible for `claim()`.
        """
        now = time.time()
        now_iso = datetime.now(UTC).isoformat()
        row = self._db.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return
        attempts, max_attempts = row
        attempts += 1

        if attempts >= max_attempts:
            self._db.execute(
                "UPDATE jobs SET status = 'dead', attempts = ?, updated_at = ?, "
                "last_error = ? WHERE id = ?",
                (attempts, now_iso, error, job_id),
            )
        else:
            delay = min(backoff_base_s * 2 ** (attempts - 1), MAX_BACKOFF_S)
            self._db.execute(
                "UPDATE jobs SET status = 'pending', attempts = ?, next_attempt_at = ?, "
                "updated_at = ?, last_error = ?, claimed_at = NULL WHERE id = ?",
                (attempts, now + delay, now_iso, error, job_id),
            )
        self._db.commit()

    def list_dead(self, kind: str | None = None) -> list[Job]:
        return self._list_by_status("dead", kind)

    def list_pending(self, kind: str | None = None) -> list[Job]:
        return self._list_by_status("pending", kind)

    def _list_by_status(self, status: str, kind: str | None) -> list[Job]:
        if kind is None:
            rows = self._db.execute(
                "SELECT id, kind, payload_json, attempts FROM jobs "
                "WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id, kind, payload_json, attempts FROM jobs "
                "WHERE status = ? AND kind = ? ORDER BY created_at",
                (status, kind),
            ).fetchall()
        return [
            Job(id=r[0], kind=r[1], payload=json.loads(r[2]), attempts=r[3]) for r in rows
        ]


async def run_worker(
    queue: JobQueue,
    kind: str,
    handler: Callable[[Job], Awaitable[None]],
    poll_interval_s: float = 2.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Generic poll loop: claim a job of `kind`, hand it to `handler`.

    Zero knowledge of what a job "means" lives here — that's entirely the
    handler's job. On success the job is marked `complete()`; on any
    exception raised by the handler it's routed to `fail()` with `str(exc)`
    as the error. When no eligible job is found, sleeps `poll_interval_s`
    before retrying. Loops until `stop_event` is set (checked between
    iterations, so a set event stops the loop promptly rather than mid-sleep
    forever).
    """
    event = stop_event or asyncio.Event()
    while not event.is_set():
        job = queue.claim(kind)
        if job is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=poll_interval_s)
            continue
        try:
            await handler(job)
        except Exception as exc:  # noqa: BLE001 - routed to dead-letter, not swallowed
            queue.fail(job.id, str(exc))
        else:
            queue.complete(job.id)
