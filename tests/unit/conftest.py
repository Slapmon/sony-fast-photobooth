from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.mock import MockBackend
from photobooth.camera.worker import run_worker


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def worker_port(fixtures_dir: Path) -> Iterator[int]:
    backend = MockBackend(fixtures_dir=fixtures_dir, trigger_delay_ms=0, thumb_latency_ms=0)
    port = _free_port()
    ready = threading.Event()
    stop = threading.Event()

    thread = threading.Thread(
        target=run_worker,
        args=(backend, "127.0.0.1", port),
        kwargs={"ready_event": ready, "stop_event": stop},
        daemon=True,
    )
    thread.start()
    assert ready.wait(timeout=5.0), "worker did not become ready in time"

    yield port

    stop.set()
    thread.join(timeout=5.0)


@pytest.fixture
def client(worker_port: int) -> CameraWorkerClient:
    return CameraWorkerClient("127.0.0.1", worker_port)
