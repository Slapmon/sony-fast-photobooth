"""Tests for storage/retention.py — capture retention sweep
(IMPLEMENTATION_PLAN.md T-4.5)."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from photobooth.config.models import RetentionConfig
from photobooth.storage import db as storage_db
from photobooth.storage.retention import run_retention_sweep, run_retention_sweep_once


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.executescript(storage_db.SCHEMA)
    return c


def _insert_capture(conn: sqlite3.Connection, capture_id: str, created_at: datetime) -> None:
    """Bypass CaptureRepo.create() (which always stamps `now`) so we can
    plant captures with an arbitrary `created_at` for cutoff testing."""
    conn.execute(
        "INSERT INTO sessions (id, event_id, state, created_at) VALUES (?, 'evt', 'IDLE', ?)",
        (f"session-{capture_id}", created_at.isoformat()),
    )
    conn.execute(
        "INSERT INTO captures (id, session_id, created_at) VALUES (?, ?, ?)",
        (capture_id, f"session-{capture_id}", created_at.isoformat()),
    )
    conn.commit()


def test_sweep_deletes_captures_older_than_cutoff(conn: sqlite3.Connection, tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=40)
    _insert_capture(conn, "old-capture", old)
    (tmp_path / "old-capture.jpg").write_bytes(b"old full")
    (tmp_path / "old-capture-preview.jpg").write_bytes(b"old preview")

    config = RetentionConfig(enabled=True, max_age_days=30)
    deleted = run_retention_sweep_once(conn, tmp_path, config)

    assert deleted == 1
    row = conn.execute("SELECT id FROM captures WHERE id = 'old-capture'").fetchone()
    assert row is None
    assert not (tmp_path / "old-capture.jpg").exists()
    assert not (tmp_path / "old-capture-preview.jpg").exists()


def test_sweep_leaves_newer_captures_alone(conn: sqlite3.Connection, tmp_path: Path) -> None:
    recent = datetime.now(UTC) - timedelta(days=1)
    _insert_capture(conn, "recent-capture", recent)
    (tmp_path / "recent-capture.jpg").write_bytes(b"fresh")

    config = RetentionConfig(enabled=True, max_age_days=30)
    deleted = run_retention_sweep_once(conn, tmp_path, config)

    assert deleted == 0
    row = conn.execute("SELECT id FROM captures WHERE id = 'recent-capture'").fetchone()
    assert row is not None
    assert (tmp_path / "recent-capture.jpg").exists()


def test_sweep_tolerates_missing_file(conn: sqlite3.Connection, tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=40)
    _insert_capture(conn, "no-file-capture", old)
    # Deliberately don't create any file on disk for this capture.

    config = RetentionConfig(enabled=True, max_age_days=30)
    deleted = run_retention_sweep_once(conn, tmp_path, config)

    assert deleted == 1
    row = conn.execute("SELECT id FROM captures WHERE id = 'no-file-capture'").fetchone()
    assert row is None


def test_sweep_noops_when_disabled(conn: sqlite3.Connection, tmp_path: Path) -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    _insert_capture(conn, "old-capture", old)
    (tmp_path / "old-capture.jpg").write_bytes(b"old full")

    config = RetentionConfig(enabled=False, max_age_days=30)
    deleted = run_retention_sweep_once(conn, tmp_path, config)

    assert deleted == 0
    row = conn.execute("SELECT id FROM captures WHERE id = 'old-capture'").fetchone()
    assert row is not None
    assert (tmp_path / "old-capture.jpg").exists()


def test_sweep_mixed_ages_deletes_only_the_old_one(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    old = datetime.now(UTC) - timedelta(days=90)
    recent = datetime.now(UTC) - timedelta(days=2)
    _insert_capture(conn, "old-one", old)
    _insert_capture(conn, "new-one", recent)
    (tmp_path / "old-one.jpg").write_bytes(b"x")
    (tmp_path / "new-one.jpg").write_bytes(b"y")

    config = RetentionConfig(enabled=True, max_age_days=30)
    deleted = run_retention_sweep_once(conn, tmp_path, config)

    assert deleted == 1
    ids = {r[0] for r in conn.execute("SELECT id FROM captures").fetchall()}
    assert ids == {"new-one"}
    assert not (tmp_path / "old-one.jpg").exists()
    assert (tmp_path / "new-one.jpg").exists()


async def test_run_retention_sweep_loop_runs_and_stops(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    old = datetime.now(UTC) - timedelta(days=40)
    _insert_capture(conn, "old-capture", old)
    (tmp_path / "old-capture.jpg").write_bytes(b"old")

    config = RetentionConfig(enabled=True, max_age_days=30)
    stop_event = asyncio.Event()

    async def _stop_after_first_sweep() -> None:
        # Poll until the sweep has run once, then stop the loop.
        for _ in range(200):
            row = conn.execute("SELECT id FROM captures WHERE id = 'old-capture'").fetchone()
            if row is None:
                stop_event.set()
                return
            await asyncio.sleep(0.01)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_retention_sweep(conn, tmp_path, config, interval_s=0.01, stop_event=stop_event),
            _stop_after_first_sweep(),
        ),
        timeout=5.0,
    )

    row = conn.execute("SELECT id FROM captures WHERE id = 'old-capture'").fetchone()
    assert row is None


async def test_run_retention_sweep_survives_a_failing_iteration(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single sweep failure must not kill the loop — the next iteration
    should still run."""
    import photobooth.storage.retention as retention_module

    calls = {"n": 0}
    real_once = retention_module.run_retention_sweep_once

    def _flaky_once(
        db: sqlite3.Connection, captures_dir: Path, config: RetentionConfig
    ) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated sweep failure")
        return real_once(db, captures_dir, config)

    monkeypatch.setattr(retention_module, "run_retention_sweep_once", _flaky_once)

    old = datetime.now(UTC) - timedelta(days=40)
    _insert_capture(conn, "old-capture", old)

    config = RetentionConfig(enabled=True, max_age_days=30)
    stop_event = asyncio.Event()

    async def _stop_once_deleted() -> None:
        for _ in range(300):
            if calls["n"] >= 2:
                row = conn.execute(
                    "SELECT id FROM captures WHERE id = 'old-capture'"
                ).fetchone()
                if row is None:
                    stop_event.set()
                    return
            await asyncio.sleep(0.01)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(
            run_retention_sweep(conn, tmp_path, config, interval_s=0.01, stop_event=stop_event),
            _stop_once_deleted(),
        ),
        timeout=5.0,
    )

    assert calls["n"] >= 2
