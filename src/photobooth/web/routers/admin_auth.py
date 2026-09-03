"""Admin auth — shared-PIN login, signed session cookie, and the
`require_admin` dependency other admin routers gate on.

Design (IMPLEMENTATION_PLAN.md T-3.7, photobooth-plan.md §7): this is a
single-booth kiosk, not a multi-user system, so there are no user accounts —
one shared PIN, configured server-side (`config/models.py`'s `AdminConfig`),
grants a signed session token. The PIN is compared with
`hmac.compare_digest` to avoid trivial timing attacks, even though a numeric
PIN doesn't call for a real password-hashing library.

Token shape: `f"{issued_at}.{signature}"` where `signature` is
`HMAC-SHA256(secret_key, issued_at)`, hex-encoded. `issued_at` is a Unix
timestamp (integer seconds, ASCII decimal) — deliberately not carrying any
other claims, since there's exactly one admin "identity" to assert. Verifying
a token means: signature checks out AND `now - issued_at <= session_ttl`.
This is a roll-your-own `itsdangerous`-style signed+timestamped token rather
than a dependency, since the whole thing is ~10 lines of stdlib `hmac`.

The token lives in an httpOnly, SameSite=Strict cookie — even on a LAN kiosk
app, keeping a bearer credential out of reach of JS (no XSS exfiltration
path) is the right default when it costs nothing.

No login rate-limiting / lockout in v1 — see Admin.svelte's integration
notes for why that's an accepted gap, not an oversight.
"""

from __future__ import annotations

import hmac
import time
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from photobooth.config.models import Settings

router = APIRouter(prefix="/admin", tags=["admin-auth"])

COOKIE_NAME = "photobooth_admin_session"


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings)]


def _sign(secret_key: str, issued_at: int) -> str:
    return hmac.new(secret_key.encode(), str(issued_at).encode(), sha256).hexdigest()


def make_token(secret_key: str, *, now: float | None = None) -> str:
    issued_at = int(now if now is not None else time.time())
    return f"{issued_at}.{_sign(secret_key, issued_at)}"


def verify_token(
    token: str, secret_key: str, session_ttl_hours: float, *, now: float | None = None
) -> bool:
    """True iff `token` was signed by `secret_key` and is not expired."""
    try:
        issued_at_raw, signature = token.split(".", 1)
        issued_at = int(issued_at_raw)
    except ValueError:
        return False

    expected = _sign(secret_key, issued_at)
    if not hmac.compare_digest(signature, expected):
        return False

    current = now if now is not None else time.time()
    age_s = current - issued_at
    if age_s < 0:  # clock skew / forged future timestamp — reject, don't clamp
        return False
    return age_s <= session_ttl_hours * 3600


def _set_session_cookie(response: Response, settings: Settings) -> None:
    token = make_token(settings.admin.secret_key)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(settings.admin.session_ttl_hours * 3600),
        httponly=True,
        samesite="strict",
        # `secure=True` would silently break this over plain-HTTP LAN access
        # (photobooth-plan.md §7: "/admin from a laptop on the booth's
        # network") — no TLS termination exists in this deployment. httpOnly
        # + SameSite=Strict is the meaningful protection here; Secure is a
        # transport-security add-on this deployment doesn't have.
        secure=False,
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


async def require_admin(request: Request, settings: SettingsDep) -> None:
    """FastAPI dependency for gating admin-only routes.

    Usage in a downstream router (T-3.8 onward):

        from photobooth.web.routers.admin_auth import require_admin

        router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])

    Raises 401 if the session cookie is missing, malformed, forged, or
    expired. Returns nothing on success — callers don't need an identity,
    just "is this an authenticated admin session."
    """
    token = request.cookies.get(COOKIE_NAME)
    if token is None or not verify_token(
        token, settings.admin.secret_key, settings.admin.session_ttl_hours
    ):
        raise HTTPException(status_code=401, detail="admin authentication required")


class LoginRequest(BaseModel):
    pin: str


@router.post("/login")
async def login(body: LoginRequest, response: Response, settings: SettingsDep) -> dict[str, bool]:
    if not hmac.compare_digest(body.pin, settings.admin.pin):
        raise HTTPException(status_code=401, detail="incorrect PIN")
    _set_session_cookie(response, settings)
    return {"ok": True}


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    _clear_session_cookie(response)
    return {"ok": True}


@router.get("/session")
async def get_session(request: Request, settings: SettingsDep) -> dict[str, bool]:
    token = request.cookies.get(COOKIE_NAME)
    authenticated = token is not None and verify_token(
        token, settings.admin.secret_key, settings.admin.session_ttl_hours
    )
    return {"authenticated": authenticated}
