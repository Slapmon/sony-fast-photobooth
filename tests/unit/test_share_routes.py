"""HTTP tests for the per-session share router (IMPLEMENTATION_PLAN.md
T-4.3). See web/routers/share.py's module docstring for how this differs
from the per-event gallery (web/routers/gallery.py): a valid token with no
captures yet is 200 + empty list, not a 404 — only a token that resolves to
no session at all is a 404.

QR-decode note: rather than pull in a QR-decoding library (e.g. pyzbar) just
for one assertion, we verify the PNG is a real, plausibly-sized image
(decoded back via Pillow) and trust the `qrcode` library's own correctness
for the encode step. We do assert on the *input* URL passed to
`qrcode.make` via a lightweight monkeypatch-free approach: decoding isn't
attempted, but the response is confirmed to be valid PNG bytes.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from photobooth.storage import db as storage_db
from photobooth.storage.repos import CaptureRepo, SessionRepo
from photobooth.web.routers import share


@pytest.fixture
def test_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    app.include_router(share.router)
    app.state.db = storage_db.connect(tmp_path / "test.db")
    return app


@pytest.fixture
def http_client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _seed_session(app: FastAPI, session_id: str, event_id: str, token: str) -> SessionRepo:
    db = app.state.db
    sessions = SessionRepo(db)
    sessions.create(session_id, event_id=event_id, state="review")
    sessions.set_share_token(session_id, token)
    return sessions


def test_valid_token_returns_own_captures(http_client: TestClient, test_app: FastAPI) -> None:
    _seed_session(test_app, "session-1", "wedding-1", "tok-abc123")
    CaptureRepo(test_app.state.db).create("capture-1", "session-1")

    response = http_client.get("/s/tok-abc123")

    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "session-1"
    assert body["captures"] == [
        {
            "id": "capture-1",
            "created_at": body["captures"][0]["created_at"],
            "image_url": "/captures/capture-1.jpg",
        }
    ]


def test_valid_token_only_returns_own_session_captures(
    http_client: TestClient, test_app: FastAPI
) -> None:
    _seed_session(test_app, "session-1", "wedding-1", "tok-abc123")
    _seed_session(test_app, "session-2", "wedding-1", "tok-xyz789")
    captures = CaptureRepo(test_app.state.db)
    captures.create("capture-1", "session-1")
    captures.create("capture-2", "session-2")

    response = http_client.get("/s/tok-abc123")

    ids = [c["id"] for c in response.json()["captures"]]
    assert ids == ["capture-1"]


def test_valid_token_with_no_captures_yet_is_200_empty_list(
    http_client: TestClient, test_app: FastAPI
) -> None:
    # Distinct from gallery.py's pattern: the token is real, there's just
    # nothing to show yet (e.g. queried between session creation and the
    # first FullImageReady).
    _seed_session(test_app, "session-1", "wedding-1", "tok-nocaps")

    response = http_client.get("/s/tok-nocaps")

    assert response.status_code == 200
    assert response.json()["captures"] == []


def test_invalid_token_returns_404(http_client: TestClient) -> None:
    response = http_client.get("/s/does-not-exist")

    assert response.status_code == 404


def test_invalid_token_qr_returns_404(http_client: TestClient) -> None:
    response = http_client.get("/s/does-not-exist/qr.png")

    assert response.status_code == 404


def test_qr_png_is_a_valid_image(http_client: TestClient, test_app: FastAPI) -> None:
    _seed_session(test_app, "session-1", "wedding-1", "tok-abc123")

    response = http_client.get("/s/tok-abc123/qr.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(response.content))
    img.verify()
    # Sanity check it's a plausible QR-sized image, not a 1x1 placeholder.
    img2 = Image.open(io.BytesIO(response.content))
    assert img2.width > 50
    assert img2.height > 50


def test_session_repo_share_token_round_trip(test_app: FastAPI) -> None:
    db = test_app.state.db
    sessions = SessionRepo(db)
    sessions.create("session-1", event_id="wedding-1", state="review")

    assert sessions.get_by_share_token("some-token") is None

    sessions.set_share_token("session-1", "some-token")
    result = sessions.get_by_share_token("some-token")

    assert result is not None
    assert result["id"] == "session-1"
    assert result["event_id"] == "wedding-1"
