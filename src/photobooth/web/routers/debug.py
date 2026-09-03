"""Debug endpoints — the tool that answers "where did the 10 seconds go"
(telemetry/spans.py's docstring, IMPLEMENTATION_PLAN.md §4.2). Reads the
spans SQLite table the capture flow already writes to; records nothing
itself.
"""

from __future__ import annotations

import asyncio
import sqlite3
import statistics
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from photobooth.camera.client import CameraWorkerClient
from photobooth.config.models import Settings
from photobooth.printing.backend import PrinterBackend
from photobooth.web import health_checks
from photobooth.web.session import SessionManager

router = APIRouter(prefix="/debug")


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


@router.get("/traces")
def get_traces(db: DbDep, limit: int = 20) -> list[dict[str, Any]]:
    capture_ids = [
        row[0]
        for row in db.execute(
            "SELECT DISTINCT capture_id FROM spans ORDER BY rowid DESC LIMIT ?", (limit,)
        )
    ]
    traces = []
    for capture_id in capture_ids:
        rows = db.execute(
            "SELECT name, t_start, t_end, meta_json FROM spans "
            "WHERE capture_id = ? ORDER BY t_start",
            (capture_id,),
        ).fetchall()
        traces.append(
            {
                "capture_id": capture_id,
                "spans": [
                    {
                        "name": name,
                        "t_start": t_start,
                        "t_end": t_end,
                        "duration_ms": None if t_end is None else (t_end - t_start) * 1000,
                        "meta": meta_json,
                    }
                    for name, t_start, t_end, meta_json in rows
                ],
            }
        )
    return traces


@router.get("/timings")
def get_timings(db: DbDep, limit_per_name: int = 200) -> dict[str, dict[str, float | int]]:
    names = [row[0] for row in db.execute("SELECT DISTINCT name FROM spans")]
    result: dict[str, dict[str, float | int]] = {}
    for name in names:
        durations_ms = [
            (t_end - t_start) * 1000
            for (t_start, t_end) in db.execute(
                "SELECT t_start, t_end FROM spans WHERE name = ? AND t_end IS NOT NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (name, limit_per_name),
            )
        ]
        if not durations_ms:
            continue
        sorted_ms = sorted(durations_ms)
        result[name] = {
            "count": len(sorted_ms),
            "p50": _percentile(sorted_ms, 0.50),
            "p95": _percentile(sorted_ms, 0.95),
            "p99": _percentile(sorted_ms, 0.99),
            "max": sorted_ms[-1],
        }
    return result


def _percentile(sorted_values: list[float], p: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    return statistics.quantiles(sorted_values, n=100, method="inclusive")[int(p * 100) - 1]


@router.get("/health")
async def get_health(request: Request) -> list[dict[str, Any]]:
    """Preflight-style green/red/gray checklist (IMPLEMENTATION_PLAN.md
    T-3.12, photobooth-plan.md §10 "Pre-event checklist"). Reuses the same
    probes as `/admin/status` (T-3.10) via `web/health_checks.py`, presented
    as a flat named list rather than a status dashboard, matching the
    "green/red per line" UX photobooth-plan.md describes.

    Items from photobooth-plan.md's checklist not buildable today are marked
    `"not_available"` (no such check exists yet) or `"not_configured"`
    (depends on hardware/backend that doesn't exist yet, Phase 4 printing) —
    see this task's report for the reasoning per item, including why
    test-shot-within-budget and time-synced aren't wired in here.
    """
    settings: Settings = request.app.state.settings
    camera_client: CameraWorkerClient = request.app.state.camera_client
    session_manager: SessionManager = request.app.state.session_manager
    printer_backend: PrinterBackend | None = request.app.state.printer_backend

    checks = (
        health_checks.check_camera(camera_client),
        health_checks.check_preview(
            settings.preview.stream_url, settings.preview.connect_timeout_s
        ),
        health_checks.check_disk(settings.storage.sqlite_path.parent),
        health_checks.check_network(),
    )
    if printer_backend is not None:
        camera_connected, preview, disk, network, printer = await asyncio.gather(
            *checks, printer_backend.status()
        )
    else:
        camera_connected, preview, disk, network = await asyncio.gather(*checks)
        printer = health_checks.NOT_CONFIGURED_PRINTER

    # "camera idle/ready": the CameraWorkerClient protocol doesn't expose a
    # busy/idle signal of its own (get_status() only reports `connected`),
    # but the guest session state machine does — CAPTURING means a download
    # is actively in flight on the one PTP handle, everything else means the
    # camera is safe to use for a preflight test shot.
    camera_ready = {
        "status": "green" if session_manager.state.value != "capturing" else "red",
        "detail": f"session state: {session_manager.state.value}",
    }

    return [
        {"name": "camera_connected", **camera_connected},
        {"name": "camera_idle", **camera_ready},
        {"name": "preview_stream", **preview},
        {"name": "disk_free", **disk},
        {"name": "network", **network},
        {
            "name": "camera_settings_profile",
            "status": "not_available",
            "detail": "no expected-profile check exists yet",
        },
        {
            "name": "test_shot_within_budget",
            "status": "not_available",
            "detail": (
                "not run automatically from this GET (would be a mutating action from a "
                "passive health check) — trigger POST /admin/actions/test-shot manually"
            ),
        },
        {
            "name": "flash_fires",
            "status": "not_available",
            "detail": "unmeasurable via software",
        },
        {"name": "printer_online_with_media", **printer},
        {
            "name": "time_synced",
            "status": "not_available",
            "detail": "skipped for v1 — no NTP-offset check implemented",
        },
    ]
