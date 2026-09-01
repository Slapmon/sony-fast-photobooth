"""Kiosk-facing routes: arm/capture/dismiss the single active session, and
the WebSocket push channel that mirrors state to the frontend. See
IMPLEMENTATION_PLAN.md §7 (Phase 1) — no polling endpoints by design, the
WebSocket is the only way clients observe session progress.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect

from photobooth.camera.protocol import CameraDisconnectedError, CameraError
from photobooth.core.state import InvalidTransitionError
from photobooth.web.session import SessionManager

router = APIRouter()


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager


def get_session_manager_ws(websocket: WebSocket) -> SessionManager:
    # A WebSocket route has no Request in scope, so it needs its own
    # dependency typed on WebSocket rather than reusing get_session_manager.
    return websocket.app.state.session_manager


SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]
SessionManagerWsDep = Annotated[SessionManager, Depends(get_session_manager_ws)]


@router.post("/session/arm")
async def arm_session(session_manager: SessionManagerDep) -> dict[str, str]:
    try:
        await session_manager.arm()
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
