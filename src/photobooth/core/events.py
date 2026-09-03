"""Typed events carried on the WebSocket bus between the app and the frontend."""

from __future__ import annotations

import msgspec

from photobooth.core.state import SessionState


class StateChanged(msgspec.Struct, tag=True):
    session_id: str
    state: SessionState
    # Set only on the transition into REVIEW for a session that issued a
    # share token (T-4.3/T-4.4's `_issue_share_token_and_enqueue_uploads`),
    # i.e. only once uploads have been enqueued for the guest's shot(s).
    # None on every other transition, and None here too if the
    # SessionManager wasn't built with a job_queue (T-4.3 is a no-op then).
    # Lets the frontend build `/s/{share_token}/qr.png` for the review
    # screen (T-4.3's frontend half) without a second round trip.
    share_token: str | None = None


class CountdownStarted(msgspec.Struct, tag=True):
    session_id: str
    duration_s: float
    # 0-based index of the shot this countdown precedes, and the total shot
    # count for the session's active template (T-2.6) — a single-shot
    # session is shot_index=0, shot_count=1, same as before this field
    # existed. Lets the frontend show "2 of 4" during collage mode.
    shot_index: int = 0
    shot_count: int = 1


class PreviewReady(msgspec.Struct, tag=True):
    session_id: str
    capture_id: str
    image_url: str
    shot_index: int = 0
    shot_count: int = 1


class FullImageReady(msgspec.Struct, tag=True):
    session_id: str
    capture_id: str
    image_url: str


class CaptureFailed(msgspec.Struct, tag=True):
    session_id: str
    message: str


Event = StateChanged | CountdownStarted | PreviewReady | FullImageReady | CaptureFailed

_encoder = msgspec.json.Encoder()


def encode_event(event: Event) -> bytes:
    return _encoder.encode(event)
