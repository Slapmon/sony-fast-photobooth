"""Fixture-driven mock camera backend for laptop development.

Reads real sample JPEGs from fixtures/shots/ so the rest of the pipeline
(display sizing, compositing) sees real bytes, not synthetic placeholders.
Timings are configurable so they can be calibrated from real Phase 0
measurements once those exist (IMPLEMENTATION_PLAN.md §3).

Fault injection (IMPLEMENTATION_PLAN.md §4.4) is built in here rather than
bolted on later: `disconnect_every_n` simulates the Sony PTP-session-drop
risk the plan flags as its #1 reliability concern (photobooth-plan.md §12),
`download_timeout_pct`/`slow_download_pct` simulate a flaky USB transfer.
All default to off (0 / None) so existing callers see no behaviour change.
"""

from __future__ import annotations

import random
import time
import uuid
from pathlib import Path

from PIL import Image

from photobooth.camera.protocol import (
    CameraBackend,
    CameraDisconnectedError,
    CameraError,
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
        disconnect_every_n: int | None = None,
        download_timeout_pct: float = 0.0,
        slow_download_pct: float = 0.0,
        rng: random.Random | None = None,
    ) -> None:
        self._fixtures_dir = fixtures_dir
        self._trigger_delay_s = trigger_delay_ms / 1000
        self._thumb_latency_s = thumb_latency_ms / 1000
        self._full_download_mbps = full_download_mbps
        self._disconnect_every_n = disconnect_every_n
        self._download_timeout_pct = download_timeout_pct
        self._slow_download_pct = slow_download_pct
        self._rng = rng if rng is not None else random.Random()
        self._connected = False
        self._captures: dict[str, bytes] = {}
        self._shot_count = 0

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def _require_connected(self) -> None:
        if not self._connected:
            raise CameraDisconnectedError("mock camera not connected")

    def trigger_autofocus(self) -> None:
        self._require_connected()

    def trigger_capture(self) -> str:
        self._require_connected()
        self._shot_count += 1
        if self._disconnect_every_n and self._shot_count % self._disconnect_every_n == 0:
            self._connected = False
            raise CameraDisconnectedError(
                f"simulated PTP session drop after {self._shot_count} shots "
                f"(disconnect_every_n={self._disconnect_every_n})"
            )
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

    def _maybe_inject_download_fault(self) -> None:
        if self._download_timeout_pct and self._rng.random() * 100 < self._download_timeout_pct:
            raise CameraError("simulated download timeout (download_timeout_pct)")

    def _download_delay_multiplier(self) -> float:
        if self._slow_download_pct and self._rng.random() * 100 < self._slow_download_pct:
            return 5.0  # simulated slow/degraded USB transfer
        return 1.0

    def download_preview(self, capture_id: str) -> CapturedImage | None:
        self._require_connected()
        self._maybe_inject_download_fault()
        time.sleep(self._thumb_latency_s * self._download_delay_multiplier())
        data = self._captures[capture_id]
        with Image.open(__import__("io").BytesIO(data)) as im:
            im.thumbnail((1616, 1080))
            width, height = im.size
        return CapturedImage(kind=ImageKind.PREVIEW, data=data, width=width, height=height)

    def download_full(self, capture_id: str) -> CapturedImage:
        self._require_connected()
        self._maybe_inject_download_fault()
        data = self._captures[capture_id]
        transfer_s = (len(data) / (1024 * 1024)) / self._full_download_mbps
        time.sleep(transfer_s * self._download_delay_multiplier())
        with Image.open(__import__("io").BytesIO(data)) as im:
            width, height = im.size
        return CapturedImage(kind=ImageKind.FULL, data=data, width=width, height=height)

    def reconnect(self) -> None:
        self.disconnect()
        self.connect()
