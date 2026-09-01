"""Exercises SessionManager directly against the in-thread mock camera worker
(shared `client`/`worker_port` fixtures in conftest.py), a real sqlite
connection, and a real temp captures directory — no FastAPI/HTTP layer here.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import msgspec
import pytest

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError
from photobooth.core.events import (
    CaptureFailed,
    CountdownStarted,
    Event,
    FullImageReady,
    PreviewReady,
    StateChanged,
)
from photobooth.core.state import InvalidTransitionError, SessionState
from photobooth.storage import db as storage_db
from photobooth.web.session import SessionManager

_SHORT_COUNTDOWN_S = 0.05


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)


def _decoded_events(ws: FakeWebSocket) -> list[Event]:
    decoder = msgspec.json.Decoder(type=Event)
    return [decoder.decode(payload) for payload in ws.sent]


@pytest.fixture
def session_manager(worker_port: int, tmp_path: Path) -> SessionManager:
    camera = CameraWorkerClient("127.0.0.1", worker_port)
    conn = storage_db.connect(tmp_path / "test.db")
    return SessionManager(
        camera=camera,
        db=conn,
        captures_dir=tmp_path / "captures",
        default_countdown_s=_SHORT_COUNTDOWN_S,
    )


async def test_arm_then_capture_reaches_review_with_real_images(
    session_manager: SessionManager, tmp_path: Path
) -> None:
    await session_manager.camera.connect()
    await session_manager.arm()
    await session_manager.capture()

    assert session_manager.state == SessionState.REVIEW

    captures_dir = tmp_path / "captures"
    jpegs = list(captures_dir.glob("*.jpg"))
    assert len(jpegs) >= 1
    for jpeg in jpegs:
        data = jpeg.read_bytes()
        assert len(data) > 1000
        assert data[:2] == b"\xff\xd8"  # JPEG SOI marker


async def test_capture_broadcasts_expected_event_sequence(
    session_manager: SessionManager,
) -> None:
    ws = FakeWebSocket()
    await session_manager.register(ws)
    await session_manager.camera.connect()

    await session_manager.arm()
    await session_manager.capture()

    events = _decoded_events(ws)
    types = [type(e) for e in events]

    assert types[0] is StateChanged
    assert events[0].state == SessionState.ARMED  # type: ignore[union-attr]

    assert types[1] is StateChanged
    assert events[1].state == SessionState.COUNTDOWN  # type: ignore[union-attr]

    assert types[2] is CountdownStarted

    assert types[3] is StateChanged
    assert events[3].state == SessionState.CAPTURING  # type: ignore[union-attr]

    assert PreviewReady in types
    assert FullImageReady in types
    assert types[-1] is StateChanged
    assert events[-1].state == SessionState.REVIEW  # type: ignore[union-attr]

    preview_idx = types.index(PreviewReady)
    full_idx = types.index(FullImageReady)
    assert preview_idx < full_idx


async def test_arm_then_capture_persists_session_and_capture_rows(
    session_manager: SessionManager,
) -> None:
    await session_manager.camera.connect()
    await session_manager.arm()
    session_id = session_manager.session_id

    await session_manager.capture()

    db: sqlite3.Connection = session_manager._db  # noqa: SLF001
    session_row = db.execute(
        "SELECT id, state FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert session_row is not None
    assert session_row[0] == session_id
    assert session_row[1] == SessionState.REVIEW.value

    capture_rows = db.execute(
        "SELECT id, session_id FROM captures WHERE session_id = ?", (session_id,)
    ).fetchall()
    assert len(capture_rows) == 1
    assert capture_rows[0][1] == session_id


async def test_dismiss_from_review_returns_to_idle(session_manager: SessionManager) -> None:
    await session_manager.camera.connect()
    await session_manager.arm()
    await session_manager.capture()
    assert session_manager.state == SessionState.REVIEW

    await session_manager.dismiss()
    assert session_manager.state == SessionState.IDLE


async def test_capture_without_arm_raises_invalid_transition(
    session_manager: SessionManager,
) -> None:
    with pytest.raises(InvalidTransitionError):
        await session_manager.capture()


async def test_capture_failure_broadcasts_and_returns_to_idle(
    session_manager: SessionManager,
) -> None:
    # Camera client never connects -> trigger_capture surfaces
    # CameraDisconnectedError end to end (MockBackend._require_connected).
    ws = FakeWebSocket()
    await session_manager.register(ws)

    await session_manager.arm()
    with pytest.raises(CameraDisconnectedError):
        await session_manager.capture()

    assert session_manager.state == SessionState.IDLE

    events = _decoded_events(ws)
    assert any(isinstance(e, CaptureFailed) for e in events)
    assert isinstance(events[-1], StateChanged)
    assert events[-1].state == SessionState.IDLE
