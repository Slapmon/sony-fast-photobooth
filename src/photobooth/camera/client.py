"""Async client used by the FastAPI app to talk to the camera-worker process.

The worker owns the blocking CameraBackend calls (protocol.py); this client
is the async-safe side of the IPC boundary described in photobooth-plan.md
§3.2 and IMPLEMENTATION_PLAN.md §1 (TCP loopback, length-prefixed msgspec
frames — see worker.py for why TCP loopback instead of a UNIX socket).

Requests are serialized through a single asyncio.Lock: the wire protocol is
strict request/response lockstep on one connection (the worker reads one
frame, dispatches, writes one frame, and only then reads the next), so two
concurrent callers issuing requests without a lock would interleave their
frames and desync the connection.
"""

from __future__ import annotations

import asyncio
import contextlib

from photobooth.camera import messages
from photobooth.camera.protocol import (
    CameraDisconnectedError,
    CameraError,
    CapturedImage,
    ImageKind,
)


class CameraWorkerClient:
    """Talks to camera/worker.py over TCP loopback.

    The connection is opened lazily: either call `open()` explicitly before
    the first request, or just start issuing requests and the client will
    open the connection on first use. Call `close()` to release the socket
    (e.g. on app shutdown); a later request will transparently reopen it.
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        except OSError as exc:
            raise CameraDisconnectedError(f"cannot reach camera worker: {exc}") from exc

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(OSError):
                await self._writer.wait_closed()
        self._reader = None
        self._writer = None

    async def _request(self, request: messages.Request) -> messages.Response:
        async with self._lock:
            await self.open()
            assert self._reader is not None
            assert self._writer is not None
            try:
                self._writer.write(messages.encode_request(request))
                await self._writer.drain()
                header = await self._reader.readexactly(4)
                length = messages.read_frame_length(header)
                payload = await self._reader.readexactly(length)
            except (
                OSError,
                asyncio.IncompleteReadError,
                messages.FrameTooLargeError,
            ) as exc:
                await self.close()
                if isinstance(exc, messages.FrameTooLargeError):
                    raise CameraError(f"camera worker sent an oversized frame: {exc}") from exc
                raise CameraDisconnectedError(
                    f"lost connection to camera worker: {exc}"
                ) from exc
            return messages.decode_response(payload)

    def _raise_for_error(self, response: messages.Response) -> None:
        if isinstance(response, messages.ErrorResult):
            if response.error_type == "disconnected":
                raise CameraDisconnectedError(response.message)
            raise CameraError(response.message)

    async def connect(self) -> None:
        response = await self._request(messages.Connect())
        self._raise_for_error(response)

    async def disconnect(self) -> None:
        response = await self._request(messages.Disconnect())
        self._raise_for_error(response)

    async def reconnect(self) -> None:
        response = await self._request(messages.Reconnect())
        self._raise_for_error(response)

    async def get_status(self) -> dict[str, object]:
        response = await self._request(messages.GetStatus())
        self._raise_for_error(response)
        assert isinstance(response, messages.StatusResult)
        return {"connected": response.connected}

    async def trigger_autofocus(self) -> None:
        response = await self._request(messages.TriggerAutofocus())
        self._raise_for_error(response)

    async def trigger_capture(self) -> str:
        response = await self._request(messages.TriggerCapture())
        self._raise_for_error(response)
        assert isinstance(response, messages.CaptureResult)
        return response.capture_id

    async def download_preview(self, capture_id: str) -> CapturedImage | None:
        response = await self._request(messages.DownloadPreview(capture_id=capture_id))
        if isinstance(response, messages.NoPreview):
            return None
        self._raise_for_error(response)
        assert isinstance(response, messages.ImageResult)
        return CapturedImage(
            kind=ImageKind(response.kind),
            data=response.data,
            width=response.width,
            height=response.height,
        )

    async def download_full(self, capture_id: str) -> CapturedImage:
        response = await self._request(messages.DownloadFull(capture_id=capture_id))
        self._raise_for_error(response)
        assert isinstance(response, messages.ImageResult)
        return CapturedImage(
            kind=ImageKind(response.kind),
            data=response.data,
            width=response.width,
            height=response.height,
        )
