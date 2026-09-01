"""Real camera backend — python-gphoto2 against a persistent PTP session.

Non-negotiable per photobooth-plan.md §3.2: one long-lived process owns the
camera handle for the whole event; this backend is only ever instantiated
inside camera/worker.py, never imported into the async web app.

Import of `gphoto2` is deferred into __init__ so this module can be imported
(for typing/tests) on machines without libgphoto2 installed — it only fails
loudly the moment someone actually tries to construct a GphotoBackend.

Config widget names below were confirmed against the real a6400 via
`gphoto2 --list-config` / `--get-config` (2026-09-01, Debian 13/aarch64,
libgphoto2 2.5.31): autofocus and capture are TOGGLE actions under
/main/actions, image size is a RADIO under /main/imgsettings with choices
Large/Medium/Small.
"""

from __future__ import annotations

import contextlib
import io
import time
from typing import TYPE_CHECKING, Literal

from PIL import Image

if TYPE_CHECKING:
    import gphoto2 as gp

from photobooth.camera.protocol import (
    CameraBackend,
    CameraDisconnectedError,
    CameraError,
    CapturedImage,
    ImageKind,
)

JpegSize = Literal["S", "M", "L"]

_SIZE_LABELS: dict[JpegSize, str] = {"S": "Small", "M": "Medium", "L": "Large"}

# gp_camera_wait_for_event poll granularity and overall budget while waiting
# for GP_EVENT_FILE_ADDED after a trigger_capture(). See IMPLEMENTATION_PLAN.md
# §6 T-C4 — this is the "trigger_capture + wait_for_event" path, benchmarked
# against the simpler blocking capture() call.
_EVENT_POLL_MS = 1000
_EVENT_WAIT_BUDGET_S = 10.0


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


class GphotoBackend(CameraBackend):
    def __init__(self, jpeg_size: JpegSize = "S") -> None:
        try:
            import gphoto2 as gp
        except ImportError as exc:
            raise RuntimeError(
                "python-gphoto2 is not installed — install the `pi` extra "
                "on a machine with libgphoto2 present (see IMPLEMENTATION_PLAN.md §1)"
            ) from exc
        self._gp = gp
        self._jpeg_size = jpeg_size
        self._camera: gp.Camera | None = None
        # capture_id -> gp.CameraFilePath, so download_preview/download_full
        # can address the same in-camera file. Cleared by download_full
        # (which also deletes the file on-camera when possible).
        self._pending: dict[str, gp.CameraFilePath] = {}

    def connect(self) -> None:
        gp = self._gp
        camera = gp.Camera()
        try:
            camera.init()
        except gp.GPhoto2Error as exc:
            # the expected failure mode while retrying reconnect() with the
            # camera still unplugged -- callers rely on our own type here.
            raise CameraDisconnectedError(str(exc)) from exc
        self._camera = camera
        self._apply_image_size()

    def _apply_image_size(self) -> None:
        assert self._camera is not None
        gp = self._gp
        config = self._camera.get_config()
        try:
            widget = config.get_child_by_name("imagesize")
        except gp.GPhoto2Error:
            return  # body doesn't expose this control — proceed with its default
        widget.set_value(_SIZE_LABELS[self._jpeg_size])
        self._camera.set_config(config)

    def disconnect(self) -> None:
        if self._camera is not None:
            self._camera.exit()
            self._camera = None
        self._pending.clear()

    def is_connected(self) -> bool:
        if self._camera is None:
            return False
        try:
            self._camera.get_summary()
        except self._gp.GPhoto2Error:
            return False
        return True

    def _require_camera(self) -> gp.Camera:
        if self._camera is None:
            raise CameraDisconnectedError("camera not connected")
        return self._camera

    def trigger_autofocus(self) -> None:
        """Standalone AF trigger, not tied to a capture.

        Separate from trigger_capture() because the booth pre-focuses once at
        a fixed distance (photobooth-plan.md §3.4) — this exists for the
        Phase 0 benchmark tools to test AF behaviour in isolation.
        """
        camera = self._require_camera()
        gp = self._gp
        config = camera.get_config()
        widget = config.get_child_by_name("autofocus")
        widget.set_value(1)
        try:
            camera.set_config(config)
        except gp.GPhoto2Error as exc:
            raise CameraError(f"autofocus trigger failed: {exc}") from exc

    def trigger_capture(self) -> str:
        camera = self._require_camera()
        gp = self._gp
        try:
            camera.trigger_capture()

            deadline = time.monotonic() + _EVENT_WAIT_BUDGET_S
            while time.monotonic() < deadline:
                event_type, event_data = camera.wait_for_event(_EVENT_POLL_MS)
                if event_type == gp.GP_EVENT_FILE_ADDED:
                    capture_id = f"{event_data.folder}/{event_data.name}"
                    self._pending[capture_id] = event_data
                    return capture_id
                if event_type == gp.GP_EVENT_CAPTURE_COMPLETE:
                    continue  # some bodies fire this before the file-added event
        except gp.GPhoto2Error as exc:
            # covers both the trigger command itself and the event wait that
            # follows -- a camera unplugged mid-wait fails here, not in
            # trigger_capture() itself, so both must map to our own type.
            raise CameraDisconnectedError(str(exc)) from exc
        raise CameraError("timed out waiting for GP_EVENT_FILE_ADDED after trigger_capture")

    def download_preview(self, capture_id: str) -> CapturedImage | None:
        camera = self._require_camera()
        gp = self._gp
        path = self._pending[capture_id]
        try:
            camera_file = camera.file_get(path.folder, path.name, gp.GP_FILE_TYPE_PREVIEW)
        except gp.GPhoto2Error:
            return None  # body has no embedded preview — caller falls back to full+shrink
        data = bytes(memoryview(camera_file.get_data_and_size()))
        if not data:
            return None
        width, height = _jpeg_dimensions(data)
        return CapturedImage(kind=ImageKind.PREVIEW, data=data, width=width, height=height)

    def download_full(self, capture_id: str) -> CapturedImage:
        camera = self._require_camera()
        gp = self._gp
        path = self._pending.pop(capture_id)
        try:
            camera_file = camera.file_get(path.folder, path.name, gp.GP_FILE_TYPE_NORMAL)
        except gp.GPhoto2Error as exc:
            raise CameraDisconnectedError(str(exc)) from exc
        data = bytes(memoryview(camera_file.get_data_and_size()))
        with contextlib.suppress(gp.GPhoto2Error):
            camera.file_delete(path.folder, path.name)  # sdram bodies may not support/need this
        width, height = _jpeg_dimensions(data)
        return CapturedImage(kind=ImageKind.FULL, data=data, width=width, height=height)

    def reconnect(self) -> None:
        self.disconnect()
        time.sleep(1.0)
        self.connect()
