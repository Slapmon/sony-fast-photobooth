"""IPC message types for the camera-worker protocol.

Wire format: each frame is a 4-byte big-endian length prefix followed by
that many bytes of msgspec-msgpack-encoded payload. Strict request/response
lockstep over one UNIX socket connection — the worker processes exactly one
command at a time, since it owns a single blocking camera handle for its
entire lifetime (protocol.py, photobooth-plan.md §3.2). No pipelining, no
concurrent requests: the client (client.py) enforces this with a lock.
"""

from __future__ import annotations

from typing import cast

import msgspec


class Connect(msgspec.Struct, tag=True):
    pass


class Disconnect(msgspec.Struct, tag=True):
    pass


class Reconnect(msgspec.Struct, tag=True):
    pass


class GetStatus(msgspec.Struct, tag=True):
    pass


class TriggerAutofocus(msgspec.Struct, tag=True):
    pass


class TriggerCapture(msgspec.Struct, tag=True):
    pass


class DownloadPreview(msgspec.Struct, tag=True):
    capture_id: str


class DownloadFull(msgspec.Struct, tag=True):
    capture_id: str


Request = (
    Connect
    | Disconnect
    | Reconnect
    | GetStatus
    | TriggerAutofocus
    | TriggerCapture
    | DownloadPreview
    | DownloadFull
)


class Ok(msgspec.Struct, tag=True):
    pass


class StatusResult(msgspec.Struct, tag=True):
    connected: bool


class CaptureResult(msgspec.Struct, tag=True):
    capture_id: str


class ImageResult(msgspec.Struct, tag=True):
    kind: str  # ImageKind value ("preview" | "full")
    data: bytes
    width: int
    height: int


class NoPreview(msgspec.Struct, tag=True):
    """download_preview() result when the body has no PTP preview support."""


class ErrorResult(msgspec.Struct, tag=True):
    # "disconnected" maps back to CameraDisconnectedError client-side, any
    # other value maps to the base CameraError (protocol.py).
    error_type: str
    message: str


Response = Ok | StatusResult | CaptureResult | ImageResult | NoPreview | ErrorResult

_request_encoder = msgspec.msgpack.Encoder()
_request_decoder = msgspec.msgpack.Decoder(type=Request)
_response_encoder = msgspec.msgpack.Encoder()
_response_decoder = msgspec.msgpack.Decoder(type=Response)

_LENGTH_PREFIX_BYTES = 4
_MAX_FRAME_BYTES = 64 * 1024 * 1024  # generous headroom over an L-size JPEG (~4 MB)


class FrameTooLargeError(Exception):
    pass


def encode_request(msg: Request) -> bytes:
    return _frame(_request_encoder.encode(msg))


def decode_request(data: bytes) -> Request:
    return cast(Request, _request_decoder.decode(data))


def encode_response(msg: Response) -> bytes:
    return _frame(_response_encoder.encode(msg))


def decode_response(data: bytes) -> Response:
    return cast(Response, _response_decoder.decode(data))


def _frame(payload: bytes) -> bytes:
    return len(payload).to_bytes(_LENGTH_PREFIX_BYTES, "big") + payload


def read_frame_length(header: bytes) -> int:
    """Parse a 4-byte length prefix already read off the wire."""
    length = int.from_bytes(header, "big")
    if length > _MAX_FRAME_BYTES:
        raise FrameTooLargeError(f"frame length {length} exceeds max {_MAX_FRAME_BYTES}")
    return length
