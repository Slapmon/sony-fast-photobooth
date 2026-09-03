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

import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from photobooth.config.event import load_event
from photobooth.storage.repos import CaptureRepo

router = APIRouter(prefix="/gallery")

_NOT_FOUND_DETAIL = "gallery not found"


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
    try:
        event = load_event(events_dir, event_id)
    except (FileNotFoundError, OSError):
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL) from None

    if not event.gallery_enabled:
        # Identical status/body to the "event doesn't exist" branch above —
        # see module docstring.
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)

    captures = CaptureRepo(db).list_by_event(event_id)
    return [
        {
            "id": capture_id,
            "created_at": created_at,
            "image_url": f"/captures/{capture_id}.jpg",
        }
        for capture_id, created_at in captures
    ]
