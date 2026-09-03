"""Bounded-concurrency render worker pool (IMPLEMENTATION_PLAN.md §8 T-2.8).

Wraps `concurrent.futures.ThreadPoolExecutor` around
`pipeline.compositor.render_variant()`. Threads, not processes: pyvips'
heavy operations (JPEG decode, resize, composite, encode) run in C and
release the GIL, so a thread pool gets real parallelism without the
pickling/IPC overhead multiprocessing would add for image bytes and
`EventConfig`/`Path` arguments.

Bounded, not unbounded: letting N simultaneous collage renders each spin up
their own thread would have them all fight every CPU core for cycles and
starve the rest of the app (the asyncio event loop, camera IPC, WS pushes).
`RenderPool` caps concurrency at `min(4, os.cpu_count() or 2)` by default --
enough to overlap I/O-bound render stages without oversubscribing a modest
box (a Pi 4 has 4 cores total).

`render()` is async and never blocks the event loop: it hands the (blocking,
CPU-heavy) `render_variant()` call to the executor via
`loop.run_in_executor()` and awaits the resulting future.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import TracebackType

from photobooth.config.event import EventConfig
from photobooth.pipeline.compositor import render_variant

# Never unbounded: cap at 4 even on a many-core dev machine, since the Pi
# target has 4 cores and we want dev-mode concurrency limits to mean
# something close to what they'll mean in production.
_DEFAULT_MAX_WORKERS = min(4, os.cpu_count() or 2)


class RenderPool:
    """Bounded thread pool for rendering template variants.

    Sized via `max_workers` (defaults to `min(4, os.cpu_count() or 2)`).
    Call `aclose()` (async callers) or `close()` (sync callers) on app
    shutdown to release the underlying threads -- matches the two shutdown
    conventions already used in this codebase: `preview/proxy.py`'s
    `PreviewProxy.aclose()` and `camera/client.py`'s
    `CameraWorkerClient.close()`. Also usable as an async context manager
    (`async with RenderPool() as pool: ...`), which closes it on exit.
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self._max_workers = _DEFAULT_MAX_WORKERS if max_workers is None else max_workers
        if self._max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {self._max_workers}")
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="render-pool"
        )

    @property
    def max_workers(self) -> int:
        return self._max_workers

    async def render(
        self,
        template_path: Path,
        source_images: list[Path],
        variant: str,
        event: EventConfig,
    ) -> bytes:
        """Render one template variant on the pool without blocking the
        event loop. Delegates to `render_variant()` as a black box -- this
        pool only adds bounded-concurrency scheduling around it, it does not
        reimplement or alter compositing behavior."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor, render_variant, template_path, source_images, variant, event
        )

    def close(self, wait: bool = True) -> None:
        """Synchronously shut the pool down, releasing its threads. Safe to
        call more than once. For sync callers (e.g. app shutdown code that
        isn't itself a coroutine) -- async callers should prefer `aclose()`
        so the wait for in-flight renders doesn't block the event loop."""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    async def aclose(self) -> None:
        """Shut the pool down cleanly, waiting for any in-flight renders to
        finish. Run off the event loop thread (`ThreadPoolExecutor.shutdown`
        blocks until workers drain) so callers awaiting this don't stall
        other event-loop work in the meantime."""
        await asyncio.to_thread(self._executor.shutdown, wait=True)

    async def __aenter__(self) -> RenderPool:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()
