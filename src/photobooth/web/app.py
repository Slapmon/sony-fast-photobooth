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

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError, CameraError
from photobooth.config.models import Settings
from photobooth.delivery.backend import build_delivery_backend
from photobooth.delivery.worker import DeliveryWorker
from photobooth.preview.proxy import PreviewProxy
from photobooth.printing.backend import build_printer_backend
from photobooth.printing.queue import PrintQueue, make_print_handler
from photobooth.storage import db as storage_db
from photobooth.storage.queue import JobQueue, run_worker
from photobooth.storage.retention import run_retention_sweep
from photobooth.telemetry.logging_config import configure_logging
from photobooth.web.routers import admin, admin_auth, debug, gallery, kiosk, preview, share
from photobooth.web.session import SessionManager

logger = structlog.get_logger(__name__)

CAPTURES_DIR = Path("out/captures")
# Layout YAMLs (T-2.1) — same "templates/" repo-root convention as
# events.base_dir, resolved here rather than added to Settings since it's
# fixed by repo layout, not something dev/pi profiles vary (IMPLEMENTATION_PLAN.md §8).
TEMPLATES_DIR = Path("templates")
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
    # Must run before anything else logs (T-5.2).
    configure_logging(settings.logging)
    # Read by admin_auth's require_admin dependency (T-3.7) to check PINs
    # and sign/verify session tokens.
    app.state.settings = settings

    # Blocking Popen is intentional here: this runs once at startup, before
    # the event loop is serving any requests, and _wait_for_worker right
    # after it is a blocking poll too — there's nothing concurrent for an
    # async subprocess API to help with at this point.
    #
    # Everything from here on is nested in try/finally, each level closing
    # exactly the resource it opened — not just the single try/finally
    # around `yield` that used to be here. A startup failure anywhere below
    # (a bad printer/delivery config, a broken event file, etc.) used to
    # leak the worker subprocess: it was spawned, but the only cleanup path
    # was the finally block wrapping `yield`, which a pre-yield exception
    # skips entirely. That's exactly what happened deploying to the Pi — a
    # misconfigured CupsBackend raised before `yield`, and the orphaned
    # worker process sat holding camera.worker_port until killed by hand.
    worker_process = subprocess.Popen(_worker_args(settings))  # noqa: ASYNC220
    try:
        _wait_for_worker("127.0.0.1", settings.camera.worker_port, _WORKER_READY_TIMEOUT_S)

        camera_client = CameraWorkerClient("127.0.0.1", settings.camera.worker_port)
        try:
            await camera_client.connect()
        except (CameraError, CameraDisconnectedError) as exc:
            logger.warning("camera_connect_failed", error=str(exc))

        conn = storage_db.connect(settings.storage.sqlite_path)
        try:
            # Delivery (T-4.2/T-4.4): uploads are enqueued, never called
            # inline, so a target being unreachable degrades to
            # retry-with-backoff rather than a lost photo. See
            # delivery/worker.py's module docstring for the full contract.
            job_queue = JobQueue(conn)
            delivery_backend = build_delivery_backend(settings.delivery)
            delivery_worker = DeliveryWorker(job_queue, delivery_backend)
            delivery_worker.start()
            if settings.delivery.backend == "local":
                settings.delivery.local.output_dir.mkdir(parents=True, exist_ok=True)

            # Printing (T-4.6/T-4.7): backend=None means printing is
            # disabled for this profile — no worker task is started, and
            # admin/kiosk routes must treat a None printer_backend as "not
            # configured" rather than erroring.
            printer_backend = build_printer_backend(settings.printing)
            print_queue = PrintQueue(job_queue, settings.printing.cups.print_limit_per_session)
            print_stop_event = asyncio.Event()
            print_task: asyncio.Task[None] | None = None
            if printer_backend is not None:
                print_task = asyncio.create_task(
                    run_worker(
                        job_queue,
                        kind="print",
                        handler=make_print_handler(printer_backend),
                        stop_event=print_stop_event,
                    )
                )

            # Retention (T-4.5): always started — a no-op loop when
            # settings.retention.enabled is False, per run_retention_sweep's
            # own contract, so there's no separate enabled-branch needed.
            retention_stop_event = asyncio.Event()
            retention_task = asyncio.create_task(
                run_retention_sweep(
                    conn, CAPTURES_DIR, settings.retention, stop_event=retention_stop_event
                )
            )

            session_manager = SessionManager(
                camera=camera_client,
                db=conn,
                captures_dir=CAPTURES_DIR,
                events_dir=settings.events.base_dir,
                templates_dir=TEMPLATES_DIR,
                active_event_id=settings.events.active_event_id,
                job_queue=job_queue,
            )
            preview_proxy = PreviewProxy(
                settings.preview.stream_url, settings.preview.connect_timeout_s
            )
            app.state.session_manager = session_manager
            app.state.preview_proxy = preview_proxy
            app.state.worker_process = worker_process
            app.state.camera_client = camera_client
            app.state.db = conn
            # Read by the kiosk router's /session/event endpoint (T-3.1) to
            # serve the active event's public info to the attract loop —
            # kept as plain app.state attributes (like everything else
            # above) rather than stashing the whole Settings object, so it
            # stays a two-line addition.
            app.state.events_dir = settings.events.base_dir
            app.state.active_event_id = settings.events.active_event_id
            app.state.kiosk_idle_timeout_s = settings.kiosk.idle_timeout_s
            # Read by admin/kiosk routers for delivery/print status,
            # submitting print jobs, and the printer-status gate on the
            # guest print button.
            app.state.job_queue = job_queue
            app.state.printer_backend = printer_backend
            app.state.print_queue = print_queue
            # Read by web/routers/share.py's QR generation — overrides the
            # host guests are sent to (see DeliveryConfig.public_base_url's
            # own docstring for when/why to set this).
            app.state.share_public_base_url = settings.delivery.public_base_url

            try:
                yield
            finally:
                print_stop_event.set()
                if print_task is not None:
                    await print_task
                retention_stop_event.set()
                await retention_task
                await delivery_worker.aclose()
                await preview_proxy.aclose()
                await camera_client.close()
        finally:
            conn.close()
    finally:
        worker_process.terminate()
        try:
            worker_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            worker_process.kill()


