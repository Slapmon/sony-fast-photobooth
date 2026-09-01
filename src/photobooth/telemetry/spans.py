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


def record_duration(
    conn: sqlite3.Connection, name: str, capture_id: str, duration_s: float, **meta: object
) -> None:
    """Record a span whose duration was measured elsewhere (e.g. the
    browser's own decode timer, reported back over the WebSocket) rather
    than by wrapping the call here. t_end is "now"; t_start is back-computed
    from the reported duration — server and browser clocks aren't the same
    clock, so only the delta is meaningful, same as for span() above.
    """
    import json

    t_end = time.monotonic()
    conn.execute(
        "INSERT INTO spans (capture_id, name, t_start, t_end, meta_json) VALUES (?, ?, ?, ?, ?)",
        (capture_id, name, t_end - duration_s, t_end, json.dumps(meta)),
    )
    conn.commit()
