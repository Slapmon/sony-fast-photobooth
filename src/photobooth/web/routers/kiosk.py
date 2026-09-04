"""Kiosk-facing routes: arm/capture/dismiss the single active session, and
the WebSocket push channel that mirrors state to the frontend. See
IMPLEMENTATION_PLAN.md §7 (Phase 1) — no polling endpoints by design, the
WebSocket is the only way clients observe session progress.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from photobooth.camera.protocol import CameraDisconnectedError, CameraError
from photobooth.config.event import EventConfig, load_event, resolved_strings
from photobooth.core.state import InvalidTransitionError
from photobooth.printing.backend import PrinterBackend
from photobooth.printing.queue import PrintLimitExceededError, PrintQueue
from photobooth.web.session import SessionManager

router = APIRouter()

# Mirrors web/routers/admin.py's own CAPTURES_DIR constant (repo-root
# `out/captures/` convention, fixed by layout — see that module's docstring
# for why it's duplicated rather than read off `app.state`). Needed here so
# POST /session/print can find the local file a print job submits.
CAPTURES_DIR = Path("out/captures")
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


def get_session_manager_ws(websocket: WebSocket) -> SessionManager:
    # A WebSocket route has no Request in scope, so it needs its own
    # dependency typed on WebSocket rather than reusing get_session_manager.
    return websocket.app.state.session_manager


def get_events_dir(request: Request) -> Path:
    return request.app.state.events_dir


def get_active_event_id(request: Request) -> str:
    return request.app.state.active_event_id


def get_idle_timeout_s(request: Request) -> float:
    return request.app.state.kiosk_idle_timeout_s


def get_printer_backend(request: Request) -> PrinterBackend | None:
    return request.app.state.printer_backend  # type: ignore[no-any-return]


def get_print_queue(request: Request) -> PrintQueue:
    return request.app.state.print_queue  # type: ignore[no-any-return]


SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
SessionManagerWsDep = Annotated[SessionManager, Depends(get_session_manager_ws)]
EventsDirDep = Annotated[Path, Depends(get_events_dir)]
ActiveEventIdDep = Annotated[str, Depends(get_active_event_id)]
IdleTimeoutDep = Annotated[float, Depends(get_idle_timeout_s)]
PrinterBackendDep = Annotated[PrinterBackend | None, Depends(get_printer_backend)]
PrintQueueDep = Annotated[PrintQueue, Depends(get_print_queue)]


class PrintRequest(BaseModel):
    capture_id: str


def _load_active_event(events_dir: Path, active_event_id: str) -> EventConfig:
    if not active_event_id:
        raise HTTPException(status_code=404, detail="no active event configured")
    try:
        return load_event(events_dir, active_event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="active event not found") from exc


@router.get("/session/event")
async def get_active_event(
    events_dir: EventsDirDep,
    active_event_id: ActiveEventIdDep,
    idle_timeout_s: IdleTimeoutDep,
) -> dict[str, Any]:
    """Public info for the attract loop (T-3.1): title/date/background
    image, the event id (so the frontend can link to its gallery without
    hardcoding one), the guest-facing capture-mode buttons, and the
    idle-timeout knob (T-3.3).

    `modes` mirrors `EventConfig.modes` (id/label only — the backing
    template name is a server-side detail the guest never needs). An event
    with no `modes` configured (the pre-mode-selection single-template
    shape) gets one synthesized default mode so old events keep working
    with a single button rather than none.
    """
    event = _load_active_event(events_dir, active_event_id)
    background_image_url = "/session/event/background" if event.background_image else None
    logo_image_url = "/session/event/logo" if event.logo_image else None
    modes = (
        [{"id": mode.id, "label": mode.label} for mode in event.modes]
        if event.modes
        else [{"id": "default", "label": "Take Photo"}]
    )
    return {
        "event_id": event.id,
        "title": event.title,
        "date": event.date,
        "background_image_url": background_image_url,
        "logo_image_url": logo_image_url,
        "theme": {"primary_color": event.theme.primary_color},
        "modes": modes,
        "strings": resolved_strings(event),
        "idle_timeout_s": idle_timeout_s,
    }


def _serve_active_event_image(
    events_dir: Path, event: EventConfig, filename: str, missing_detail: str
) -> FileResponse:
    if not filename:
        raise HTTPException(status_code=404, detail=missing_detail)
    path = events_dir / event.id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{missing_detail} file not found")
    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=media_type)


@router.get("/session/event/background")
async def get_event_background(
    events_dir: EventsDirDep, active_event_id: ActiveEventIdDep
) -> FileResponse:
    event = _load_active_event(events_dir, active_event_id)
    return _serve_active_event_image(
        events_dir, event, event.background_image, "event has no background image"
    )


@router.get("/session/event/logo")
async def get_event_logo(
    events_dir: EventsDirDep, active_event_id: ActiveEventIdDep
) -> FileResponse:
    event = _load_active_event(events_dir, active_event_id)
    return _serve_active_event_image(events_dir, event, event.logo_image, "event has no logo image")


@router.post("/session/arm")
async def arm_session(
    session_manager: SessionManagerDep, mode_id: str | None = None
) -> dict[str, str]:
    """`mode_id` (query param, e.g. `?mode_id=single`) picks which of the
    active event's guest-facing buttons (`GET /session/event`'s `modes`)
    this session uses — see `SessionManager.arm()`'s docstring for the
    fallback behaviour when it's omitted or unrecognized.
    """
    try:
        await session_manager.arm(mode_id)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"session_id": session_manager.session_id, "state": session_manager.state.value}


@router.post("/session/capture")
async def capture_session(session_manager: SessionManagerDep) -> dict[str, str]:
    try:
        await session_manager.capture()
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CameraDisconnectedError, CameraError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"state": "review"}


@router.post("/session/dismiss")
async def dismiss_session(session_manager: SessionManagerDep) -> dict[str, str]:
    try:
        await session_manager.dismiss()
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"state": "idle"}


@router.get("/session/printer-status")
async def get_printer_status(printer_backend: PrinterBackendDep) -> dict[str, Any]:
    """Gates the guest print button (T-4.8) — same `{"status", "detail",
    ...}` shape `web/health_checks.py`'s checks already use. A `None`
    backend (printing disabled for this profile) reports "gray" rather than
    a distinct literal, since the frontend only branches on
    `status === "green"` to show the button at all.
    """
    if printer_backend is None:
        return {"status": "gray", "detail": "printing not configured"}
    return await printer_backend.status()


@router.post("/session/print")
async def print_capture(
    body: PrintRequest,
    session_manager: SessionManagerDep,
    printer_backend: PrinterBackendDep,
    print_queue: PrintQueueDep,
) -> dict[str, int]:
    """Guest-facing print submission (T-4.8), gated by the per-session limit
    `PrintQueue.submit()` enforces. Rejected up front (409) if no printer
    backend is configured at all — a guest should never be able to enqueue a
    print job that can never be processed (no worker task is even running
    for it, see web/app.py's lifespan).
    """
    if printer_backend is None:
        raise HTTPException(status_code=409, detail="printing not configured")

    image_path = CAPTURES_DIR / f"{body.capture_id}.jpg"
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="capture not found")

    try:
        print_queue.submit(image_path, session_manager.session_id)
    except PrintLimitExceededError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"remaining": print_queue.remaining_for_session(session_manager.session_id)}


def _handle_client_message(session_manager: SessionManager, raw: str) -> None:
    # Malformed input from a client is not worth dropping the connection
    # over — a browser decode-timing report is a nice-to-have measurement,
    # not something the session flow depends on.
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(msg, dict) or msg.get("type") != "browser_decode":
        return
    capture_id = msg.get("capture_id")
    duration_ms = msg.get("duration_ms")
    if isinstance(capture_id, str) and isinstance(duration_ms, int | float):
        session_manager.record_browser_decode(capture_id, float(duration_ms))


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, session_manager: SessionManagerWsDep) -> None:
    await websocket.accept()
    await session_manager.register(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            _handle_client_message(session_manager, raw)
    except WebSocketDisconnect:
        await session_manager.unregister(websocket)
    except Exception:
        await session_manager.unregister(websocket)
        raise
