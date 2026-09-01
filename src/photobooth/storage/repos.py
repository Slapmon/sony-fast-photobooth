"""Thin write-path repositories over the schema in storage/db.py — plain
parameterized SQL, no ORM, matching the project's stated approach. No
query/list API is provided here: that's a future phase's concern, not this
one's.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


class SessionRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def create(self, session_id: str, event_id: str, state: str) -> None:
        self._db.execute(
            "INSERT INTO sessions (id, event_id, state, created_at) VALUES (?, ?, ?, ?)",
            (session_id, event_id, state, datetime.now(UTC).isoformat()),
        )
        self._db.commit()

    def update_state(self, session_id: str, state: str) -> None:
        self._db.execute(
            "UPDATE sessions SET state = ? WHERE id = ?",
            (state, session_id),
        )
        self._db.commit()


class CaptureRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def create(self, capture_id: str, session_id: str) -> None:
        self._db.execute(
            "INSERT INTO captures (id, session_id, created_at) VALUES (?, ?, ?)",
            (capture_id, session_id, datetime.now(UTC).isoformat()),
        )
        self._db.commit()
