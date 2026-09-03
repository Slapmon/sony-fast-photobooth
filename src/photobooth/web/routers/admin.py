"""Admin panel routes (IMPLEMENTATION_PLAN.md T-3.8..T-3.11): event
switching/editing, template preview, live status, and out-of-band actions
(test shot, camera reconnect, clean shutdown).

Every route here is gated by `require_admin` (T-3.7, `admin_auth.py`) at the
router level — no per-route dependency needed.

`TEMPLATES_DIR` mirrors `web/app.py`'s own module-level constant of the same
name (repo-root `templates/` convention, fixed by layout rather than
Settings-driven — see app.py's comment). It's duplicated here rather than
read off `app.state` because app.py doesn't currently publish it there and
this task's constraints say not to edit app.py's lifespan wiring. Same story
for `CAPTURES_DIR`, needed here so the admin test-shot action can save its
result somewhere the existing `/captures` static mount already serves.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError, CameraError
from photobooth.config.event import EventConfig, load_event
from photobooth.config.models import Settings
from photobooth.core.state import InvalidTransitionError
from photobooth.pipeline.compositor import render_variant
from photobooth.pipeline.template import TemplateValidationError, load_template
from photobooth.printing.backend import PrinterBackend
from photobooth.storage.repos import CaptureRepo
from photobooth.web import health_checks
from photobooth.web.routers.admin_auth import require_admin
from photobooth.web.session import SessionManager

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

TEMPLATES_DIR = Path("templates")
CAPTURES_DIR = Path("out/captures")
SAMPLE_SHOT = Path("fixtures/shots/sample-01.jpg")

# Created at import time (matching web/app.py's own module-level
# CAPTURES_DIR.mkdir() call) rather than inside test_shot() — ruff's
# ASYNC240 flags blocking pathlib calls inside async def bodies.
CAPTURES_DIR.mkdir(parents=True, exist_ok=True)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_camera_client(request: Request) -> CameraWorkerClient:
    return request.app.state.camera_client  # type: ignore[no-any-return]


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.session_manager  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings)]
CameraClientDep = Annotated[CameraWorkerClient, Depends(get_camera_client)]
SessionManagerDep = Annotated[SessionManager, Depends(get_session_manager)]


# ---------------------------------------------------------------------------
# T-3.8: event switching, event config editor
# ---------------------------------------------------------------------------


@router.get("/events")
def list_events(settings: SettingsDep) -> list[dict[str, Any]]:
    base_dir = settings.events.base_dir
    if not base_dir.is_dir():
        return []
    events: list[dict[str, Any]] = []
    for child in sorted(base_dir.iterdir()):
        if not child.is_dir() or not (child / "event.yaml").is_file():
            continue
        try:
            event = load_event(base_dir, child.name)
        except (OSError, ValueError) as exc:  # pydantic raises ValueError subclasses
            events.append({"id": child.name, "error": str(exc)})
            continue
        events.append(
            {
                "id": event.id,
                "title": event.title,
                "date": event.date,
                "template": event.template,
                "gallery_enabled": event.gallery_enabled,
            }
        )
    return events


@router.get("/events/{event_id}")
def get_event(event_id: str, settings: SettingsDep) -> EventConfig:
    try:
        return load_event(settings.events.base_dir, event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="event not found") from exc


@router.put("/events/{event_id}")
def update_event(event_id: str, body: EventConfig, settings: SettingsDep) -> EventConfig:
    if body.id != event_id:
        raise HTTPException(
            status_code=400, detail="event id in request body must match the URL path"
        )
    event_dir = settings.events.base_dir / event_id
    if not event_dir.is_dir():
        raise HTTPException(status_code=404, detail="event not found")
    path = event_dir / "event.yaml"
    # body has already round-tripped through EventConfig validation (FastAPI
    # parses the request body as EventConfig before this function runs), so
    # writing it straight back out is safe per T-3.8's "validate via
    # EventConfig before writing" instruction.
    path.write_text(yaml.safe_dump(body.model_dump(), sort_keys=False))
    return body


@router.post("/events/{event_id}/activate")
def activate_event(event_id: str, request: Request, settings: SettingsDep) -> dict[str, str]:
    try:
        load_event(settings.events.base_dir, event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="event not found") from exc
    # Mutates the live app.state attribute directly (see this task's report
    # for exactly which downstream readers do/don't pick this up without a
    # restart).
    request.app.state.active_event_id = event_id
    return {"active_event_id": event_id}


# ---------------------------------------------------------------------------
# T-3.9: template picker with live preview render
# ---------------------------------------------------------------------------


@router.get("/templates")
def list_templates() -> list[dict[str, Any]]:
    if not TEMPLATES_DIR.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            template = load_template(path)
        except TemplateValidationError as exc:
            result.append({"name": path.name, "error": str(exc)})
            continue
        result.append(
            {
                "name": path.name,
                "title": template.name,
                "slot_count": len(template.slots),
            }
        )
    return result


@router.post("/templates/{name}/preview")
def preview_template(name: str, request: Request, settings: SettingsDep) -> Response:
    template_path = TEMPLATES_DIR / name
    if not template_path.is_file():
        raise HTTPException(status_code=404, detail="template not found")
    try:
        template = load_template(template_path)
    except TemplateValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    active_event_id = request.app.state.active_event_id
    try:
        event = load_event(settings.events.base_dir, active_event_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="active event not found") from exc

    if not SAMPLE_SHOT.is_file():
        raise HTTPException(status_code=500, detail=f"sample fixture image missing: {SAMPLE_SHOT}")
    source_images = [SAMPLE_SHOT] * len(template.slots)

    try:
        jpeg_bytes = render_variant(template_path, source_images, "web", event)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(content=jpeg_bytes, media_type="image/jpeg")


# ---------------------------------------------------------------------------
# T-3.10: live camera/printer/network/disk status
# ---------------------------------------------------------------------------


@router.get("/status")
async def get_status(request: Request, settings: SettingsDep) -> dict[str, Any]:
    camera_client: CameraWorkerClient = request.app.state.camera_client
    printer_backend: PrinterBackend | None = request.app.state.printer_backend
    checks = (
        health_checks.check_camera(camera_client),
        health_checks.check_preview(
            settings.preview.stream_url, settings.preview.connect_timeout_s
        ),
        health_checks.check_disk(settings.storage.sqlite_path.parent),
        health_checks.check_network(),
    )
    if printer_backend is not None:
        camera, preview, disk, network, printer = await asyncio.gather(
            *checks, printer_backend.status()
        )
    else:
        camera, preview, disk, network = await asyncio.gather(*checks)
        printer = health_checks.NOT_CONFIGURED_PRINTER
    return {
        "camera": camera,
        "preview": preview,
        "disk": disk,
        "network": network,
        "printer": printer,
    }


# ---------------------------------------------------------------------------
# T-3.11: test shot, camera reconnect, clean shutdown
# ---------------------------------------------------------------------------


@router.post("/actions/test-shot")
async def test_shot(camera_client: CameraClientDep) -> dict[str, Any]:
    """Fires a real capture out-of-band, bypassing SessionManager's guest
    state machine entirely (see this task's report — calling straight
    through CameraWorkerClient is a deliberate judgment call, not an
    oversight, and carries a documented risk if fired mid-guest-session).
    """
    try:
        capture_id = await camera_client.trigger_capture()
        full = await camera_client.download_full(capture_id)
    except (CameraError, CameraDisconnectedError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    filename = f"admin-test-{capture_id}.jpg"
    (CAPTURES_DIR / filename).write_bytes(full.data)
    return {
        "capture_id": capture_id,
        "image_url": f"/captures/{filename}",
        "width": full.width,
        "height": full.height,
    }


@router.post("/actions/reconnect-camera")
async def reconnect_camera(camera_client: CameraClientDep) -> dict[str, bool]:
    try:
        await camera_client.reconnect()
    except (CameraError, CameraDisconnectedError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/actions/shutdown-camera")
async def shutdown_camera(
    camera_client: CameraClientDep, session_manager: SessionManagerDep
) -> dict[str, bool]:
    """ "Clean shutdown" (photobooth-plan.md §7) interpreted as: return the
    guest flow to IDLE and cleanly release the camera handle for end-of-event
    teardown. Explicitly NOT an OS/process shutdown — see this task's report.
    """
    # Already idle, or mid-capture in a state dismiss() can't reach from
    # directly — not fatal for a teardown action; the camera disconnect
    # below is the part that actually matters here.
    with contextlib.suppress(InvalidTransitionError):
        await session_manager.dismiss()
    try:
        await camera_client.disconnect()
    except (CameraError, CameraDisconnectedError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": True}


# ---------------------------------------------------------------------------
# T-4.9: reprint from admin (operator override, bypasses the guest print limit)
# ---------------------------------------------------------------------------


@router.post("/actions/reprint/{capture_id}")
async def reprint_capture(capture_id: str, request: Request) -> dict[str, bool]:
    """Deliberate ADMIN OVERRIDE: goes straight through
    `PrinterBackend.submit()` rather than `PrintQueue.submit()`, bypassing
    the guest per-session print limit entirely (photobooth-plan.md:
    "someone will drop a print in a drink" — an operator must always be able
    to reprint, regardless of how many times the guest already printed).
    Mirrors `test_shot`'s existing pattern of calling straight through a
    backend, same judgment call, same justification.
    """
    db = request.app.state.db
    session_id = CaptureRepo(db).get_session_id(capture_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="capture not found")

    image_path = CAPTURES_DIR / f"{capture_id}.jpg"
    if not image_path.is_file():
        raise HTTPException(status_code=404, detail="capture image file not found")

    printer_backend: PrinterBackend | None = request.app.state.printer_backend
    if printer_backend is None:
        raise HTTPException(status_code=409, detail="printing not configured")

    try:
        await printer_backend.submit(image_path, session_id)
    except Exception as exc:  # backend-specific errors (PrinterOfflineError, CUPS, ...)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"ok": True}
