"""Runs the same assertions against every CameraBackend implementation.

This is what makes the mock trustworthy — if it drifts from real hardware
behaviour, this suite is meant to catch it once GphotoBackend is real and
run with --hardware (IMPLEMENTATION_PLAN.md §4.5).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from photobooth.camera.mock import MockBackend
from photobooth.camera.protocol import CameraBackend, CameraDisconnectedError


@pytest.fixture(params=["mock"])
def backend(request: pytest.FixtureRequest, fixtures_dir: Path) -> Iterator[CameraBackend]:
    if request.param == "mock":
        b: CameraBackend = MockBackend(
            fixtures_dir=fixtures_dir, trigger_delay_ms=0, thumb_latency_ms=0
        )
    else:  # pragma: no cover - real hardware, opt in with --hardware
        pytest.skip("hardware backend not selected")
    b.connect()
    yield b
    b.disconnect()


def test_connect_reports_connected(backend: CameraBackend) -> None:
    assert backend.is_connected()


def test_trigger_capture_returns_id(backend: CameraBackend) -> None:
    capture_id = backend.trigger_capture()
    assert capture_id


def test_download_full_after_trigger(backend: CameraBackend) -> None:
    capture_id = backend.trigger_capture()
    image = backend.download_full(capture_id)
    assert image.data
    assert image.width > 0
    assert image.height > 0


def test_operations_fail_when_disconnected(backend: CameraBackend) -> None:
    backend.disconnect()
    with pytest.raises(CameraDisconnectedError):
        backend.trigger_capture()