CAPTURES_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="photobooth", lifespan=lifespan)
app.include_router(kiosk.router)
app.include_router(preview.router)
app.include_router(debug.router)
app.include_router(gallery.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
app.include_router(share.router)
app.mount("/captures", StaticFiles(directory=CAPTURES_DIR), name="captures")
# Mirrors CAPTURES_DIR's mount above — LocalDirBackend.upload() (T-4.2)
# returns URLs of the form /uploads/{remote_key}; only meaningful when
# delivery.backend == "local", but mounting unconditionally at a fixed
# repo-relative default keeps this a static, testable path (matching
# TEMPLATES_DIR's reasoning above) rather than something profile-dependent
# resolved at import time, before Settings has even loaded.
UPLOADS_DIR = Path("out/uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Serves the built Svelte frontend (`frontend/dist`, `npm run build`) so this
# app works standalone in production — locally, the Vite dev server has
# always fronted it instead (vite.config.ts's own docstring: "the same code
# works unproxied once built and served by the backend itself" — this is
# that "once built and served" half). Registered LAST so every API router
# above still wins for its own paths; only unmatched requests reach here.
#
# App.svelte's client-side router (path-based, not a real router library)
# recognizes exactly three URL shapes: `/`, `/admin`, `/gallery/<event_id>` —
# all three must serve the same `index.html`, letting the bundled JS take
# over from there. A blanket catch-all route would risk silently shadowing
# a future API path; three explicit routes plus a trailing static mount for
# literal files (JS/CSS/favicon) is safer and no harder to maintain.
FRONTEND_DIST = Path("frontend/dist")
_FRONTEND_INDEX_MISSING = (
    "frontend/dist/index.html not found — run `npm run build` in frontend/ first"
)


def _serve_frontend_index() -> FileResponse:
    index_path = FRONTEND_DIST / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail=_FRONTEND_INDEX_MISSING)
    return FileResponse(index_path)


@app.get("/", include_in_schema=False)
async def serve_kiosk() -> FileResponse:
    return _serve_frontend_index()


@app.get("/admin", include_in_schema=False)
async def serve_admin() -> FileResponse:
    return _serve_frontend_index()


@app.get("/gallery/{event_id}", include_in_schema=False)
async def serve_gallery(event_id: str) -> FileResponse:
    return _serve_frontend_index()


if FRONTEND_DIST.is_dir():
    # Literal built assets (JS/CSS bundles under assets/, favicon.svg, etc.)
    # — anything not matched by the three routes above falls through to
    # this, which 404s for a genuinely missing file rather than silently
    # serving index.html for it.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST), name="frontend")
