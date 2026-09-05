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

    def set_share_token(self, session_id: str, token: str) -> None:
        """Attach an unguessable share-link token to a session
        (IMPLEMENTATION_PLAN.md T-4.3). Additive — doesn't touch the
        write-path methods above.

        Integration note for the wave that wires this into the live capture
        flow (web/session.py's SessionManager, deliberately NOT edited by
        this task): call this once per session, after `FullImageReady` is
        broadcast for the *last* shot of the session (i.e. right before or
        alongside the COUNTDOWN/CAPTURING -> REVIEW transition in
        `capture()`), passing a freshly generated
        `secrets.token_urlsafe(18)` (or similar, 24+ chars) as `token`. Doing
        it there (rather than at `arm()`/session creation) means a session
        that never completes a capture never gets a share link — matching
        the `share_token` column's NULL default and this repo's read side
        (`get_by_share_token` — see below), which the /s/{token} routes in
        web/routers/share.py already assume as "valid token, no captures
        yet" only ever describes a session with SOME capture."""
        self._db.execute(
            "UPDATE sessions SET share_token = ? WHERE id = ?",
            (token, session_id),
        )
        self._db.commit()

    def get_by_share_token(self, token: str) -> dict[str, str] | None:
        """Look up a session by its share token (web/routers/share.py). None
        if no session has this token — callers must turn that into a generic
        404 (photobooth-plan.md §11: don't let a response distinguish
        "wrong token" from any other failure mode)."""
        row = self._db.execute(
            "SELECT id, event_id, state, created_at FROM sessions WHERE share_token = ?",
            (token,),
        ).fetchone()
        if row is None:
            return None
        return {"id": row[0], "event_id": row[1], "state": row[2], "created_at": row[3]}


class CaptureRepo:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def create(self, capture_id: str, session_id: str, *, is_deliverable: bool = True) -> None:
        """`is_deliverable=False` marks a raw per-slot shot that only feeds
        a composite (multi-shot collage/strip) — not directly shown to a
        guest. Call `mark_deliverable()` once the real deliverable (the
        composite, or this same row for a 1-slot template) is known. See
        `web/session.py`'s `capture()`."""
        self._db.execute(
            "INSERT INTO captures (id, session_id, created_at, is_deliverable) VALUES (?, ?, ?, ?)",
            (capture_id, session_id, datetime.now(UTC).isoformat(), int(is_deliverable)),
        )
        self._db.commit()

    def mark_deliverable(self, capture_id: str) -> None:
        self._db.execute("UPDATE captures SET is_deliverable = 1 WHERE id = ?", (capture_id,))
        self._db.commit()

    def list_older_than(self, cutoff_iso: str) -> list[str]:
        """Read-only listing for the retention sweep (IMPLEMENTATION_PLAN.md
        T-4.5). Returns capture ids whose `created_at` is strictly older than
        `cutoff_iso` (an ISO-8601 timestamp, comparable lexicographically to
        the stored `created_at` since both are `datetime.isoformat()` in UTC —
        same convention `create()` already uses). Additive, doesn't touch the
        write-path methods above.
        """
        rows = self._db.execute(
            "SELECT id FROM captures WHERE created_at < ?",
            (cutoff_iso,),
        ).fetchall()
        return [row[0] for row in rows]

    def delete(self, capture_id: str) -> None:
        """Delete one capture's DB row (IMPLEMENTATION_PLAN.md T-4.5). Does
        not touch on-disk files — that's the retention sweep's job
        (storage/retention.py), kept separate so this repo stays pure DB
        access, matching the rest of this file.
        """
        self._db.execute("DELETE FROM captures WHERE id = ?", (capture_id,))
        self._db.commit()

    def list_by_event(self, event_id: str) -> list[tuple[str, str]]:
        """Read-only listing for the gallery (IMPLEMENTATION_PLAN.md T-3.4).

        Captures don't carry `event_id` directly, only via their session, so
        this joins through `sessions`. Additive — doesn't touch the
        write-path methods above. Returns `(capture_id, created_at)` pairs,
        most recent first.
        """
        rows = self._db.execute(
            "SELECT captures.id, captures.created_at FROM captures "
            "JOIN sessions ON captures.session_id = sessions.id "
            "WHERE sessions.event_id = ? AND captures.is_deliverable = 1 "
            "ORDER BY captures.created_at DESC",
            (event_id,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def get_session_id(self, capture_id: str) -> str | None:
        """capture_id -> owning session_id lookup (IMPLEMENTATION_PLAN.md
        T-4.9, admin reprint) — the admin reprint action knows only a
        capture_id (a text field, no "pick from recent captures" UI yet) but
        `PrinterBackend.submit()` needs a session_id. None if no capture has
        this id. Additive — doesn't touch the write-path methods above.
        """
        row = self._db.execute(
            "SELECT session_id FROM captures WHERE id = ?",
            (capture_id,),
        ).fetchone()
        return row[0] if row is not None else None

    def list_by_session(self, session_id: str) -> list[tuple[str, str]]:
        """Read-only listing for the per-session share link
        (IMPLEMENTATION_PLAN.md T-4.3, web/routers/share.py) — a single
        guest's own capture(s), not a whole event's gallery. Mirrors
        `list_by_event`'s shape: `(capture_id, created_at)` pairs, most
        recent first. Additive — doesn't touch the write-path methods above.
        """
        rows = self._db.execute(
            "SELECT id, created_at FROM captures "
            "WHERE session_id = ? AND is_deliverable = 1 "
            "ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
