"""HTTP/WebSocket route tests for the kiosk router, against a minimal FastAPI
app (not the real app.py lifespan/subprocess) wired to the in-thread mock
camera worker.

The TestClient is used as a context manager so all requests in a test share
one event-loop portal — the camera client's asyncio stream reader/writer are
bound to whichever loop opened the TCP connection, so opening the connection
and issuing requests on different portals (the TestClient default when not
used as a context manager) would break the connection between calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import msgspec
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from photobooth.camera.client import CameraWorkerClient
from photobooth.core.events import Event
from photobooth.storage import db as storage_db
from photobooth.web.routers import kiosk
from photobooth.web.session import SessionManager

_SHORT_COUNTDOWN_S = 0.05


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


@pytest.fixture
def test_app(session_manager: SessionManager) -> FastAPI:
    app = FastAPI()
    app.include_router(kiosk.router)
    app.state.session_manager = session_manager
    return app


@pytest.fixture
def http_client(test_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(test_app) as client:
        yield client


def test_arm_returns_session_id(http_client: TestClient) -> None:
    response = http_client.post("/session/arm")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"]
    assert body["state"] == "armed"


def test_capture_after_arm_returns_review(
    http_client: TestClient, session_manager: SessionManager
) -> None:
    assert http_client.portal is not None
    http_client.portal.call(session_manager.camera.connect)

    http_client.post("/session/arm")
    response = http_client.post("/session/capture")
    assert response.status_code == 200
    assert response.json() == {"state": "review"}


def test_capture_before_arm_returns_409(http_client: TestClient) -> None:
    response = http_client.post("/session/capture")
    assert response.status_code == 409


def test_websocket_receives_events_in_order(
    http_client: TestClient, session_manager: SessionManager
) -> None:
    assert http_client.portal is not None
    http_client.portal.call(session_manager.camera.connect)
    decoder = msgspec.json.Decoder(type=Event)

    with http_client.websocket_connect("/ws") as ws:
        http_client.post("/session/arm")
        http_client.post("/session/capture")

        received = []
        for _ in range(7):
            data = ws.receive_bytes()
            received.append(decoder.decode(data))

    types = [type(e) for e in received]
    assert types[0].__name__ == "StateChanged"
    assert received[0].state == "armed"  # type: ignore[union-attr]
    assert "CountdownStarted" in [t.__name__ for t in types]
    assert "FullImageReady" in [t.__name__ for t in types]
    assert types[-1].__name__ == "StateChanged"
    assert received[-1].state == "review"  # type: ignore[union-attr]
