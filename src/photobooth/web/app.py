"""FastAPI app entrypoint. Routers per surface (kiosk / gallery / admin /
debug) are added incrementally as their phases land — see
IMPLEMENTATION_PLAN.md §7-9. /health exists from commit one so `just dev`
and CI have something to point at.

Startup spawns the dedicated camera-worker subprocess (photobooth-plan.md
§3.2 — libgphoto2/blocking calls must never share a thread with the async
app) and waits for it to become reachable before wiring up the
CameraWorkerClient the rest of the app uses.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError, CameraError
from photobooth.config.models import Settings
from photobooth.preview.proxy import PreviewProxy
from photobooth.storage import db as storage_db
from photobooth.web.routers import debug, kiosk, preview
from photobooth.web.session import SessionManager

logger = structlog.get_logger(__name__)

CAPTURES_DIR = Path("out/captures")
_WORKER_READY_TIMEOUT_S = 5.0
_WORKER_READY_POLL_S = 0.1


def _worker_args(settings: Settings) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "photobooth.camera.worker",
        "--backend",
        settings.camera.backend,
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.camera.worker_port),
    ]
    if settings.camera.backend == "mock":
        mock = settings.camera.mock
        args += [
            "--fixtures-dir",
            str(mock.fixtures_dir),
            "--trigger-delay-ms",
            str(mock.trigger_delay_ms),
            "--thumb-latency-ms",
            str(mock.thumb_latency_ms),
            "--full-download-mbps",
            str(mock.full_download_mbps),
            "--download-timeout-pct",
            str(mock.download_timeout_pct),
            "--slow-download-pct",
            str(mock.slow_download_pct),
        ]
        if mock.disconnect_every_n is not None:
            args += ["--disconnect-every-n", str(mock.disconnect_every_n)]
    else:
        args += ["--jpeg-size", settings.camera.gphoto.jpeg_size]
    return args


def _wait_for_worker(host: str, port: int, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(_WORKER_READY_POLL_S)
    raise TimeoutError(f"camera worker not reachable on {host}:{port}") from last_error


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config_path = Path(os.environ.get("PHOTOBOOTH_CONFIG", "config/dev.yaml"))
    settings = Settings.load(config_path)

    # Blocking Popen is intentional here: this runs once at startup, before
    # the event loop is serving any requests, and _wait_for_worker right
    # after it is a blocking poll too — there's nothing concurrent for an
    # async subprocess API to help with at this point.
    worker_process = subprocess.Popen(_worker_args(settings))  # noqa: ASYNC220
    _wait_for_worker("127.0.0.1", settings.camera.worker_port, _WORKER_READY_TIMEOUT_S)

    camera_client = CameraWorkerClient("127.0.0.1", settings.camera.worker_port)
    try:
        await camera_client.connect()
    except (CameraError, CameraDisconnectedError) as exc:
        logger.warning("camera_connect_failed", error=str(exc))

    conn = storage_db.connect(settings.storage.sqlite_path)

    session_manager = SessionManager(camera=camera_client, db=conn, captures_dir=CAPTURES_DIR)
    preview_proxy = PreviewProxy(
        settings.preview.stream_url, settings.preview.connect_timeout_s
    )
    app.state.session_manager = session_manager
    app.state.preview_proxy = preview_proxy
    app.state.worker_process = worker_process
    app.state.camera_client = camera_client
    app.state.db = conn

    try:
        yield
    finally:
        await preview_proxy.aclose()
        await camera_client.close()
        worker_process.terminate()
        try:
            worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_process.kill()
        conn.close()


CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="photobooth", lifespan=lifespan)
app.include_router(kiosk.router)
app.include_router(preview.router)
app.include_router(debug.router)
app.mount("/captures", StaticFiles(directory=CAPTURES_DIR), name="captures")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
