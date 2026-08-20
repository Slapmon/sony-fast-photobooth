"""CameraBackend contract — every backend (mock, gphoto) implements this,
and tests/contract/test_camera_backend.py runs the same assertions against
both. See IMPLEMENTATION_PLAN.md §4.5 and photobooth-plan.md §3.2/§5.

A backend owns the physical/simulated camera handle for its entire lifetime.
Calls are blocking by design — they run inside the dedicated camera-worker
process, never inside the app's async event loop (photobooth-plan.md §3.2).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum


class ImageKind(StrEnum):
    PREVIEW = "preview"  # small embedded JPEG (PTP GetThumb), if supported
    FULL = "full"


@dataclass(frozen=True, slots=True)
class CapturedImage:
    kind: ImageKind
    data: bytes
    width: int
    height: int


class CameraError(Exception):
    """Base for all camera backend failures (disconnect, timeout, busy, ...)."""


class CameraDisconnectedError(CameraError):
    pass


class CameraBackend(ABC):
    """One instance owns the camera handle for its entire lifetime."""

    @abstractmethod
    def connect(self) -> None:
        """Open the camera handle. Called once at worker startup."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release the camera handle. Called on clean shutdown only."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Cheap liveness check for the preflight/heartbeat, not a full capture."""

    @abstractmethod
    def trigger_capture(self) -> str:
        """Fire the shutter. Returns a capture_id used to correlate downloads."""

    @abstractmethod
    def download_preview(self, capture_id: str) -> CapturedImage | None:
        """Fetch the embedded thumbnail (PTP GetThumb), if the body supports it.

        Returns None rather than raising when the body has no preview support —
        callers fall back to download_full + shrink-on-load (photobooth-plan.md
        §3.3, IMPLEMENTATION_PLAN.md §5 fallback list).
        """

    @abstractmethod
    def download_full(self, capture_id: str) -> CapturedImage:
        """Fetch the full-resolution image for the given capture."""

    @abstractmethod
    def reconnect(self) -> None:
        """Recover from a dropped session. Must be safe to call after any error."""
