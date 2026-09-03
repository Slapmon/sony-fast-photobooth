"""Tests for pipeline/pool.py: bounded-concurrency render worker pool
(IMPLEMENTATION_PLAN.md §8 T-2.8).

Two concerns, tested separately:
1. Correctness -- the pool must actually produce the right bytes/dimensions
   for each template+variant, not just "some bytes." Exercised with the
   real `render_variant()` pipeline (no mocking) against the real templates.
2. The concurrency bound -- submitting more concurrent render calls than
   `max_workers` must never let more than `max_workers` run at once.
   Exercised by monkeypatching the pool's `render_variant` reference with a
   fake that tracks concurrently-active calls under a lock, so this test
   doesn't depend on real render timing (which would be slow and flaky to
   race against).
"""

from __future__ import annotations

import asyncio
import io
import threading
from pathlib import Path

import pytest
from PIL import Image

from photobooth.config.event import EventConfig, load_event
from photobooth.pipeline import pool as pool_module
from photobooth.pipeline.pool import RenderPool

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
SOURCE_IMAGE = REPO_ROOT / "fixtures" / "shots" / "sample-01.jpg"


@pytest.fixture
def event() -> EventConfig:
    return load_event(REPO_ROOT / "events", "example-event")


def test_default_max_workers_is_bounded_and_never_unbounded() -> None:
    pool = RenderPool()
    try:
        assert 1 <= pool.max_workers <= 4
    finally:
        pool._executor.shutdown(wait=True)


async def test_render_pool_produces_correct_output_per_template_and_variant(
    event: EventConfig,
) -> None:
    pool = RenderPool(max_workers=2)
    try:
        jobs = [
            (TEMPLATES_DIR / "collage-2x2.yaml", [SOURCE_IMAGE] * 4, "print", (1795, 1205)),
            (TEMPLATES_DIR / "collage-2x2.yaml", [SOURCE_IMAGE] * 4, "web", (2000, 1343)),
            (TEMPLATES_DIR / "strip-1plus2.yaml", [SOURCE_IMAGE] * 3, "thumb", (400, 269)),
            (TEMPLATES_DIR / "strip-3strip.yaml", [SOURCE_IMAGE] * 3, "print", (600, 1800)),
        ]

        results = await asyncio.gather(
            *(
                pool.render(template, sources, variant, event)
                for template, sources, variant, _ in jobs
            )
        )

        for (_, _, _, expected_size), data in zip(jobs, results, strict=True):
            assert isinstance(data, bytes)
            image = Image.open(io.BytesIO(data))
            image.load()
            assert image.size == expected_size
    finally:
        await pool.aclose()


async def test_render_pool_never_exceeds_configured_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    max_workers = 3
    submitted = 10

    active = 0
    peak = 0
    lock = threading.Lock()

    def fake_render_variant(
        template_path: Path, source_images: list[Path], variant: str, event: EventConfig
    ) -> bytes:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            # Small blocking sleep so overlapping calls actually overlap in
            # wall-clock time, giving the concurrency bound something real
            # to violate if it were broken.
            import time

            time.sleep(0.05)
        finally:
            with lock:
                active -= 1
        return f"{template_path}:{variant}".encode()

    monkeypatch.setattr(pool_module, "render_variant", fake_render_variant)

    dummy_event = EventConfig(id="e", title="T", template="t")
    pool = RenderPool(max_workers=max_workers)
    try:
        results = await asyncio.gather(
            *(
                pool.render(Path("dummy.yaml"), [], "print", dummy_event)
                for _ in range(submitted)
            )
        )
    finally:
        await pool.aclose()

    assert len(results) == submitted
    assert all(r == b"dummy.yaml:print" for r in results)
    assert peak <= max_workers, (
        f"observed peak concurrency {peak} exceeded max_workers={max_workers}"
    )
    # Also prove the bound was actually exercised (not trivially satisfied
    # because everything ran sequentially) -- with 10 jobs at 50ms each and
    # 3 workers, at least 2 must have overlapped at some point.
    assert peak >= 2
