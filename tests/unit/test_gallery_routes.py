"""HTTP tests for the gallery router (IMPLEMENTATION_PLAN.md T-3.4/T-3.5).

Covers: listing captures for an event with the gallery enabled, 404 when
disabled, 404 when the event doesn't exist, and — the security-critical bit
(photobooth-plan.md §11) — that the "disabled" and "doesn't exist" 404s are
indistinguishable from the response alone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from photobooth.storage import db as storage_db
from photobooth.storage.repos import CaptureRepo, SessionRepo
from photobooth.web.routers import gallery


def _write_event(events_dir: Path, event_id: str, gallery_enabled: bool) -> None:
    event_dir = events_dir / event_id
    event_dir.mkdir(parents=True)
    (event_dir / "event.yaml").write_text(
        yaml.safe_dump(
            {
                "id": event_id,
                "title": "Test Event",
                "template": "collage-2x2.yaml",
                "gallery_enabled": gallery_enabled,
            }
        )
    )


@pytest.fixture
def events_dir(tmp_path: Path) -> Path:
    d = tmp_path / "events"
    d.mkdir()
    return d


@pytest.fixture
def test_app(tmp_path: Path, events_dir: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(gallery.router)
    app.state.db = storage_db.connect(tmp_path / "test.db")
    app.state.events_dir = events_dir
    return app


@pytest.fixture
def http_client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _seed_capture(app: FastAPI, event_id: str, session_id: str, capture_id: str) -> None:
    db = app.state.db
    SessionRepo(db).create(session_id, event_id=event_id, state="review")
    CaptureRepo(db).create(capture_id, session_id)


def test_list_captures_for_enabled_gallery(
    http_client: TestClient, test_app: FastAPI, events_dir: Path
) -> None:
    _write_event(events_dir, "wedding-1", gallery_enabled=True)
    _seed_capture(test_app, "wedding-1", "session-1", "capture-1")

    response = http_client.get("/gallery/wedding-1/captures")

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {
            "id": "capture-1",
            "created_at": body[0]["created_at"],
            "image_url": "/captures/capture-1.jpg",
        }
    ]


def test_list_captures_only_returns_own_event(
    http_client: TestClient, test_app: FastAPI, events_dir: Path
) -> None:
    _write_event(events_dir, "wedding-1", gallery_enabled=True)
    _write_event(events_dir, "wedding-2", gallery_enabled=True)
    _seed_capture(test_app, "wedding-1", "session-1", "capture-1")
    _seed_capture(test_app, "wedding-2", "session-2", "capture-2")

    response = http_client.get("/gallery/wedding-1/captures")

    assert response.status_code == 200
    ids = [c["id"] for c in response.json()]
    assert ids == ["capture-1"]


def test_disabled_gallery_returns_404(http_client: TestClient, events_dir: Path) -> None:
    _write_event(events_dir, "wedding-1", gallery_enabled=False)

    response = http_client.get("/gallery/wedding-1/captures")

    assert response.status_code == 404


def test_nonexistent_event_returns_404(http_client: TestClient) -> None:
    response = http_client.get("/gallery/does-not-exist/captures")

    assert response.status_code == 404


def test_disabled_and_nonexistent_are_indistinguishable(
    http_client: TestClient, events_dir: Path
) -> None:
    _write_event(events_dir, "wedding-1", gallery_enabled=False)

    disabled_response = http_client.get("/gallery/wedding-1/captures")
    missing_response = http_client.get("/gallery/does-not-exist/captures")

    assert disabled_response.status_code == missing_response.status_code == 404
    assert disabled_response.json() == missing_response.json()
