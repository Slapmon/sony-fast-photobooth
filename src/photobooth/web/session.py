"""SessionManager — glues the camera-worker client, the session state
machine, and the WebSocket event bus into the single live capture flow
described in IMPLEMENTATION_PLAN.md §7 (Phase 1). One instance per app
process; single active session at a time (no multi-booth concurrency yet).
"""

from __future__ import annotations

import asyncio
import secrets
import sqlite3
import uuid
from pathlib import Path

from fastapi import WebSocket

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraError
from photobooth.config.event import load_event
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
from photobooth.pipeline.template import load_template
from photobooth.storage.queue import JobQueue
from photobooth.storage.repos import CaptureRepo, SessionRepo
from photobooth.telemetry import spans

# 18 random bytes -> 24 url-safe chars (secrets.token_urlsafe), matching the
# length share.py's own docstring specifies for issuers of this token (T-4.3).
_SHARE_TOKEN_BYTES = 18


class SessionManager:
    def __init__(
        self,
        camera: CameraWorkerClient,
        db: sqlite3.Connection,
        captures_dir: Path,
        default_countdown_s: float = 3.0,
        events_dir: Path | None = None,
        templates_dir: Path | None = None,
        active_event_id: str = "dev",
        job_queue: JobQueue | None = None,
    ) -> None:
        self._camera = camera
        self._db = db
        self._captures_dir = captures_dir
        self._captures_dir.mkdir(parents=True, exist_ok=True)
        self._default_countdown_s = default_countdown_s
        # Left unset, a session issues no share token and enqueues no
        # upload jobs (T-4.3/T-4.4) — matching the same "unset = pre-Phase-4
        # behaviour" convention events_dir/templates_dir already established
        # for T-2.6, so existing callers/tests are unaffected.
        self._job_queue = job_queue
        # events_dir/templates_dir resolve the active template so shot_count
        # can be derived from len(template.slots) (T-2.6). Left unset, a
        # session is always shot_count=1 — this is exactly the pre-T-2.6
        # single-shot behaviour, and it's the default so existing callers
        # (and the regression test) don't need to change.
        self._events_dir = events_dir
        self._templates_dir = templates_dir
        self._active_event_id = active_event_id
        self._shot_count = 1
        self._machine = SessionStateMachine()
        self._session_id = uuid.uuid4().hex
        self._websockets: set[WebSocket] = set()
        self._sessions = SessionRepo(db)
        self._captures = CaptureRepo(db)
        # Camera-busy gate (IMPLEMENTATION_PLAN.md §5 "Critical guard"): set
        # whenever no download_full is in flight, cleared for the duration
        # of one. The trigger for the next shot awaits this before firing,
        # so an overrunning download holds the next countdown at "1" rather
        # than letting a trigger queue up behind it on the one PTP handle.
        self._camera_idle = asyncio.Event()
        self._camera_idle.set()
        # Full-resolution capture_ids for the just-completed session, in
        # shot order. Seam for a future wave's compositor to consume — this
        # task does not render the collage itself.
        self._session_capture_ids: list[str] = []

    @property
    def state(self) -> SessionState:
        return self._machine.state

    @property
    def camera(self) -> CameraWorkerClient:
        return self._camera

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def shot_count(self) -> int:
        return self._shot_count

    @property
    def capture_ids(self) -> list[str]:
        """Full-resolution capture_ids from the most recently completed
        session, in shot order. TODO(compositor wave): a future consumer
        renders these slots into the template's collage composite — that
        wiring is intentionally not done here (T-2.6 is capture-flow only).
        """
        return list(self._session_capture_ids)

    async def register(self, websocket: WebSocket) -> None:
        self._websockets.add(websocket)

    async def unregister(self, websocket: WebSocket) -> None:
        self._websockets.discard(websocket)

    async def broadcast(self, event: Event) -> None:
        # Sent as a text frame, not send_bytes: encode_event's output is
        # JSON bytes, but a binary WS frame arrives in the browser as a
        # Blob, and JSON.parse(Blob) throws rather than parsing it.
        payload = encode_event(event).decode()
        dead: list[WebSocket] = []
        for ws in self._websockets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._websockets.discard(ws)

    async def _transition(self, target: SessionState, share_token: str | None = None) -> None:
        self._machine.transition(target)
        self._sessions.update_state(self._session_id, target.value)
        await self.broadcast(
            StateChanged(session_id=self._session_id, state=target, share_token=share_token)
        )

    async def arm(self, mode_id: str | None = None) -> None:
        """`mode_id` selects one of the active event's `EventConfig.modes`
        (guest-facing buttons like "Single Photo"/"Collage" — see
        `web/routers/kiosk.py`'s `POST /session/arm`). Unset, or an event
        with no `modes` configured, falls back to `EventConfig.template` —
        the pre-mode-selection single-template behaviour, so old
        events/tests without `modes` are unaffected. An unknown `mode_id`
        also falls back to the event's first configured mode rather than
        erroring, since a stale client request naming a mode that no longer
        exists shouldn't strand a guest.
        """
        self._machine.transition(SessionState.ARMED)
        self._session_id = uuid.uuid4().hex
        self._session_capture_ids = []

        event_id = self._active_event_id
        shot_count = 1
        if self._events_dir is not None and self._templates_dir is not None:
            event = load_event(self._events_dir, self._active_event_id)
            template_name = event.template
            if event.modes:
                chosen = next((m for m in event.modes if m.id == mode_id), event.modes[0])
                template_name = chosen.template
            template = load_template(self._templates_dir / template_name)
            shot_count = len(template.slots)
            event_id = event.id
        self._shot_count = shot_count

        self._sessions.create(self._session_id, event_id=event_id, state=SessionState.ARMED.value)
        await self.broadcast(StateChanged(session_id=self._session_id, state=SessionState.ARMED))

    async def capture(self, countdown_s: float | None = None) -> None:
        """Drive the session through shot_count shots back to back (T-2.6).

        shot_count == 1 (the default, and every pre-T-2.6 caller) behaves
        exactly like the old single-shot flow. For shot_count > 1 the loop
        goes COUNTDOWN -> CAPTURING -> COUNTDOWN -> ... -> CAPTURING once per
        slot in the active template, landing in REVIEW only after the last
        shot's full-resolution download completes.
        """
        duration = self._default_countdown_s if countdown_s is None else countdown_s
        shot_count = self._shot_count
        capture_ids: list[str] = []

        for shot_index in range(shot_count):
            # ARMED -> COUNTDOWN for shot 0, CAPTURING -> COUNTDOWN for
            # every shot after that (the edge core/state.py added for T-2.6).
            await self._transition(SessionState.COUNTDOWN)
            await self.broadcast(
                CountdownStarted(
                    session_id=self._session_id,
                    duration_s=duration,
                    shot_index=shot_index,
                    shot_count=shot_count,
                )
            )
            await asyncio.sleep(duration)
            # Critical guard (IMPLEMENTATION_PLAN.md §5): never let the next
            # trigger queue up behind a still-in-flight download on the one
            # PTP handle. If shot_index - 1's download_full overruns the
            # countdown, this wait is what actually holds the countdown at
            # "1" rather than firing the next shutter early.
            await self._camera_idle.wait()
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
                            shot_index=shot_index,
                            shot_count=shot_count,
                        )
                    )

                self._camera_idle.clear()
                try:
                    with spans.span(self._db, "ptp.download_full", capture_id=capture_id):
                        full = await self._camera.download_full(capture_id)
                finally:
                    self._camera_idle.set()
                full_path = self._captures_dir / f"{capture_id}.jpg"
                full_path.write_bytes(full.data)
                capture_ids.append(capture_id)
                await self.broadcast(
                    FullImageReady(
                        session_id=self._session_id,
                        capture_id=capture_id,
                        image_url=f"/captures/{capture_id}.jpg",
                    )
                )
            except CameraError as exc:
                self._camera_idle.set()
                await self.broadcast(CaptureFailed(session_id=self._session_id, message=str(exc)))
                await self._transition(SessionState.IDLE)
                raise

        self._session_capture_ids = capture_ids
        share_token = self._issue_share_token_and_enqueue_uploads(capture_ids)
        await self._transition(SessionState.REVIEW, share_token=share_token)

    def _issue_share_token_and_enqueue_uploads(self, capture_ids: list[str]) -> str | None:
        """Runs once, right after the last shot lands and before REVIEW
        (T-4.3/T-4.4). No-ops entirely if this SessionManager was built
        without a job_queue (dev/test callers that don't pass one), returning
        None so the REVIEW StateChanged broadcast carries no share_token
        either — matching the pre-Phase-4 behaviour those callers expect.
        """
        if self._job_queue is None:
            return None
        token = secrets.token_urlsafe(_SHARE_TOKEN_BYTES)
        self._sessions.set_share_token(self._session_id, token)
        for capture_id in capture_ids:
            full_path = self._captures_dir / f"{capture_id}.jpg"
            self._job_queue.enqueue(
                "upload",
                {
                    "local_path": str(full_path),
                    "remote_key": f"{capture_id}.jpg",
                    "capture_id": capture_id,
                },
            )
        return token

    async def dismiss(self) -> None:
        await self._transition(SessionState.IDLE)

    def record_browser_decode(self, capture_id: str, duration_ms: float) -> None:
        """The frontend reports how long its own <img> decode took over the
        WebSocket (IMPLEMENTATION_PLAN.md T-1.12) — this closes the loop on
        the mandatory display.browser_decode span (§4.1), the one stage of
        the latency budget the server can't measure itself.
        """
        spans.record_duration(self._db, "display.browser_decode", capture_id, duration_ms / 1000)
