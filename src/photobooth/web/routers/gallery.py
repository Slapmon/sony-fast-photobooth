"""Read-only gallery routes: guests browse an event's captures after the
fact (photobooth-plan.md §7 "Gallery", IMPLEMENTATION_PLAN.md T-3.4/T-3.5).

Security note (photobooth-plan.md §11, "gallery links should be unguessable
tokens... don't let anyone enumerate someone else's wedding"): a disabled
gallery (`EventConfig.gallery_enabled = False`) and a nonexistent
`event_id` both return a plain 404 with an identical body. This is
deliberate — a 403, or any response shape that differs between "disabled"
and "doesn't exist", would let a caller distinguish real event ids from
made-up ones by probing, which defeats the point of using unguessable event
slugs in the first place.

No thumbnail pipeline here on purpose — that's the render worker's job
(T-2.3/T-2.8, in progress on a parallel track). Until it lands, this serves
the existing full-size `/captures/{id}.jpg` files (already static-mounted
in web/app.py). Swap in a real `-thumb.jpg` variant once the render worker
produces one; this router's shape doesn't need to change to do that.
"""

from __future__ import annotations

import mimetypes
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from photobooth.config.event import EventConfig, load_event, resolved_strings
from photobooth.storage.repos import CaptureRepo

router = APIRouter(prefix="/gallery")

_NOT_FOUND_DETAIL = "gallery not found"


def _load_gallery_event(events_dir: Path, event_id: str) -> EventConfig:
    """Shared by every route below: 404s identically for "no such event"
    and "gallery disabled" (see module docstring)."""
    try:
        event = load_event(events_dir, event_id)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None
    if not event.gallery_enabled:
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return event


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db


def get_events_dir(request: Request) -> Path:
    # Falls back to the same default as config.models.EventsConfig.base_dir
    # so this router works standalone even before app.py's lifespan sets
    # app.state.events_dir from Settings (see this task's report for the
    # exact wiring the overseer still needs to add).
    events_dir = getattr(request.app.state, "events_dir", None)
    return events_dir if events_dir is not None else Path("events")


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]
EventsDirDep = Annotated[Path, Depends(get_events_dir)]


@router.get("/{event_id}/captures")
def list_captures(event_id: str, db: DbDep, events_dir: EventsDirDep) -> list[dict[str, str]]:
    _load_gallery_event(events_dir, event_id)
    captures = CaptureRepo(db).list_by_event(event_id)
    return [
        {
            "id": capture_id,
            "created_at": created_at,
            "image_url": f"/captures/{capture_id}.jpg",
        }
        for capture_id, created_at in captures
    ]


@router.get("/{event_id}/info")
def get_gallery_info(event_id: str, events_dir: EventsDirDep) -> dict[str, Any]:
    """Branding for the gallery page (IMPLEMENTATION_PLAN.md's UI-redesign
    follow-up): title, background/logo image URLs (None if unset), and the
    event's theme color. Same shape as GET /session/event's equivalent
    fields, but scoped to a specific `event_id` rather than "whichever
    event is currently active" — a gallery link should keep working for
    its own event even after the booth moves on to a different one.
    """
    event = _load_gallery_event(events_dir, event_id)
    modes = (
        [{"id": mode.id, "label": mode.label} for mode in event.modes]
        if event.modes
        else [{"id": "default", "label": "Take Photo"}]
    )
    return {
        "title": event.title,
        "background_image_url": (
            f"/gallery/{event_id}/background" if event.background_image else None
        ),
        "logo_image_url": f"/gallery/{event_id}/logo" if event.logo_image else None,
        "theme": {"primary_color": event.theme.primary_color},
        "modes": modes,
        "strings": resolved_strings(event),
    }


def _serve_event_image(events_dir: Path, event_id: str, filename: str) -> FileResponse:
    path = events_dir / event_id / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image file not found")
    media_type, _ = mimetypes.guess_type(str(path))
    return FileResponse(path, media_type=media_type)


@router.get("/{event_id}/background")
def get_gallery_background(event_id: str, events_dir: EventsDirDep) -> FileResponse:
    event = _load_gallery_event(events_dir, event_id)
    if not event.background_image:
        raise HTTPException(status_code=404, detail="event has no background image")
    return _serve_event_image(events_dir, event_id, event.background_image)


@router.get("/{event_id}/logo")
def get_gallery_logo(event_id: str, events_dir: EventsDirDep) -> FileResponse:
    event = _load_gallery_event(events_dir, event_id)
    if not event.logo_image:
        raise HTTPException(status_code=404, detail="event has no logo image")
    return _serve_event_image(events_dir, event_id, event.logo_image)
