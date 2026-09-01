"""SQLite (WAL mode) connection + schema bootstrap.

Every durable queue (uploads, print jobs) and every trace span lives here so
a crash or power loss doesn't lose a guest's photos or pending work
(photobooth-plan.md §5 principle 5). Plain sqlite3 + a thin repo layer, no
ORM (IMPLEMENTATION_PLAN.md §1).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spans (
    capture_id TEXT NOT NULL,
    name TEXT NOT NULL,
    t_start REAL NOT NULL,
    t_end REAL,
    meta_json TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the app's async event loop can hop OS threads
    # (e.g. FastAPI's threadpool for sync dependencies, anyio portals in
    # tests) while callers still serialize access to this one connection —
    # sqlite3's default same-thread check would otherwise reject that even
    # though nothing here actually uses the connection concurrently.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn
