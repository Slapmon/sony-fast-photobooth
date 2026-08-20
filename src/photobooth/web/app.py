"""FastAPI app entrypoint. Routers per surface (kiosk / gallery / admin /
debug) are added incrementally as their phases land — see
IMPLEMENTATION_PLAN.md §7-9. /health exists from commit one so `just dev`
and CI have something to point at.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="photobooth")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
