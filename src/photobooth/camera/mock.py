"""Fixture-driven mock camera backend for laptop development.

Reads real sample JPEGs from fixtures/shots/ so the rest of the pipeline
(display sizing, compositing) sees real bytes, not synthetic placeholders.
Timings are configurable so they can be calibrated from real Phase 0
measurements once those exist (IMPLEMENTATION_PLAN.md §3).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from PIL import Image

from photobooth.camera.protocol import (
    CameraBackend,
    CameraDisconnectedError,
    CapturedImage,
    ImageKind,
)


class MockBackend(CameraBackend):
    def __init__(
        self,
        fixtures_dir: Path,
        trigger_delay_ms: int = 250,
        thumb_latency_ms: int = 150,
        full_download_mbps: float = 40.0,
    ) -> None:
        self._fixtures_dir = fixtures_dir
        self._trigger_delay_s = trigger_delay_ms / 1000
        self._thumb_latency_s = thumb_latency_ms / 1000
        self._full_download_mbps = full_download_mbps
        self._connected = False
        self._captures: dict[str, bytes] = {}

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise CameraDisconnectedError("mock camera not connected")

    def trigger_capture(self) -> str:
        self._require_connected()
        time.sleep(self._trigger_delay_s)
        capture_id = str(uuid.uuid4())
        shot = self._pick_fixture()
        self._captures[capture_id] = shot.read_bytes()
        return capture_id

    def _pick_fixture(self) -> Path:
        shots = sorted(self._fixtures_dir.glob("*.jpg"))
        if not shots:
            raise FileNotFoundError(
                f"no fixture JPEGs in {self._fixtures_dir} — add at least one sample shot"
            )
        return shots[hash(uuid.uuid4()) % len(shots)]

    def download_preview(self, capture_id: str) -> CapturedImage | None:
        self._require_connected()
        time.sleep(self._thumb_latency_s)
        data = self._captures[capture_id]
        with Image.open(__import__("io").BytesIO(data)) as im:
            im.thumbnail((1616, 1080))
            width, height = im.size
        return CapturedImage(kind=ImageKind.PREVIEW, data=data, width=width, height=height)

    def download_full(self, capture_id: str) -> CapturedImage:
        self._require_connected()
        data = self._captures[capture_id]
        transfer_s = (len(data) / (1024 * 1024)) / self._full_download_mbps
        time.sleep(transfer_s)
        with Image.open(__import__("io").BytesIO(data)) as im:
            width, height = im.size
        return CapturedImage(kind=ImageKind.FULL, data=data, width=width, height=height)

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()
