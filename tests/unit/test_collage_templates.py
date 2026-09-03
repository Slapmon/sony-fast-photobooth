"""Collage-mode template tests (IMPLEMENTATION_PLAN.md §8 T-2.5).

Proves the three example templates (2x2, 1+2, 3-strip) all load cleanly
through `load_template()` and that `render_variant()` produces correctly
sized, correctly encoded output for every declared variant
(print/web/thumb) -- 3 templates x 3 variants = 9 render calls.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from photobooth.config.event import EventConfig, load_event
from photobooth.pipeline.compositor import render_variant
from photobooth.pipeline.template import load_template

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
SOURCE_IMAGE = REPO_ROOT / "fixtures" / "shots" / "sample-01.jpg"

# (template filename, slot count, {variant: expected (width, height)})
TEMPLATE_CASES = [
    (
        "collage-2x2.yaml",
        4,
        {
            "print": (1795, 1205),
            "web": (2000, 1343),
            "thumb": (400, 269),
        },
    ),
    (
        "strip-1plus2.yaml",
        3,
        {
            "print": (1795, 1205),
            "web": (2000, 1343),
            "thumb": (400, 269),
        },
    ),
    (
        "strip-3strip.yaml",
        3,
        {
            "print": (600, 1800),
            "web": (667, 2000),
            "thumb": (133, 400),
        },
    ),
]


@pytest.fixture
def event() -> EventConfig:
    return load_event(REPO_ROOT / "events", "example-event")


@pytest.mark.parametrize("template_name,slot_count,expected_sizes", TEMPLATE_CASES)
def test_template_loads_cleanly(
    template_name: str, slot_count: int, expected_sizes: dict[str, tuple[int, int]]
) -> None:
    template = load_template(TEMPLATES_DIR / template_name)
    assert len(template.slots) == slot_count
    assert set(expected_sizes) <= set(template.variants)


@pytest.mark.parametrize(
    "template_name,slot_count,variant,expected_size",
    [
        (name, slots, variant, size)
        for name, slots, sizes in TEMPLATE_CASES
        for variant, size in sizes.items()
    ],
)
def test_render_variant_produces_correctly_sized_output(
    event: EventConfig,
    template_name: str,
    slot_count: int,
    variant: str,
    expected_size: tuple[int, int],
) -> None:
    sources = [SOURCE_IMAGE] * slot_count
    data = render_variant(TEMPLATES_DIR / template_name, sources, variant, event)

    assert isinstance(data, bytes)
    assert len(data) > 0

    image = Image.open(io.BytesIO(data))
    image.load()  # force full decode -> proves it's a valid, correctly-encoded JPEG
    assert image.format == "JPEG"
    assert image.size == expected_size
