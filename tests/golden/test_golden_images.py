"""Golden-image regression tests for the compositor's `print` variant
(IMPLEMENTATION_PLAN.md §8 T-2.4).

For each of the three example templates, re-renders the `print` variant
against the same fixed inputs (fixture source image + example-event) used to
generate the checked-in reference in this directory, and compares the two
with a perceptual/pixel-difference tolerance -- NOT byte-exact. JPEG
re-encoding and libvips version drift across machines can nudge individual
pixel values by a point or two without any visible or meaningful change, so
a byte-for-byte or exact-pixel comparison would flake on CI/dev-machine
differences that have nothing to do with a real regression.

Metric: Pillow's `ImageChops.difference` (this project already depends on
Pillow; no need to pull in numpy/scikit-image for what is a regression
guard, not a rigorous visual QA tool) reduced to a per-channel mean via
`ImageStat.Stat` -- effectively a mean absolute error (MAE) over all pixels
and channels.

Tolerance: mean per-channel difference < 2.0 (out of 255). Reasoning:
- On this machine, re-rendering the same template/inputs twice back-to-back
  produces a *zero* pixel difference (pyvips' JPEG decode/encode and the
  compositor's math are fully deterministic here) -- so 2.0 is already many
  times looser than the observed same-machine noise floor, giving headroom
  for font-rasterization/antialiasing/libvips-version differences on a
  different machine without the test flaking.
- It is still tight enough to catch a real regression: breaking the crop
  math (see `test_broken_crop_produces_large_diff` below) shifts or resizes
  entire slot regions, which drives the mean difference into the tens, not
  fractions of a point -- nowhere near this threshold.

Also includes a test that intentionally breaks something (wrong slot count
via a hand-built template with an empty/mis-cropped region) to prove the
diff mechanism actually has teeth -- a golden test that would pass even
against a blank white image isn't testing anything.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml
from PIL import Image, ImageChops, ImageStat

from photobooth.config.event import EventConfig, load_event
from photobooth.pipeline.compositor import render_variant

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates"
GOLDEN_DIR = Path(__file__).resolve().parent
SOURCE_IMAGE = REPO_ROOT / "fixtures" / "shots" / "sample-01.jpg"

# See module docstring for how this number was chosen.
MEAN_DIFF_TOLERANCE = 2.0

TEMPLATE_CASES = [
    ("collage-2x2.yaml", 4),
    ("strip-1plus2.yaml", 3),
    ("strip-3strip.yaml", 3),
]


@pytest.fixture
def event() -> EventConfig:
    return load_event(REPO_ROOT / "events", "example-event")


def _mean_diff(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute per-channel pixel difference between two same-size RGB
    images (0..255 scale). Higher = more visually different."""
    a_rgb = a.convert("RGB")
    b_rgb = b.convert("RGB")
    assert a_rgb.size == b_rgb.size, f"size mismatch: {a_rgb.size} vs {b_rgb.size}"
    diff = ImageChops.difference(a_rgb, b_rgb)
    stat = ImageStat.Stat(diff)
    return sum(stat.mean) / len(stat.mean)


@pytest.mark.parametrize("template_name,slot_count", TEMPLATE_CASES)
def test_print_variant_matches_golden(
    event: EventConfig, template_name: str, slot_count: int
) -> None:
    golden_path = GOLDEN_DIR / f"{Path(template_name).stem}-print.jpg"
    assert golden_path.is_file(), (
        f"missing golden reference {golden_path}; generate it with "
        "tools/gen_golden_images.py"
    )

    sources = [SOURCE_IMAGE] * slot_count
    rendered = render_variant(TEMPLATES_DIR / template_name, sources, "print", event)

    rendered_image = Image.open(io.BytesIO(rendered))
    golden_image = Image.open(golden_path)

    mean_diff = _mean_diff(rendered_image, golden_image)
    assert mean_diff < MEAN_DIFF_TOLERANCE, (
        f"{template_name}: rendered print variant differs from golden by "
        f"mean {mean_diff:.3f} (tolerance {MEAN_DIFF_TOLERANCE}) -- looks "
        "like a real regression, not encoder/version noise"
    )


