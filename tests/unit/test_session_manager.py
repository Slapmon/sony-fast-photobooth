"""Exercises SessionManager directly against the in-thread mock camera worker
(shared `client`/`worker_port` fixtures in conftest.py), a real sqlite
connection, and a real temp captures directory — no FastAPI/HTTP layer here.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
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
_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures"
_EVENTS_DIR = _FIXTURES_ROOT / "events"
_TEMPLATES_DIR = _FIXTURES_ROOT / "templates"


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
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


def _templated_session_manager(
    worker_port: int, tmp_path: Path, active_event_id: str
) -> SessionManager:
    """Same as the `session_manager` fixture, but wired to the fixture
    events/templates dirs so shot_count is derived from a real template
    (T-2.6) instead of defaulting to 1."""
    camera = CameraWorkerClient("127.0.0.1", worker_port)
    conn = storage_db.connect(tmp_path / "test.db")
    return SessionManager(
        camera=camera,
        db=conn,
        captures_dir=tmp_path / "captures",
        default_countdown_s=_SHORT_COUNTDOWN_S,
        events_dir=_EVENTS_DIR,
        templates_dir=_TEMPLATES_DIR,
        active_event_id=active_event_id,
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


# ---------------------------------------------------------------------------
# T-2.6: multi-shot collage flow + camera.idle gating
# ---------------------------------------------------------------------------


async def test_single_slot_template_matches_old_single_shot_behavior(
    worker_port: int, tmp_path: Path
) -> None:
    """shot_count derived from a 1-slot template must be indistinguishable
    from the pre-T-2.6 flow: one CountdownStarted, one PreviewReady, one
    FullImageReady, straight to REVIEW."""
    session_manager = _templated_session_manager(worker_port, tmp_path, "single-shot-event")
    ws = FakeWebSocket()
    await session_manager.register(ws)
    await session_manager.camera.connect()

    await session_manager.arm()
    assert session_manager.shot_count == 1
    await session_manager.capture()

    assert session_manager.state == SessionState.REVIEW
    assert len(session_manager.capture_ids) == 1

    events = _decoded_events(ws)
    types = [type(e) for e in events]
    assert types.count(CountdownStarted) == 1
    assert types.count(PreviewReady) == 1
    assert types.count(FullImageReady) == 1
    countdown = next(e for e in events if isinstance(e, CountdownStarted))
    assert countdown.shot_index == 0
    assert countdown.shot_count == 1

    db: sqlite3.Connection = session_manager._db  # noqa: SLF001
    capture_rows = db.execute(
        "SELECT id FROM captures WHERE session_id = ?", (session_manager.session_id,)
    ).fetchall()
    assert len(capture_rows) == 1


async def test_arm_with_mode_id_selects_that_modes_template(
    worker_port: int, tmp_path: Path
) -> None:
    """A guest's chosen mode_id (from the attract screen's buttons) picks
    the matching EventConfig.modes entry's template, not the event's legacy
    fallback `template` field."""
    session_manager = _templated_session_manager(worker_port, tmp_path, "multi-mode-event")
    await session_manager.camera.connect()

    await session_manager.arm(mode_id="collage")
    assert session_manager.shot_count == 2  # two-slot.yaml


async def test_arm_with_unknown_mode_id_falls_back_to_first_mode(
    worker_port: int, tmp_path: Path
) -> None:
    session_manager = _templated_session_manager(worker_port, tmp_path, "multi-mode-event")
    await session_manager.camera.connect()

    await session_manager.arm(mode_id="does-not-exist")
    assert session_manager.shot_count == 1  # modes[0] is "single" -> single-slot.yaml


async def test_multi_slot_template_drives_n_shots(worker_port: int, tmp_path: Path) -> None:
    """A 2-slot template runs the COUNTDOWN -> CAPTURING -> COUNTDOWN ->
    CAPTURING -> REVIEW cycle, broadcasting one PreviewReady/FullImageReady
    per shot and persisting one CaptureRepo row per shot."""
    session_manager = _templated_session_manager(worker_port, tmp_path, "two-shot-event")
    ws = FakeWebSocket()
    await session_manager.register(ws)
    await session_manager.camera.connect()

    await session_manager.arm()
    assert session_manager.shot_count == 2
    await session_manager.capture()

    assert session_manager.state == SessionState.REVIEW
    assert len(session_manager.capture_ids) == 2
    assert len(set(session_manager.capture_ids)) == 2  # distinct capture_ids

    events = _decoded_events(ws)
    types = [type(e) for e in events]
    assert types.count(CountdownStarted) == 2
    assert types.count(PreviewReady) == 2
    assert types.count(FullImageReady) == 2

    countdowns = [e for e in events if isinstance(e, CountdownStarted)]
    assert [c.shot_index for c in countdowns] == [0, 1]
    assert all(c.shot_count == 2 for c in countdowns)

    previews = [e for e in events if isinstance(e, PreviewReady)]
    assert [p.shot_index for p in previews] == [0, 1]

    # CAPTURING -> COUNTDOWN -> CAPTURING -> REVIEW, i.e. the new
    # intra-collage edge from core/state.py fires exactly once here.
    state_changes = [e.state for e in events if isinstance(e, StateChanged)]
    assert state_changes == [
        SessionState.ARMED,
        SessionState.COUNTDOWN,
        SessionState.CAPTURING,
        SessionState.COUNTDOWN,
        SessionState.CAPTURING,
        SessionState.REVIEW,
    ]

    db: sqlite3.Connection = session_manager._db  # noqa: SLF001
    capture_rows = db.execute(
        "SELECT id FROM captures WHERE session_id = ?", (session_manager.session_id,)
    ).fetchall()
    assert len(capture_rows) == 2


async def test_next_trigger_waits_for_previous_download_full_to_finish(
    worker_port: int, tmp_path: Path
) -> None:
    """The 'critical guard' from IMPLEMENTATION_PLAN.md §5: the next shot's
    trigger must not fire while the previous shot's full-resolution download
    is still in flight, even if the countdown timer has already elapsed.

    Wraps the camera client's download_full/trigger_capture with timestamped
    logging and makes shot 1's download artificially slow (much slower than
    the countdown), then asserts shot 2's trigger_capture call only happens
    after shot 1's download_full call returns.
    """
    session_manager = _templated_session_manager(worker_port, tmp_path, "two-shot-event")
    await session_manager.camera.connect()

    call_log: list[tuple[str, float]] = []
    slow_download_s = 10 * _SHORT_COUNTDOWN_S  # much slower than the countdown

    orig_download_full = session_manager.camera.download_full
    orig_trigger_capture = session_manager.camera.trigger_capture

    download_call_count = 0

    async def slow_download_full(capture_id: str):  # type: ignore[no-untyped-def]
        nonlocal download_call_count
        download_call_count += 1
        call_log.append((f"download_full_start_{download_call_count}", time.monotonic()))
        if download_call_count == 1:
            await asyncio.sleep(slow_download_s)
        result = await orig_download_full(capture_id)
        call_log.append((f"download_full_end_{download_call_count}", time.monotonic()))
        return result

    async def logged_trigger_capture():  # type: ignore[no-untyped-def]
        call_log.append(
            (
                f"trigger_{len([c for c in call_log if c[0].startswith('trigger')])}",
                time.monotonic(),
            )
        )
        return await orig_trigger_capture()

    session_manager.camera.download_full = slow_download_full  # type: ignore[method-assign]
    session_manager.camera.trigger_capture = logged_trigger_capture  # type: ignore[method-assign]

    await session_manager.arm()
    await session_manager.capture()

    assert session_manager.state == SessionState.REVIEW
    assert len(session_manager.capture_ids) == 2

    events_by_label = {label: ts for label, ts in call_log}
    first_download_end = events_by_label["download_full_end_1"]
    second_trigger = events_by_label["trigger_1"]  # the *second* trigger_capture call (0-indexed)

    assert second_trigger >= first_download_end, (
        "shot 2's trigger fired before shot 1's download_full finished — "
        "the camera.idle gate did not hold the countdown"
    )
