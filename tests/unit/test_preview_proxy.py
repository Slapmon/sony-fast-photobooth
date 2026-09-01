"""Exercises PreviewProxy and the /preview/stream route against a fake
go2rtc-shaped ASGI app (httpx.ASGITransport), since real go2rtc is a Pi-only
systemd service not present in this dev environment.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from photobooth.preview.proxy import PreviewProxy
from photobooth.web.routers import preview

_FAKE_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=frame"
_FAKE_CHUNKS = [b"--frame\r\nContent-Type: image/jpeg\r\n\r\n", b"fake-jpeg-bytes", b"\r\n"] * 3


def _fake_go2rtc_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/stream.mjpeg")
    async def stream() -> StreamingResponse:
        async def body() -> Iterator[bytes]:  # type: ignore[misc]
            for chunk in _FAKE_CHUNKS:
                yield chunk

        return StreamingResponse(body(), media_type=_FAKE_CONTENT_TYPE)

    return app


@pytest.fixture
def fake_transport() -> httpx.ASGITransport:
    return httpx.ASGITransport(app=_fake_go2rtc_app())


async def test_preview_proxy_relays_bytes_and_content_type(
    fake_transport: httpx.ASGITransport,
) -> None:
    proxy = PreviewProxy(
        "http://go2rtc.test/api/stream.mjpeg", transport=fake_transport
    )
    try:
        content_type, body = await proxy.open_stream()
        assert content_type == _FAKE_CONTENT_TYPE
        received = b"".join([chunk async for chunk in body])
        assert received == b"".join(_FAKE_CHUNKS)
    finally:
        await proxy.aclose()


@pytest.fixture
def test_app(fake_transport: httpx.ASGITransport) -> FastAPI:
    app = FastAPI()
    app.include_router(preview.router)
    app.state.preview_proxy = PreviewProxy(
        "http://go2rtc.test/api/stream.mjpeg", transport=fake_transport
    )
    return app


@pytest.fixture
def http_client(test_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(test_app) as client:
        yield client


def test_preview_stream_route_relays_upstream(http_client: TestClient) -> None:
    response = http_client.get("/preview/stream")
    assert response.status_code == 200
    assert response.headers["content-type"] == _FAKE_CONTENT_TYPE
    assert response.content == b"".join(_FAKE_CHUNKS)


def test_preview_stream_route_returns_502_when_upstream_unreachable() -> None:
    app = FastAPI()
    app.include_router(preview.router)
    # Port 1 is reserved (TCPMUX) and never has anything listening on a dev
    # box, so this reliably refuses the connection without a real go2rtc.
    app.state.preview_proxy = PreviewProxy(
        "http://127.0.0.1:1/api/stream.mjpeg", connect_timeout_s=0.2
    )

    with TestClient(app) as client:
        response = client.get("/preview/stream")
    assert response.status_code == 502
