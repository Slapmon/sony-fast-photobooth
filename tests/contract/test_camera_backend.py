"""Runs the same assertions against every CameraBackend implementation.

This is what makes the mock trustworthy — if it drifts from real hardware
behaviour, this suite is meant to catch it. The `gphoto` param is marked
`hardware` (pyproject.toml's `addopts = "-m 'not hardware'"` skips it by
default on a machine with no camera attached); run it explicitly with
`pytest -m hardware` on the Pi with the a6400 connected and in PC Remote
mode. GphotoBackend's own import is deferred inside the fixture so this
module still collects fine on a machine without python-gphoto2 installed.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from photobooth.camera.mock import MockBackend
from photobooth.camera.protocol import CameraBackend, CameraDisconnectedError


@pytest.fixture(params=["mock", pytest.param("gphoto", marks=pytest.mark.hardware)])
def backend(request: pytest.FixtureRequest, fixtures_dir: Path) -> Iterator[CameraBackend]:
    b: CameraBackend
    if request.param == "mock":
        b = MockBackend(fixtures_dir=fixtures_dir, trigger_delay_ms=0, thumb_latency_ms=0)
    else:
        from photobooth.camera.gphoto import GphotoBackend

        b = GphotoBackend(jpeg_size="S")
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
