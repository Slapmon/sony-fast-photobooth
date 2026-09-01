"""SessionManager — glues the camera-worker client, the session state
machine, and the WebSocket event bus into the single live capture flow
described in IMPLEMENTATION_PLAN.md §7 (Phase 1). One instance per app
process; single active session at a time (no multi-booth concurrency yet).
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from pathlib import Path

from fastapi import WebSocket

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraError
from photobooth.core.events import (
    CaptureFailed,
    CountdownStarted,
    Event,
    FullImageReady,
    PreviewReady,
    StateChanged,
    encode_event,
)
from photobooth.core.state import SessionState, SessionStateMachine
from photobooth.storage.repos import CaptureRepo, SessionRepo
from photobooth.telemetry import spans


class SessionManager:
    def __init__(
        self,
        camera: CameraWorkerClient,
        db: sqlite3.Connection,
        captures_dir: Path,
        default_countdown_s: float = 3.0,
    ) -> None:
        self._camera = camera
        self._db = db
        self._captures_dir = captures_dir
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        self._default_countdown_s = default_countdown_s
        self._machine = SessionStateMachine()
        self._session_id = uuid.uuid4().hex
        self._websockets: set[WebSocket] = set()
        self._sessions = SessionRepo(db)
        self._captures = CaptureRepo(db)

    @property
    def state(self) -> SessionState:
        return self._machine.state

    @property
    def camera(self) -> CameraWorkerClient:
        return self._camera

    @property
    def session_id(self) -> str:
        return self._session_id

    async def register(self, websocket: WebSocket) -> None:
        self._websockets.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        self._websockets.discard(websocket)

    async def broadcast(self, event: Event) -> None:
        payload = encode_event(event)
        dead: list[WebSocket] = []
        for ws in self._websockets:
            try:
                await ws.send_bytes(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._websockets.discard(ws)

    async def _transition(self, target: SessionState) -> None:
        self._machine.transition(target)
        self._sessions.update_state(self._session_id, target.value)
        await self.broadcast(StateChanged(session_id=self._session_id, state=target))

    async def arm(self) -> None:
        self._machine.transition(SessionState.ARMED)
        self._session_id = uuid.uuid4().hex
        # No event-config system yet (Phase 5) — "dev" is a deliberate
        # placeholder for event_id until one exists.
        self._sessions.create(self._session_id, event_id="dev", state=SessionState.ARMED.value)
        await self.broadcast(
            StateChanged(session_id=self._session_id, state=SessionState.ARMED)
        )

    async def capture(self, countdown_s: float | None = None) -> None:
        duration = self._default_countdown_s if countdown_s is None else countdown_s
        await self._transition(SessionState.COUNTDOWN)
        await self.broadcast(
            CountdownStarted(session_id=self._session_id, duration_s=duration)
        )
        await asyncio.sleep(duration)
        await self._transition(SessionState.CAPTURING)

        try:
            with spans.span(self._db, "capture.trigger", capture_id=self._session_id):
                capture_id = await self._camera.trigger_capture()
            self._captures.create(capture_id, self._session_id)

            with spans.span(self._db, "ptp.download_thumb", capture_id=capture_id):
                preview = await self._camera.download_preview(capture_id)
            if preview is not None:
                preview_path = self._captures_dir / f"{capture_id}-preview.jpg"
                preview_path.write_bytes(preview.data)
                await self.broadcast(
                    PreviewReady(
                        session_id=self._session_id,
                        capture_id=capture_id,
                        image_url=f"/captures/{capture_id}-preview.jpg",
                    )
                )

            with spans.span(self._db, "ptp.download_full", capture_id=capture_id):
                full = await self._camera.download_full(capture_id)
            full_path = self._captures_dir / f"{capture_id}.jpg"
            full_path.write_bytes(full.data)
            await self.broadcast(
                FullImageReady(
                    session_id=self._session_id,
                    capture_id=capture_id,
                    image_url=f"/captures/{capture_id}.jpg",
                )
            )
        except CameraError as exc:
            await self.broadcast(
                CaptureFailed(session_id=self._session_id, message=str(exc))
            )
            await self._transition(SessionState.IDLE)
            raise

        await self._transition(SessionState.REVIEW)

    async def dismiss(self) -> None:
        await self._transition(SessionState.IDLE)

    def record_browser_decode(self, capture_id: str, duration_ms: float) -> None:
        """The frontend reports how long its own <img> decode took over the
        WebSocket (IMPLEMENTATION_PLAN.md T-1.12) — this closes the loop on
        the mandatory display.browser_decode span (§4.1), the one stage of
        the latency budget the server can't measure itself.
        """
        spans.record_duration(self._db, "display.browser_decode", capture_id, duration_ms / 1000)
