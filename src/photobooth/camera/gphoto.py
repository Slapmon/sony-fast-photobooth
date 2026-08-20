"""Real camera backend — python-gphoto2 against a persistent PTP session.

Non-negotiable per photobooth-plan.md §3.2: one long-lived process owns the
camera handle for the whole event; this backend is only ever instantiated
inside camera/worker.py, never imported into the async web app.

Import of `gphoto2` is deferred into __init__ so this module can be imported
(for typing/tests) on machines without libgphoto2 installed — it only fails
loudly the moment someone actually tries to construct a GphotoBackend.
"""

from __future__ import annotations

from photobooth.camera.protocol import CameraBackend, CapturedImage


class GphotoBackend(CameraBackend):
    def __init__(self, jpeg_size: str = "S") -> None:
        try:
            import gphoto2  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "python-gphoto2 is not installed — install the `pi` extra "
                "on a machine with libgphoto2 present (see IMPLEMENTATION_PLAN.md §1)"
            ) from exc
        self._jpeg_size = jpeg_size
        self._camera: object | None = None

    def connect(self) -> None:
        raise NotImplementedError("T-1.7: real PTP session open, see IMPLEMENTATION_PLAN.md §7")

    def disconnect(self) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    def trigger_capture(self) -> str:
        raise NotImplementedError

    def download_preview(self, capture_id: str) -> CapturedImage | None:
        raise NotImplementedError("depends on T-C3: does the a6400 support GP_FILE_TYPE_PREVIEW")

    def download_full(self, capture_id: str) -> CapturedImage:
        raise NotImplementedError

    def reconnect(self) -> None:
        raise NotImplementedError("T-C8: measure recovery time, consider uhubctl power-cycle")
