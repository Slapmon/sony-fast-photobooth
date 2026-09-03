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
    created_at TEXT NOT NULL,
    -- Per-guest share link token (IMPLEMENTATION_PLAN.md T-4.3), distinct
    -- from the per-EVENT gallery in web/routers/gallery.py. NULL until a
    -- later wave calls SessionRepo.set_share_token (see storage/repos.py) —
    -- not every session necessarily gets one, e.g. one that never completed
    -- a capture. Cryptographically random (secrets.token_urlsafe), not a
    -- sequential id, per photobooth-plan.md §11's "unguessable tokens"
    -- principle. UNIQUE so a lookup by token can never resolve to more than
    -- one session.
    share_token TEXT UNIQUE
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

-- Generic durable job queue (IMPLEMENTATION_PLAN.md T-4.1) — used for
-- uploads (T-4.2), print jobs (T-4.7) and any future async work that needs
-- claim/retry/dead-letter semantics. `kind` is a plain discriminator the
-- queue itself never interprets. See storage/queue.py.
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_attempt_at REAL NOT NULL,
    claimed_at REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs (status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_kind_status ON jobs (kind, status);
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
