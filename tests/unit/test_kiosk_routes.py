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
from photobooth.printing.backend import NullPrinter
from photobooth.printing.queue import PrintQueue
from photobooth.storage import db as storage_db
from photobooth.storage.queue import JobQueue
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
def events_dir(tmp_path: Path) -> Path:
    base = tmp_path / "events"

    with_bg = base / "test-event"
    with_bg.mkdir(parents=True)
    (with_bg / "background.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")
    (with_bg / "event.yaml").write_text(
        "id: test-event\n"
        'title: "Test Event"\n'
        'date: "2026-09-01"\n'
        'template: "collage-2x2.yaml"\n'
        'background_image: "background.jpg"\n'
    )

    no_bg = base / "no-bg-event"
    no_bg.mkdir(parents=True)
    (no_bg / "event.yaml").write_text(
        "id: no-bg-event\n"
        'title: "No Background Event"\n'
        'template: "collage-2x2.yaml"\n'
    )

    return base


@pytest.fixture
def print_queue(tmp_path: Path) -> PrintQueue:
    conn = storage_db.connect(tmp_path / "jobs.db")
    return PrintQueue(JobQueue(conn), print_limit_per_session=2)


@pytest.fixture
def test_app(
    session_manager: SessionManager, events_dir: Path, print_queue: PrintQueue
) -> FastAPI:
    app = FastAPI()
    app.include_router(kiosk.router)
    app.state.session_manager = session_manager
    app.state.events_dir = events_dir
    app.state.active_event_id = "test-event"
    app.state.kiosk_idle_timeout_s = 42.0
    # Printing not configured by default — matches build_printer_backend's
    # `config.backend is None` case (T-4.8). Tests that need a real backend
    # override this on the app instance directly.
    app.state.printer_backend = None
    app.state.print_queue = print_queue
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


def test_get_active_event_returns_public_info(http_client: TestClient) -> None:
    response = http_client.get("/session/event")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "event_id": "test-event",
        "title": "Test Event",
        "date": "2026-09-01",
        "background_image_url": "/session/event/background",
        # No `modes` configured in the fixture event.yaml -> one synthesized
        # default button, matching pre-mode-buttons single-template behaviour.
        "modes": [{"id": "default", "label": "Take Photo"}],
        "idle_timeout_s": 42.0,
    }


def test_get_active_event_omits_background_url_when_unset(
    http_client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.active_event_id = "no-bg-event"
    response = http_client.get("/session/event")
    assert response.status_code == 200
    assert response.json()["background_image_url"] is None


def test_get_active_event_404_for_unknown_event(
    http_client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.active_event_id = "does-not-exist"
    response = http_client.get("/session/event")
    assert response.status_code == 404


def test_get_event_background_serves_the_file(http_client: TestClient) -> None:
    response = http_client.get("/session/event/background")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8\xff")


def test_get_event_background_404_when_event_has_none(
    http_client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.active_event_id = "no-bg-event"
    response = http_client.get("/session/event/background")
    assert response.status_code == 404


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
            data = ws.receive_text()
            received.append(decoder.decode(data))

    types = [type(e) for e in received]
    assert types[0].__name__ == "StateChanged"
    assert received[0].state == "armed"  # type: ignore[union-attr]
    assert "CountdownStarted" in [t.__name__ for t in types]
    assert "FullImageReady" in [t.__name__ for t in types]
    assert types[-1].__name__ == "StateChanged"
    assert received[-1].state == "review"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# T-4.8: printer-status gate + guest print submission
# ---------------------------------------------------------------------------


def test_printer_status_reports_gray_when_not_configured(http_client: TestClient) -> None:
    response = http_client.get("/session/printer-status")
    assert response.status_code == 200
    assert response.json() == {"status": "gray", "detail": "printing not configured"}


def test_printer_status_reflects_a_real_backend(
    http_client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    test_app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    response = http_client.get("/session/printer-status")
    assert response.status_code == 200
    assert response.json()["status"] == "green"


def test_print_rejected_when_printer_not_configured(http_client: TestClient) -> None:
    response = http_client.post("/session/print", json={"capture_id": "does-not-exist"})
    assert response.status_code == 409


def test_print_404s_for_missing_capture_file(
    http_client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    test_app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    response = http_client.post("/session/print", json={"capture_id": "does-not-exist"})
    assert response.status_code == 404


def test_print_succeeds_and_reports_remaining(
    http_client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    test_app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    capture_id = "print-test-01"
    (kiosk.CAPTURES_DIR / f"{capture_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    try:
        response = http_client.post("/session/print", json={"capture_id": capture_id})
        assert response.status_code == 200
        assert response.json() == {"remaining": 1}
    finally:
        (kiosk.CAPTURES_DIR / f"{capture_id}.jpg").unlink(missing_ok=True)


def test_print_reports_limit_exceeded(
    http_client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    test_app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    capture_id = "print-test-02"
    (kiosk.CAPTURES_DIR / f"{capture_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    try:
        http_client.post("/session/print", json={"capture_id": capture_id})
        http_client.post("/session/print", json={"capture_id": capture_id})
        response = http_client.post("/session/print", json={"capture_id": capture_id})
        assert response.status_code == 409
    finally:
        (kiosk.CAPTURES_DIR / f"{capture_id}.jpg").unlink(missing_ok=True)