def test_broken_crop_produces_large_diff(tmp_path: Path, event: EventConfig) -> None:
    """Proves the diff mechanism has teeth: a template whose slot geometry
    is deliberately wrong (badly off-center crop covering a different part
    of the canvas) must NOT pass as "close enough" against the real
    collage-2x2 golden image. If this test failed to catch it, the golden
    test above would be worthless -- it would pass even against a broken
    compositor."""
    (tmp_path / "font.ttf").write_bytes(b"not-a-real-font")
    # Same canvas/slot *count* as collage-2x2 (so `render_variant` accepts 4
    # sources and produces the same output dimensions -- required so the
    # size-mismatch assert in `_mean_diff` doesn't short-circuit the
    # comparison before the pixel diff even runs), but each slot is shrunk
    # to a corner instead of filling its quadrant -- most of the canvas
    # that should show photo content is left as bare background instead.
    data = {
        "name": "Broken crop",
        "canvas": {"width_mm": 152, "height_mm": 102, "dpi": 300, "background": "#ffffff"},
        "slots": [
            {"x": 40, "y": 40, "w": 100, "h": 100, "fit": "cover"},
            {"x": 915, "y": 40, "w": 100, "h": 100, "fit": "cover"},
            {"x": 40, "y": 635, "w": 100, "h": 100, "fit": "cover"},
            {"x": 915, "y": 635, "w": 100, "h": 100, "fit": "cover"},
        ],
        "overlays": [],
        "variants": {
            "print": {"dpi": 300, "format": "jpeg", "quality": 95},
            "web": {"long_edge": 2000, "format": "jpeg", "quality": 85},
            "thumb": {"long_edge": 400, "format": "jpeg", "quality": 75},
        },
    }
    template_path = tmp_path / "broken.yaml"
    template_path.write_text(yaml.safe_dump(data))

    golden_path = GOLDEN_DIR / "collage-2x2-print.jpg"
    broken = render_variant(template_path, [SOURCE_IMAGE] * 4, "print", event)

    broken_image = Image.open(io.BytesIO(broken))
    golden_image = Image.open(golden_path)

    mean_diff = _mean_diff(broken_image, golden_image)
    assert mean_diff >= MEAN_DIFF_TOLERANCE, (
        "expected the deliberately-broken crop geometry to differ "
        f"substantially from the golden image, but mean diff was only "
        f"{mean_diff:.3f} -- the diff mechanism isn't actually sensitive "
        "enough to catch real regressions"
    )
    # Not just "different" -- clearly, unmistakably different: most of the
    # canvas that should be photo content is now bare white background.
    assert mean_diff > 10.0


def test_quality_change_materially_changes_output_size(
    tmp_path: Path, event: EventConfig
) -> None:
    """Second "does the mechanism have teeth" check, at the byte level
    rather than the pixel level: rendering the same template at a much
    lower JPEG quality must produce a meaningfully smaller file. If quality
    settings were silently ignored, this would fail."""
    sources = [SOURCE_IMAGE] * 4
    high_q = render_variant(TEMPLATES_DIR / "collage-2x2.yaml", sources, "print", event)

    # Build a low-quality variant of the same template in an isolated tmp
    # dir (references assets/ relatively, so copy those alongside it).
    import shutil

    template_data = yaml.safe_load((TEMPLATES_DIR / "collage-2x2.yaml").read_text())
    template_data["variants"]["print"]["quality"] = 20
    shutil.copytree(TEMPLATES_DIR / "assets", tmp_path / "assets")
    low_q_path = tmp_path / "low_quality.yaml"
    low_q_path.write_text(yaml.safe_dump(template_data))
    low_q = render_variant(low_q_path, sources, "print", event)

    assert len(low_q) < len(high_q) * 0.7, (
        f"expected quality=20 output ({len(low_q)} bytes) to be materially "
        f"smaller than quality=95 output ({len(high_q)} bytes)"
    )
