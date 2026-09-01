"""MJPEG proxy: relays go2rtc's stream to the frontend without decode/re-encode.

go2rtc owns the capture device exclusively (IMPLEMENTATION_PLAN.md §0 — the
stick allows exactly one consumer). This module re-serves
http://127.0.0.1:1984/api/stream.mjpeg?src=photobooth as multipart/x-mixed-replace,
giving fan-out to kiosk + admin without touching /dev/video1 twice. See T-1.10.

Bytes are relayed straight from ``httpx``'s ``aiter_bytes()`` to the browser —
frame boundaries and image content are never inspected, which is the whole
point (photobooth-plan.md §4: decoding/re-encoding is the difference between
~2% and ~60% CPU on a Pi 4).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx


class PreviewProxy:
    """Holds one reusable ``httpx.AsyncClient`` to go2rtc, shared across all
    browser requests, so N kiosk/admin tabs don't each open their own
    connection to go2rtc (which allows exactly one upstream consumer).

    ``transport`` is exposed purely for tests, to point the client at an
    in-process ASGI app via ``httpx.ASGITransport`` instead of a real socket.
    """

    def __init__(
        self,
        stream_url: str,
        connect_timeout_s: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._stream_url = stream_url
        # read=None: an MJPEG stream is long-lived by design, only the
        # initial connect should be timeout-bounded.
        timeout = httpx.Timeout(connect_timeout_s, read=None, write=None, pool=None)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def open_stream(self) -> tuple[str, AsyncIterator[bytes]]:
        """Opens the upstream request and returns go2rtc's real
        ``Content-Type`` (with its generated multipart boundary) alongside an
        async iterator of raw body chunks.

        Split into (content_type, body) rather than a single generator
        because the FastAPI route needs the boundary-bearing Content-Type
        header *before* it can construct its StreamingResponse — a bare
        generator can't hand back a value ahead of its first yield.

        Connection failures (refused, timeout, DNS) raise the underlying
        httpx exception here, before any body bytes are produced — the
        caller (the /preview/stream route) is responsible for turning that
        into an HTTP error response.
        """
        request = self._client.build_request("GET", self._stream_url)
        response = await self._client.send(request, stream=True)
        content_type = response.headers.get("content-type", "multipart/x-mixed-replace")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        return content_type, body()

    async def aclose(self) -> None:
        await self._client.aclose()
