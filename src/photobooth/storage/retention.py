"""Retention policy: delete captures (DB row + on-disk files) older than a
configurable window (IMPLEMENTATION_PLAN.md T-4.5, photobooth-plan.md §11's
GDPR retention requirement — "automatic deletion after a defined window,
enforced in code, not by hand").

Integration contract for the wave that wires this into `web/app.py`'s
`lifespan` (do NOT wire it here — matches T-4.4's DeliveryWorker contract in
delivery/worker.py):

Startup (inside `lifespan`, after `conn = storage_db.connect(...)`):

    import asyncio
    from photobooth.storage.retention import run_retention_sweep

    retention_stop_event = asyncio.Event()
    retention_task = asyncio.create_task(
        run_retention_sweep(
            conn, CAPTURES_DIR, settings.retention, stop_event=retention_stop_event
        )
    )
    app.state.retention_stop_event = retention_stop_event
    app.state.retention_task = retention_task

Shutdown (inside `lifespan`'s `finally`, before `conn.close()` for the same
reason as DeliveryWorker — the sweep still uses `conn` until it observes the
stop event):

    retention_stop_event.set()
    await retention_task

If `settings.retention.enabled` is `False` (the default), `run_retention_sweep`
still loops and sleeps `interval_s` forever without ever calling
`run_retention_sweep_once` — cheap to always start, no separate "is retention
on" branch needed at the call site.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import structlog

from photobooth.config.models import RetentionConfig
from photobooth.storage.repos import CaptureRepo

logger = structlog.get_logger(__name__)


def run_retention_sweep_once(
    db: sqlite3.Connection, captures_dir: Path, config: RetentionConfig
) -> int:
    """Delete every capture (DB row + on-disk JPEGs) older than
    `config.max_age_days`. Returns the number of captures deleted.

    No-ops (returns 0) when `config.enabled` is `False` — callers can invoke
    this unconditionally without their own enabled-check, matching how
    `run_retention_sweep`'s loop below does the same.

    File deletion is tolerant of a missing file: a capture might have only a
    full JPEG and no `-preview.jpg` (or vice versa, or a file already
    manually removed), and one missing file must never abort the sweep for
    every other capture. Each capture's DB row and files are still deleted
    as a unit — if the DB delete succeeds we don't leave orphaned files
    around by skipping them, and vice versa (best-effort: DB row is the
    source of truth for "is this capture retained").
    """
    if not config.enabled:
        return 0

    cutoff = datetime.now(UTC) - timedelta(days=config.max_age_days)
    cutoff_iso = cutoff.isoformat()

    repo = CaptureRepo(db)
    stale_ids = repo.list_older_than(cutoff_iso)

    for capture_id in stale_ids:
        for suffix in (".jpg", "-preview.jpg"):
            path = captures_dir / f"{capture_id}{suffix}"
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "retention_file_delete_failed",
                    capture_id=capture_id,
                    path=str(path),
                    error=str(exc),
                )
        repo.delete(capture_id)
        logger.info("retention_capture_deleted", capture_id=capture_id)

    return len(stale_ids)


async def run_retention_sweep(
    db: sqlite3.Connection,
    captures_dir: Path,
    config: RetentionConfig,
    interval_s: float = 3600.0,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Periodic sweep loop — runs `run_retention_sweep_once` every
    `interval_s` until `stop_event` is set. The blocking sqlite/filesystem
    work is run via `asyncio.to_thread` so a large sweep doesn't stall the
    event loop, matching this codebase's established convention
    (`pipeline/pool.py`'s `RenderPool`, `delivery/backend.py`'s upload
    backends).
    """
    event = stop_event or asyncio.Event()
    while not event.is_set():
        try:
            deleted = await asyncio.to_thread(run_retention_sweep_once, db, captures_dir, config)
            if deleted:
                logger.info("retention_sweep_complete", deleted=deleted)
        except Exception:  # noqa: BLE001 - a sweep failure must not kill the loop
            logger.exception("retention_sweep_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=interval_s)
