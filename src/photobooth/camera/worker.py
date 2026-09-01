"""Entrypoint for the dedicated camera-worker process.

Owns exactly one CameraBackend instance for the process lifetime and serves
it over TCP loopback to camera/client.py. Runs single-threaded: libgphoto2
calls are blocking and must never share a thread with anything else
(photobooth-plan.md §3.2). See IMPLEMENTATION_PLAN.md T-1.6.

TCP loopback rather than a UNIX domain socket: this is developed and tested
on Windows, where `socket.AF_UNIX` isn't available in this Python build.
127.0.0.1 works identically on the Pi at deploy time, so there's no
platform branching — one code path for dev and production.

Connecting the backend is not done at process startup. The worker just
holds the constructed-but-unconnected backend; the client decides when to
send Connect (e.g. after a preflight check), keeping camera-open lifecycle
under app control rather than implicit at process boot.
"""

from __future__ import annotations

import argparse
import signal
import socket
import threading
from pathlib import Path

from photobooth.camera import messages
from photobooth.camera.gphoto import GphotoBackend
from photobooth.camera.mock import MockBackend
from photobooth.camera.protocol import (
    CameraBackend,
    CameraDisconnectedError,
    CameraError,
)

_RECV_CHUNK_BYTES = 65536


def _read_exact(sock: socket.socket, size: int) -> bytes | None:
    """Read exactly `size` bytes, or None if the peer closed before that."""
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(min(remaining, _RECV_CHUNK_BYTES))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _handle_request(backend: CameraBackend, request: messages.Request) -> messages.Response:
    try:
        match request:
            case messages.Connect():
                backend.connect()
                return messages.Ok()
            case messages.Disconnect():
                backend.disconnect()
                return messages.Ok()
            case messages.Reconnect():
                backend.reconnect()
                return messages.Ok()
            case messages.GetStatus():
                return messages.StatusResult(connected=backend.is_connected())
            case messages.TriggerAutofocus():
                backend.trigger_autofocus()
                return messages.Ok()
            case messages.TriggerCapture():
                capture_id = backend.trigger_capture()
                return messages.CaptureResult(capture_id=capture_id)
            case messages.DownloadPreview():
                preview = backend.download_preview(request.capture_id)
                if preview is None:
                    return messages.NoPreview()
                return messages.ImageResult(
                    kind=preview.kind.value,
                    data=preview.data,
                    width=preview.width,
                    height=preview.height,
                )
            case messages.DownloadFull():
                full = backend.download_full(request.capture_id)
                return messages.ImageResult(
                    kind=full.kind.value,
                    data=full.data,
                    width=full.width,
                    height=full.height,
                )
    except CameraDisconnectedError as exc:
        return messages.ErrorResult(error_type="disconnected", message=str(exc))
    except CameraError as exc:
        return messages.ErrorResult(error_type="error", message=str(exc))


def _serve_connection(conn: socket.socket, backend: CameraBackend) -> None:
    with conn:
        while True:
            header = _read_exact(conn, 4)
            if header is None:
                return
            length = messages.read_frame_length(header)
            payload = _read_exact(conn, length)
            if payload is None:
                return
            request = messages.decode_request(payload)
            response = _handle_request(backend, request)
            conn.sendall(messages.encode_response(response))


def run_worker(
    backend: CameraBackend,
    host: str,
    port: int,
    ready_event: threading.Event | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Bind, listen, and serve one client connection at a time until stopped.

    Single-threaded by design: the backend owns one blocking camera handle
    for its whole lifetime, so only one command may be in flight at once
    (protocol.py). `ready_event` is set right after the listening socket is
    bound, so tests can start this in a background thread and know exactly
    when it's safe to connect instead of guessing with a sleep. `stop_event`
    lets a caller (or the SIGTERM/SIGINT handler in main()) ask the accept
    loop to exit; without one, only signals stop it.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host, port))
        listener.listen(1)
        listener.settimeout(0.5)

        if ready_event is not None:
            ready_event.set()

        while stop_event is None or not stop_event.is_set():
            try:
                conn, _addr = listener.accept()
            except TimeoutError:
                continue
            _serve_connection(conn, backend)
    finally:
        listener.close()
        if backend.is_connected():
            backend.disconnect()


def _build_backend(args: argparse.Namespace) -> CameraBackend:
    if args.backend == "mock":
        return MockBackend(
            fixtures_dir=Path(args.fixtures_dir),
            trigger_delay_ms=args.trigger_delay_ms,
            thumb_latency_ms=args.thumb_latency_ms,
            full_download_mbps=args.full_download_mbps,
            disconnect_every_n=args.disconnect_every_n,
            download_timeout_pct=args.download_timeout_pct,
            slow_download_pct=args.slow_download_pct,
        )
    return GphotoBackend(jpeg_size=args.jpeg_size)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Camera worker process")
    parser.add_argument("--backend", choices=["mock", "gphoto"], default="mock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--fixtures-dir", default="fixtures/shots")
    parser.add_argument("--trigger-delay-ms", type=int, default=250)
    parser.add_argument("--thumb-latency-ms", type=int, default=150)
    parser.add_argument("--full-download-mbps", type=float, default=40.0)
    parser.add_argument("--jpeg-size", choices=["S", "M", "L"], default="S")
    # Fault injection (IMPLEMENTATION_PLAN.md §4.4), mock-backend only.
    parser.add_argument("--disconnect-every-n", type=int, default=None)
    parser.add_argument("--download-timeout-pct", type=float, default=0.0)
    parser.add_argument("--slow-download-pct", type=float, default=0.0)
    return parser.parse_args(argv)


def main() -> None:
    args = _parse_args()
    backend = _build_backend(args)
    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    run_worker(backend, args.host, args.port, stop_event=stop_event)


if __name__ == "__main__":
    main()
