"""Shared reachability/status probes used by both `/admin/status` (T-3.10,
human-facing dashboard rows) and `/debug/health` (T-3.12, preflight-style
green/red list). Same underlying checks, two different presentations — kept
here rather than duplicated in `web/routers/admin.py` and `web/routers/debug.py`.

Each check returns `{"status": "green" | "red" | "gray", "detail": str, ...}`.
`"gray"` means "not yet meaningfully checkable" (e.g. a filesystem path that
doesn't exist), distinct from `"red"` which means "checked and it's bad."
"""

from __future__ import annotations

import asyncio
import shutil
import socket
from pathlib import Path
from typing import Any

import httpx

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError, CameraError

# Below this much free space, disk_free reports red (IMPLEMENTATION_PLAN.md
# T-3.12: "warn under 1GB" — chosen as a reasonable floor for a booth that's
# writing full-res JPEGs all evening, not derived from a measured budget).
DISK_FREE_WARN_BYTES = 1 * 1024**3

# Cheap reachability probe target for the "network" check (T-3.10: "keep this
# simple, don't build a real network diagnostics tool"). Cloudflare's
# well-known resolver — no DNS lookup needed, just a TCP connect.
_NETWORK_PROBE_HOST = "1.1.1.1"
_NETWORK_PROBE_PORT = 443


async def check_camera(camera_client: CameraWorkerClient) -> dict[str, Any]:
    try:
        status = await camera_client.get_status()
    except (CameraError, CameraDisconnectedError) as exc:
        return {"status": "red", "detail": str(exc)}
    connected = bool(status.get("connected"))
    return {
        "status": "green" if connected else "red",
        "detail": "connected" if connected else "disconnected",
    }


async def check_preview(stream_url: str, timeout_s: float = 2.0) -> dict[str, Any]:
    """Open the go2rtc MJPEG stream just long enough to see a 200 and a
    header, then close — this never reads frame bytes, it only proves the
    upstream is reachable and responding (photobooth-plan.md §4 preview path).
    """
    try:
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client,
            client.stream("GET", stream_url) as response,
        ):
            if response.status_code == 200:
                return {"status": "green", "detail": "reachable"}
            return {"status": "red", "detail": f"unexpected status {response.status_code}"}
    except httpx.HTTPError as exc:
        return {"status": "red", "detail": str(exc)}


def _check_disk_sync(path: Path) -> dict[str, Any]:
    probe = path if path.exists() else path.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as exc:
        return {"status": "gray", "detail": str(exc)}
    free_gb = usage.free / 1024**3
    status = "green" if usage.free >= DISK_FREE_WARN_BYTES else "red"
    return {"status": status, "detail": f"{free_gb:.2f} GB free", "free_bytes": usage.free}


async def check_disk(path: Path) -> dict[str, Any]:
    # shutil.disk_usage is a blocking syscall; keep it off the event loop.
    return await asyncio.to_thread(_check_disk_sync, path)


def _check_network_sync(host: str, port: int, timeout_s: float) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return {"status": "green", "detail": f"reached {host}:{port}"}
    except OSError as exc:
        return {"status": "red", "detail": str(exc)}


async def check_network(
    host: str = _NETWORK_PROBE_HOST, port: int = _NETWORK_PROBE_PORT, timeout_s: float = 1.5
) -> dict[str, Any]:
    return await asyncio.to_thread(_check_network_sync, host, port, timeout_s)


NOT_CONFIGURED_PRINTER: dict[str, Any] = {
    "status": "not_configured",
    "detail": "no printer backend exists yet (Phase 4)",
}
