"""Trace spans — mandatory instrumentation per IMPLEMENTATION_PLAN.md §4.1.

This is the tool that answers "where did the 10 seconds go." Every capture
gets a capture_id; every pipeline stage opens a span here. Spans land in
SQLite (storage/db.py) and are surfaced at /debug/traces.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def span(conn: sqlite3.Connection, name: str, capture_id: str, **meta: object) -> Iterator[None]:
    import json

    t_start = time.monotonic()
    try:
        yield
    finally:
        t_end = time.monotonic()
        conn.execute(
            "INSERT INTO spans (capture_id, name, t_start, t_end, meta_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (capture_id, name, t_start, t_end, json.dumps(meta)),
        )
        conn.commit()
