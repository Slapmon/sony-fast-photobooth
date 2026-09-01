"""Preview-facing routes: relays the go2rtc MJPEG stream to browser clients.
See preview/proxy.py for why this exists as a shared proxy rather than each
client hitting go2rtc directly.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from photobooth.preview.proxy import PreviewProxy

router = APIRouter()


def get_preview_proxy(request: Request) -> PreviewProxy:
    return request.app.state.preview_proxy


PreviewProxyDep = Annotated[PreviewProxy, Depends(get_preview_proxy)]


@router.get("/preview/stream")
async def preview_stream(preview_proxy: PreviewProxyDep) -> StreamingResponse:
    try:
        content_type, body = await preview_proxy.open_stream()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"go2rtc unreachable: {exc}") from exc
    return StreamingResponse(body, media_type=content_type)
