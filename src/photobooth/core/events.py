"""Typed events carried on the WebSocket bus between the app and the frontend."""

from __future__ import annotations

from dataclasses import dataclass

from photobooth.core.state import SessionState


@dataclass(frozen=True, slots=True)
class StateChanged:
    session_id: str
    state: SessionState


@dataclass(frozen=True, slots=True)
class PreviewReady:
    session_id: str
    capture_id: str
    image_url: str


@dataclass(frozen=True, slots=True)
class FullImageReady:
    session_id: str
    capture_id: str
    image_url: str


Event = StateChanged | PreviewReady | FullImageReady
