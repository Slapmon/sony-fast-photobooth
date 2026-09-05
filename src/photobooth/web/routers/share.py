"""Public per-SESSION share routes: a guest scans a QR code right after
their own capture and sees just their own photo(s) — not a whole event's
gallery (IMPLEMENTATION_PLAN.md T-4.3, photobooth-plan.md §11 "Legal &
privacy").

This is deliberately a DIFFERENT feature from `web/routers/gallery.py`'s
`GET /gallery/{event_id}/captures`, which is a per-EVENT listing gated on
`EventConfig.gallery_enabled` and gets ALL of an event's captures. Here,
access is gated purely by possession of an unguessable per-session
`share_token` (`storage/db.py`'s `sessions.share_token` column,
`storage/repos.py`'s `SessionRepo.set_share_token`/`get_by_share_token`) —
there is no separate event-level enable/disable switch for this route,
matching photobooth-plan.md §11's "gallery links should be unguessable
tokens, not sequential IDs" principle extended to a per-guest link.

Security note, matching gallery.py's convention: an unknown token gets a
plain, generic 404 with no distinguishing detail. Unlike gallery.py, a
*valid* token whose session has no captures yet is NOT a 404 — it's a 200
with an empty `captures: []` list, because the token itself is real, the
guest just scanned it before (or during) their photo finishing processing.
Only "this token does not resolve to any session at all" is a 404 here.

Token issuance (SessionRepo.set_share_token) is NOT wired into the live
capture flow by this task — see the docstring on `set_share_token` in
storage/repos.py for exactly when/how a later wave should call it. Until
that wiring lands, these routes are fully correct and testable given a
token that already exists in the DB; they just won't be reachable from a
real guest flow yet.

Wiring note for the overseer: this router is not registered in
`web/app.py` by this task (out of scope — see this task's report). Add:

    app.include_router(share.router)

alongside the other `app.include_router(...)` calls.
"""

from __future__ import annotations

import io
import sqlite3
from typing import Annotated, Any

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from photobooth.storage.repos import CaptureRepo, SessionRepo

router = APIRouter(prefix="/s")

_NOT_FOUND_DETAIL = "not found"


def get_db(request: Request) -> sqlite3.Connection:
    return request.app.state.db  # type: ignore[no-any-return]


DbDep = Annotated[sqlite3.Connection, Depends(get_db)]


def _resolve_session(db: sqlite3.Connection, token: str) -> dict[str, str]:
    session = SessionRepo(db).get_by_share_token(token)
    if session is None:
        # Generic 404, no distinguishing detail — see module docstring and
        # gallery.py's identical convention (photobooth-plan.md §11).
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    return session


def _share_url(request: Request, token: str) -> str:
    """Absolute URL for this token. `DeliveryConfig.public_base_url`, when
    set, wins outright — that's an operator saying "guests should reach
    their photo at THIS public server," typically once a real delivery
    target (e.g. SFTP to an internet-reachable host) is configured, so a
    phone can open it over mobile data with no need to join the venue
    Wi-Fi. Unset (the default): fall back to the *incoming request's own*
    host — what makes the QR scan correctly from a phone on the LAN during
    local testing, since the phone opens a DIFFERENT device than the one
    displaying the QR, so the URL must be whatever host the kiosk browser
    itself is actually being viewed at (its LAN IP), not `localhost`.
    `request.url` already reflects the Host header the kiosk's browser
    sent, correct even behind Vite's dev proxy."""
    base = getattr(request.app.state, "share_public_base_url", "") or (
        f"{request.url.scheme}://{request.url.netloc}"
    )
    return f"{base.rstrip('/')}/s/{token}"


@router.get("/{token}")
def get_share(token: str, db: DbDep) -> dict[str, Any]:
    session = _resolve_session(db, token)
    captures = CaptureRepo(db).list_by_session(session["id"])
    return {
        "session_id": session["id"],
        "captures": [
            {
                "id": capture_id,
                "created_at": created_at,
                "image_url": f"/captures/{capture_id}.jpg",
            }
            for capture_id, created_at in captures
        ],
    }


def _qr_target(request: Request, db: sqlite3.Connection, token: str, session_id: str) -> str:
    """Where the QR actually sends a guest. `share_public_base_url` set
    (DeliveryConfig.public_base_url — a real delivery target's public
    static-file host): link straight to the delivered file, no dependency
    on this app running anywhere else. Unset (LAN-only testing, no real
    delivery target yet): fall back to this app's own `/s/{token}` share
    page, exactly like before this change.
    """
    base = getattr(request.app.state, "share_public_base_url", "")
    if not base:
        return _share_url(request, token)

    # One deliverable per session (the composite for a multi-shot template,
    # or the single raw shot for a 1-slot one — see web/session.py's
    # capture()); list_by_session is already filtered to is_deliverable=1.
    captures = CaptureRepo(db).list_by_session(session_id)
    if not captures:
        # A share_token is only ever issued once the deliverable exists
        # (session.py's _issue_share_token_and_enqueue_uploads runs after
        # _finalize_capture) — this should be unreachable, but a generic
        # 404 is the right failure mode if it ever happens rather than a
        # broken QR image.
        raise HTTPException(status_code=404, detail=_NOT_FOUND_DETAIL)
    capture_id = captures[0][0]
    return f"{base.rstrip('/')}/{capture_id}.jpg"


@router.get("/{token}/qr.png")
def get_share_qr(token: str, request: Request, db: DbDep) -> Response:
    # Resolve first so an unknown token 404s identically to GET /s/{token}
    # rather than generating (and leaking timing/existence info via) a QR
    # code for a token that doesn't exist.
    session = _resolve_session(db, token)

    url = _qr_target(request, db, token, session["id"])
    img = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")
