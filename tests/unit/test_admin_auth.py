"""Tests for admin PIN auth (IMPLEMENTATION_PLAN.md T-3.7): login/logout,
session-cookie issuance and verification, and the `require_admin` dependency
that downstream admin routers (T-3.8 onward) will gate on.

Built against a minimal FastAPI app (not the real app.py lifespan), the same
pattern test_kiosk_routes.py uses — wires up just the pieces admin_auth.py
needs: `app.state.settings`.
"""

from __future__ import annotations

import time

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from photobooth.config.models import (
    AdminConfig,
    CameraConfig,
    DeliveryConfig,
    PreviewConfig,
    PrintingConfig,
    Settings,
    StorageConfig,
    WebConfig,
)
from photobooth.web.routers import admin_auth
from photobooth.web.routers.admin_auth import COOKIE_NAME, make_token, require_admin

_PIN = "1234"
_SECRET = "test-secret-key"


def _settings(**admin_overrides: object) -> Settings:
    return Settings(
        profile="dev",
        camera=CameraConfig(),
        preview=PreviewConfig(stream_url="http://example.invalid/stream"),
        printing=PrintingConfig(),
        delivery=DeliveryConfig(),
        storage=StorageConfig(),
        web=WebConfig(),
        admin=AdminConfig(pin=_PIN, secret_key=_SECRET, **admin_overrides),  # type: ignore[arg-type]
    )


@pytest.fixture
def app() -> FastAPI:
    fastapi_app = FastAPI()
    fastapi_app.state.settings = _settings()
    fastapi_app.include_router(admin_auth.router)

    # A protected route standing in for a T-3.8+ admin router, to exercise
    # require_admin exactly the way downstream code will use it.
    protected = APIRouter()

    @protected.get("/admin/_protected")
    async def _protected() -> dict[str, bool]:
        return {"ok": True}

    fastapi_app.include_router(protected, dependencies=[Depends(require_admin)])
    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_login_with_correct_pin_succeeds_and_sets_cookie(client: TestClient) -> None:
    response = client.post("/admin/login", json={"pin": _PIN})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert COOKIE_NAME in response.cookies


def test_login_with_wrong_pin_fails_and_sets_no_cookie(client: TestClient) -> None:
    response = client.post("/admin/login", json={"pin": "wrong"})
    assert response.status_code == 401
    assert COOKIE_NAME not in response.cookies


def test_protected_route_rejects_without_cookie(client: TestClient) -> None:
    response = client.get("/admin/_protected")
    assert response.status_code == 401


def test_protected_route_accepts_with_valid_cookie(client: TestClient) -> None:
    login_response = client.post("/admin/login", json={"pin": _PIN})
    assert login_response.status_code == 200

    response = client.get("/admin/_protected")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_protected_route_rejects_tampered_cookie(client: TestClient) -> None:
    client.post("/admin/login", json={"pin": _PIN})
    client.cookies.set(COOKIE_NAME, "0.deadbeef")
    response = client.get("/admin/_protected")
    assert response.status_code == 401


def test_expired_token_is_rejected(app: FastAPI, client: TestClient) -> None:
    settings = app.state.settings
    old_issued_at = time.time() - (settings.admin.session_ttl_hours * 3600 + 60)
    expired_token = make_token(settings.admin.secret_key, now=old_issued_at)
    client.cookies.set(COOKIE_NAME, expired_token)

    response = client.get("/admin/_protected")
    assert response.status_code == 401


def test_session_endpoint_reports_authenticated_state(client: TestClient) -> None:
    unauth = client.get("/admin/session")
    assert unauth.json() == {"authenticated": False}

    client.post("/admin/login", json={"pin": _PIN})
    auth = client.get("/admin/session")
    assert auth.json() == {"authenticated": True}


def test_logout_clears_cookie_and_revokes_access(client: TestClient) -> None:
    client.post("/admin/login", json={"pin": _PIN})
    assert client.get("/admin/_protected").status_code == 200

    client.post("/admin/logout")
    response = client.get("/admin/_protected")
    assert response.status_code == 401
