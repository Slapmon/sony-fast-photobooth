"""Fault injection on MockBackend (IMPLEMENTATION_PLAN.md §4.4) — all off
by default (covered by the existing contract suite); this file covers the
injected-fault paths specifically.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from photobooth.camera.mock import MockBackend
from photobooth.camera.protocol import CameraDisconnectedError, CameraError

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "shots"


def test_disconnect_every_n_drops_session_on_the_nth_shot() -> None:
    backend = MockBackend(
        fixtures_dir=FIXTURES_DIR,
        trigger_delay_ms=0,
        thumb_latency_ms=0,
        disconnect_every_n=3,
    )
    backend.connect()

    backend.trigger_capture()
    backend.trigger_capture()
    with pytest.raises(CameraDisconnectedError):
        backend.trigger_capture()
    assert not backend.is_connected()


def test_disconnect_every_n_recovers_after_reconnect() -> None:
    backend = MockBackend(
        fixtures_dir=FIXTURES_DIR,
        trigger_delay_ms=0,
        thumb_latency_ms=0,
        disconnect_every_n=2,
    )
    backend.connect()
    backend.trigger_capture()
    with pytest.raises(CameraDisconnectedError):
        backend.trigger_capture()

    backend.reconnect()
    capture_id = backend.trigger_capture()
    assert capture_id


def test_download_timeout_pct_100_always_raises() -> None:
    backend = MockBackend(
        fixtures_dir=FIXTURES_DIR,
        trigger_delay_ms=0,
        thumb_latency_ms=0,
        download_timeout_pct=100.0,
    )
    backend.connect()
    capture_id = backend.trigger_capture()
    with pytest.raises(CameraError):
        backend.download_full(capture_id)


def test_download_timeout_pct_0_never_raises() -> None:
    backend = MockBackend(
        fixtures_dir=FIXTURES_DIR,
        trigger_delay_ms=0,
        thumb_latency_ms=0,
        download_timeout_pct=0.0,
        rng=random.Random(0),
    )
    backend.connect()
    capture_id = backend.trigger_capture()
    image = backend.download_full(capture_id)
    assert image.data


def test_slow_download_pct_100_multiplies_delay() -> None:
    backend = MockBackend(
        fixtures_dir=FIXTURES_DIR,
        trigger_delay_ms=0,
        thumb_latency_ms=50,
        slow_download_pct=100.0,
    )
    backend.connect()
    capture_id = backend.trigger_capture()

    import time

    t0 = time.monotonic()
    backend.download_preview(capture_id)
    elapsed = time.monotonic() - t0
    # 5x the 50ms base thumb latency, with generous slack for CI jitter.
    assert elapsed >= 0.2
