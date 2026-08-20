"""Async client used by the FastAPI app to talk to the camera-worker process.

The worker owns the blocking CameraBackend calls (protocol.py); this client
is the async-safe side of the IPC boundary described in photobooth-plan.md
§3.2 and IMPLEMENTATION_PLAN.md §1 (UNIX socket, length-prefixed msgspec
frames). Not yet wired to a real socket — see T-1.3/T-1.6.
"""

from __future__ import annotations

from photobooth.camera.protocol import CapturedImage


class CameraWorkerClient:
    """Talks to camera/worker.py over a UNIX socket. Stub until T-1.3/T-1.6."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path

    async def trigger_capture(self) -> str:
        raise NotImplementedError("T-1.3: define IPC message types first")

    async def download_preview(self, capture_id: str) -> CapturedImage | None:
        raise NotImplementedError

    async def download_full(self, capture_id: str) -> CapturedImage:
        raise NotImplementedError

    async def get_status(self) -> dict[str, object]:
        raise NotImplementedError
