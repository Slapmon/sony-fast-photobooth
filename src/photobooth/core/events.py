"""Typed events carried on the WebSocket bus between the app and the frontend."""

from __future__ import annotations

import msgspec

from photobooth.core.state import SessionState


class StateChanged(msgspec.Struct, tag=True):
    session_id: str
    state: SessionState


class CountdownStarted(msgspec.Struct, tag=True):
    session_id: str
    duration_s: float


class PreviewReady(msgspec.Struct, tag=True):
    session_id: str
    capture_id: str
    image_url: str


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
