"""MJPEG proxy: relays go2rtc's stream to the frontend without decode/re-encode.

go2rtc owns the capture device exclusively (IMPLEMENTATION_PLAN.md §0 — the
stick allows exactly one consumer). This module re-serves
http://127.0.0.1:1984/api/stream.mjpeg?src=photobooth as multipart/x-mixed-replace,
giving fan-out to kiosk + admin without touching /dev/video1 twice. See T-1.10.
"""

from __future__ import annotations


async def stream_proxy(source_url: str) -> None:
    raise NotImplementedError("T-1.10: go2rtc supervision + MJPEG proxy endpoint")
