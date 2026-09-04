"""HTTP tests for the admin panel routes (IMPLEMENTATION_PLAN.md T-3.8..T-3.12):
event switching/editing, template preview, live status, actions
(test-shot/reconnect/shutdown), and the debug/health preflight checklist.

Built against a minimal FastAPI app wiring only what these routers need
(`admin_auth.router` + `admin.router` + `debug.router`), the same pattern
`test_kiosk_routes.py`/`test_admin_auth.py` use. The camera client talks to
the in-thread mock worker from `tests/unit/conftest.py`'s `worker_port`
fixture; templates/fixtures are read from the real repo `templates/` and
`fixtures/shots/` directories since `web/routers/admin.py` resolves those by
fixed repo-root convention (matching `web/app.py`'s own `TEMPLATES_DIR`), not
via Settings.
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from photobooth.camera.client import CameraWorkerClient
from photobooth.config.models import (
    AdminConfig,
    CameraConfig,
    DeliveryConfig,
    EventsConfig,
    PreviewConfig,
    PrintingConfig,
    Settings,
    StorageConfig,
    WebConfig,
)
from photobooth.printing.backend import NullPrinter
from photobooth.storage import db as storage_db
from photobooth.storage.repos import CaptureRepo, SessionRepo
from photobooth.web.routers import admin, admin_auth, debug
from photobooth.web.session import SessionManager

_PIN = "1234"
_SECRET = "test-secret-key"
_REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture
def events_dir(tmp_path: Path) -> Path:
    base = tmp_path / "events"
    event_dir = base / "test-event"
    event_dir.mkdir(parents=True)
    (event_dir / "event.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "test-event",
                "title": "Test Event",
                "date": "2026-09-01",
                "template": "collage-2x2.yaml",
                "background_image": "",
                "gallery_enabled": True,
                # collage-2x2.yaml's text overlay needs {event.couple}.
                "vars": {"couple": "A & B"},
            }
        )
    )
    return base


@pytest.fixture
def settings(tmp_path: Path, events_dir: Path) -> Settings:
    return Settings(
        profile="dev",
        camera=CameraConfig(),
        preview=PreviewConfig(stream_url="http://127.0.0.1:1/does-not-exist"),
        printing=PrintingConfig(),
        delivery=DeliveryConfig(),
        storage=StorageConfig(sqlite_path=tmp_path / "test.db"),
        web=WebConfig(),
        events=EventsConfig(base_dir=events_dir, active_event_id="test-event"),
        admin=AdminConfig(pin=_PIN, secret_key=_SECRET),
    )


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    return storage_db.connect(tmp_path / "session.db")


@pytest.fixture
def session_manager(
    worker_port: int, tmp_path: Path, db_conn: sqlite3.Connection
) -> SessionManager:
    camera = CameraWorkerClient("127.0.0.1", worker_port)
    return SessionManager(camera=camera, db=db_conn, captures_dir=tmp_path / "captures")


@pytest.fixture
def app(
    settings: Settings, session_manager: SessionManager, db_conn: sqlite3.Connection
) -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.state.settings = settings
    fastapi_app.state.camera_client = session_manager.camera
    fastapi_app.state.session_manager = session_manager
    fastapi_app.state.active_event_id = "test-event"
    fastapi_app.state.db = db_conn
    # Printing not configured by default (T-4.8/T-4.9) — matches
    # build_printer_backend's `config.backend is None` case. Tests that need
    # a real backend override this on the app instance directly.
    fastapi_app.state.printer_backend = None
    fastapi_app.include_router(admin_auth.router)
    fastapi_app.include_router(admin.router)
    fastapi_app.include_router(debug.router)
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Context-manager form: the camera client's asyncio streams are bound to
    # whichever event loop opened the TCP connection, so every request in a
    # test must share one portal (see test_kiosk_routes.py's docstring).
    with TestClient(app) as c:
        yield c


def _login(client: TestClient) -> None:
    response = client.post("/admin/login", json={"pin": _PIN})
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/admin/events"),
        ("GET", "/admin/events/test-event"),
        ("GET", "/admin/templates"),
        ("GET", "/admin/status"),
        ("POST", "/admin/actions/reconnect-camera"),
    ],
)
def test_admin_routes_require_auth(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)
    assert response.status_code == 401


def test_debug_health_has_no_auth_gate(client: TestClient) -> None:
    # Matches the existing /debug/traces and /debug/timings, which also have
    # no auth today — see this task's report for why /debug/health follows
    # that precedent rather than gating on require_admin.
    response = client.get("/debug/health")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# T-3.8: events
# ---------------------------------------------------------------------------


def test_list_events_returns_the_seeded_event(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/events")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "test-event"
    assert body[0]["template"] == "collage-2x2.yaml"


def test_get_event_returns_full_config(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/events/test-event")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "test-event"
    assert body["vars"] == {"couple": "A & B"}


def test_get_event_404_for_unknown_event(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/events/does-not-exist")
    assert response.status_code == 404


def test_update_event_writes_yaml_back_to_disk(client: TestClient, events_dir: Path) -> None:
    _login(client)
    get_response = client.get("/admin/events/test-event")
    body = get_response.json()
    body["title"] = "Updated Title"

    put_response = client.put("/admin/events/test-event", json=body)
    assert put_response.status_code == 200
    assert put_response.json()["title"] == "Updated Title"

    on_disk = yaml.safe_load((events_dir / "test-event" / "event.yaml").read_text())
    assert on_disk["title"] == "Updated Title"


def test_update_event_rejects_mismatched_id(client: TestClient) -> None:
    _login(client)
    body = client.get("/admin/events/test-event").json()
    body["id"] = "some-other-id"

    response = client.put("/admin/events/test-event", json=body)
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Event tool: templates, create, duplicate, delete
# ---------------------------------------------------------------------------


def test_list_event_templates_returns_three_presets(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/event-templates")
    assert response.status_code == 200
    body = response.json()
    ids = {preset["id"] for preset in body}
    assert ids == {"wedding", "birthday", "corporate"}


def test_create_event_from_preset_seeds_theme_and_modes(
    client: TestClient, events_dir: Path
) -> None:
    _login(client)
    response = client.post(
        "/admin/events",
        json={
            "id": "new-wedding",
            "title": "New Wedding",
            "date": "2027-01-01",
            "based_on": "wedding",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["theme"]["primary_color"] == "#C98A93"
    assert body["theme"]["scrim_color"] == "#241A1C"
    assert len(body["modes"]) == 2
    assert (events_dir / "new-wedding" / "event.yaml").is_file()


def test_create_event_from_scratch_uses_schema_defaults(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events", json={"id": "blank-event", "title": "Blank", "date": "", "based_on": None}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["theme"]["primary_color"] == ""
    assert body["modes"] == []


def test_create_event_rejects_invalid_slug(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events", json={"id": "Not A Slug!", "title": "x", "date": "", "based_on": None}
    )
    assert response.status_code == 400


def test_create_event_409s_on_existing_id(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events", json={"id": "test-event", "title": "x", "date": "", "based_on": None}
    )
    assert response.status_code == 409


def test_duplicate_event_copies_files_and_rewrites_id_and_title(
    client: TestClient, events_dir: Path
) -> None:
    _login(client)
    client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "background"},
        files={"file": ("bg.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    response = client.post(
        "/admin/events/test-event/duplicate",
        json={"new_id": "test-event-copy", "new_title": "Test Event Copy"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "test-event-copy"
    assert body["title"] == "Test Event Copy"
    assert body["vars"] == {"couple": "A & B"}
    assert (events_dir / "test-event-copy" / "background.jpg").is_file()


def test_duplicate_event_404s_for_unknown_source(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events/does-not-exist/duplicate", json={"new_id": "x", "new_title": "x"}
    )
    assert response.status_code == 404


def test_duplicate_event_409s_on_existing_new_id(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events/test-event/duplicate", json={"new_id": "test-event", "new_title": "x"}
    )
    assert response.status_code == 409


def test_delete_event_removes_directory(client: TestClient, events_dir: Path) -> None:
    _login(client)
    client.post(
        "/admin/events", json={"id": "throwaway", "title": "x", "date": "", "based_on": None}
    )
    response = client.delete("/admin/events/throwaway")
    assert response.status_code == 200
    assert not (events_dir / "throwaway").exists()


def test_delete_event_404s_for_unknown_event(client: TestClient) -> None:
    _login(client)
    response = client.delete("/admin/events/does-not-exist")
    assert response.status_code == 404


def test_delete_event_409s_for_active_event(client: TestClient) -> None:
    _login(client)
    response = client.delete("/admin/events/test-event")
    assert response.status_code == 409


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_event_image_saves_file_and_updates_event_yaml(
    client: TestClient, events_dir: Path
) -> None:
    _login(client)
    response = client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "background"},
        files={"file": ("bg.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["background_image"] == "background.jpg"
    assert (events_dir / "test-event" / "background.jpg").is_file()

    on_disk = yaml.safe_load((events_dir / "test-event" / "event.yaml").read_text())
    assert on_disk["background_image"] == "background.jpg"


def test_upload_event_image_logo_kind_sets_logo_field(
    client: TestClient, events_dir: Path
) -> None:
    _login(client)
    response = client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "logo"},
        files={"file": ("logo.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["logo_image"] == "logo.jpg"
    assert (events_dir / "test-event" / "logo.jpg").is_file()


def test_upload_event_image_rejects_unsupported_extension(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "background"},
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_event_image_404s_for_unknown_event(client: TestClient) -> None:
    _login(client)
    response = client.post(
        "/admin/events/does-not-exist/upload-image",
        data={"kind": "background"},
        files={"file": ("bg.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    assert response.status_code == 404


def test_upload_event_image_replaces_old_file_with_different_extension(
    client: TestClient, events_dir: Path
) -> None:
    _login(client)
    client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "background"},
        files={"file": ("bg.jpg", _fake_jpeg_bytes(), "image/jpeg")},
    )
    assert (events_dir / "test-event" / "background.jpg").is_file()

    png_bytes = b"\x89PNG\r\n\x1a\nfake-png-body"
    response = client.post(
        "/admin/events/test-event/upload-image",
        data={"kind": "background"},
        files={"file": ("bg.png", png_bytes, "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["background_image"] == "background.png"
    assert (events_dir / "test-event" / "background.png").is_file()
    assert not (events_dir / "test-event" / "background.jpg").exists()


def test_activate_event_mutates_app_state(client: TestClient, app: FastAPI) -> None:
    _login(client)
    response = client.post("/admin/events/test-event/activate")
    assert response.status_code == 200
    assert response.json() == {"active_event_id": "test-event"}
    assert app.state.active_event_id == "test-event"


def test_activate_unknown_event_404s_and_does_not_mutate_state(
    client: TestClient, app: FastAPI
) -> None:
    _login(client)
    response = client.post("/admin/events/does-not-exist/activate")
    assert response.status_code == 404
    assert app.state.active_event_id == "test-event"


# ---------------------------------------------------------------------------
# T-3.9: templates
# ---------------------------------------------------------------------------


def test_list_templates_finds_the_repo_template(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/templates")
    assert response.status_code == 200
    names = [t["name"] for t in response.json()]
    assert "collage-2x2.yaml" in names


def test_preview_template_renders_a_jpeg(client: TestClient) -> None:
    _login(client)
    response = client.post("/admin/templates/collage-2x2.yaml/preview")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"  # JPEG SOI marker


def test_preview_unknown_template_404s(client: TestClient) -> None:
    _login(client)
    response = client.post("/admin/templates/does-not-exist.yaml/preview")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# T-3.10: status
# ---------------------------------------------------------------------------


def test_status_reports_camera_disconnected_before_connect(client: TestClient) -> None:
    _login(client)
    response = client.get("/admin/status")
    assert response.status_code == 200
    body = response.json()
    assert body["camera"]["status"] == "red"
    assert body["printer"]["status"] == "not_configured"
    assert set(body.keys()) == {"camera", "preview", "disk", "network", "printer"}


def test_status_reports_camera_connected_after_connect(
    client: TestClient, session_manager: SessionManager
) -> None:
    assert client.portal is not None
    client.portal.call(session_manager.camera.connect)
    _login(client)

    response = client.get("/admin/status")
    assert response.status_code == 200
    assert response.json()["camera"]["status"] == "green"


def test_status_reflects_a_real_printer_backend(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    _login(client)

    response = client.get("/admin/status")
    assert response.status_code == 200
    assert response.json()["printer"]["status"] == "green"


# ---------------------------------------------------------------------------
# T-3.11: actions
# ---------------------------------------------------------------------------


def test_test_shot_requires_connected_camera(client: TestClient) -> None:
    _login(client)
    response = client.post("/admin/actions/test-shot")
    assert response.status_code == 502


def test_test_shot_returns_captured_image_info(
    client: TestClient, session_manager: SessionManager
) -> None:
    assert client.portal is not None
    client.portal.call(session_manager.camera.connect)
    _login(client)

    response = client.post("/admin/actions/test-shot")
    assert response.status_code == 200
    body = response.json()
    assert body["capture_id"]
    assert body["image_url"].startswith("/captures/admin-test-")


def test_reconnect_camera_action(client: TestClient, session_manager: SessionManager) -> None:
    assert client.portal is not None
    client.portal.call(session_manager.camera.connect)
    _login(client)

    response = client.post("/admin/actions/reconnect-camera")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_shutdown_camera_action_dismisses_and_disconnects(
    client: TestClient, session_manager: SessionManager
) -> None:
    assert client.portal is not None
    client.portal.call(session_manager.camera.connect)
    _login(client)

    response = client.post("/admin/actions/shutdown-camera")
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    status = client.portal.call(session_manager.camera.get_status)
    assert status["connected"] is False


# ---------------------------------------------------------------------------
# T-3.12: /debug/health
# ---------------------------------------------------------------------------


def test_debug_health_returns_named_checks(client: TestClient) -> None:
    response = client.get("/debug/health")
    assert response.status_code == 200
    checks = response.json()
    names = {c["name"] for c in checks}
    assert {"camera_connected", "camera_idle", "preview_stream", "disk_free", "network"} <= names
    for check in checks:
        assert check["status"] in {"green", "red", "gray", "not_available", "not_configured"}


def test_debug_health_reflects_a_real_printer_backend(
    client: TestClient, app: FastAPI, tmp_path: Path
) -> None:
    app.state.printer_backend = NullPrinter(output_dir=tmp_path / "prints")
    response = client.get("/debug/health")
    assert response.status_code == 200
    checks = {c["name"]: c for c in response.json()}
    assert checks["printer_online_with_media"]["status"] == "green"


# ---------------------------------------------------------------------------
# T-4.9: reprint from admin
# ---------------------------------------------------------------------------


def _seed_capture(db_conn: sqlite3.Connection) -> str:
    SessionRepo(db_conn).create("reprint-session", event_id="test-event", state="review")
    CaptureRepo(db_conn).create("reprint-capture", "reprint-session")
    return "reprint-capture"


def _real_jpeg_bytes() -> bytes:
    # NullPrinter.submit() actually opens the file with PIL (unlike
    # PrintQueue.submit(), which only enqueues a job payload) — a fake JPEG
    # magic-number stub isn't enough here, this needs to decode.
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buffer, format="JPEG")
    return buffer.getvalue()


def test_reprint_404s_for_unknown_capture(client: TestClient) -> None:
    _login(client)
    response = client.post("/admin/actions/reprint/does-not-exist")
    assert response.status_code == 404


def test_reprint_404s_when_capture_image_file_missing(
    client: TestClient, db_conn: sqlite3.Connection
) -> None:
    capture_id = _seed_capture(db_conn)
    _login(client)
    response = client.post(f"/admin/actions/reprint/{capture_id}")
    assert response.status_code == 404


def test_reprint_rejected_when_printer_not_configured(
    client: TestClient, db_conn: sqlite3.Connection
) -> None:
    capture_id = _seed_capture(db_conn)
    (admin.CAPTURES_DIR / f"{capture_id}.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
    try:
        _login(client)
        response = client.post(f"/admin/actions/reprint/{capture_id}")
        assert response.status_code == 409
    finally:
        (admin.CAPTURES_DIR / f"{capture_id}.jpg").unlink(missing_ok=True)


def test_reprint_succeeds_and_bypasses_the_guest_print_limit(
    client: TestClient, app: FastAPI, db_conn: sqlite3.Connection, tmp_path: Path
) -> None:
    printer = NullPrinter(output_dir=tmp_path / "prints")
    app.state.printer_backend = printer
    capture_id = _seed_capture(db_conn)
    (admin.CAPTURES_DIR / f"{capture_id}.jpg").write_bytes(_real_jpeg_bytes())
    try:
        _login(client)
        # Reprint repeatedly — an admin override has no per-session cap,
        # unlike PrintQueue.submit() (T-4.7's guest-facing limit).
        for _ in range(3):
            response = client.post(f"/admin/actions/reprint/{capture_id}")
            assert response.status_code == 200
            assert response.json() == {"ok": True}
        assert len(printer.jobs) == 3
    finally:
        (admin.CAPTURES_DIR / f"{capture_id}.jpg").unlink(missing_ok=True)
