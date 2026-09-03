"""Unit tests for pipeline/compositor.py: slot fit modes, overlays, text
placeholder resolution, and per-variant encoding
(IMPLEMENTATION_PLAN.md §8 T-2.2/T-2.3)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import pyvips
import yaml
from PIL import Image

from photobooth.config.event import EventConfig, PlaceholderResolutionError, load_event
from photobooth.pipeline.compositor import _composite, render_variant
from photobooth.pipeline.template import load_template

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "templates" / "collage-2x2.yaml"
SOURCE_IMAGE = REPO_ROOT / "fixtures" / "shots" / "sample-01.jpg"


@pytest.fixture
def event() -> EventConfig:
    return load_event(REPO_ROOT / "events", "example-event")


@pytest.fixture
def four_sources() -> list[Path]:
    return [SOURCE_IMAGE] * 4


def test_slot_count_mismatch_raises(event: EventConfig) -> None:
    with pytest.raises(ValueError, match=r"expected 4 source image"):
        render_variant(TEMPLATE_PATH, [SOURCE_IMAGE] * 3, "print", event)


def _make_template(tmp_path: Path, slot: dict, *, background: str = "#ffffff") -> Path:
    (tmp_path / "font.ttf").write_bytes(b"not-a-real-font")
    data = {
        "name": "Fit test",
        "canvas": {"width_mm": 50.8, "height_mm": 50.8, "dpi": 96, "background": background},
        "slots": [slot],
        "overlays": [],
        "variants": {
            "print": {"dpi": 96, "format": "png", "quality": 95},
            "web": {"long_edge": 100, "format": "png", "quality": 85},
            "thumb": {"long_edge": 40, "format": "png", "quality": 75},
        },
    }
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("fit", ["cover", "contain", "fill"])
def test_fit_modes_produce_slot_sized_output(tmp_path: Path, event: EventConfig, fit: str) -> None:
    # canvas is 50.8mm @ 96dpi -> 192x192px; slot covers the whole canvas.
    slot = {"x": 0, "y": 0, "w": 192, "h": 192, "fit": fit}
    template_path = _make_template(tmp_path, slot)
    data = render_variant(template_path, [SOURCE_IMAGE], "print", event)
    image = Image.open(io.BytesIO(data))
    assert image.size == (192, 192)


def test_contain_leaves_background_visible_in_letterbox(tmp_path: Path, event: EventConfig) -> None:
    # A very wide slot with a square source image -> contain leaves left/right
    # (for a portrait slot) letterbox showing the canvas background color.
    slot = {"x": 0, "y": 0, "w": 192, "h": 96, "fit": "contain"}
    template_path = _make_template(tmp_path, slot, background="#112233")
    data = render_variant(template_path, [SOURCE_IMAGE], "print", event)
    image = Image.open(io.BytesIO(data)).convert("RGB")
    # top-left corner should be untouched background, since the source is
    # portrait (taller than wide relatively) and contain will letterbox
    # top/bottom or left/right depending on aspect ratio mismatch.
    corner = image.getpixel((0, 0))
    assert corner == (0x11, 0x22, 0x33)


def test_image_overlay_alpha_composites_correctly(tmp_path: Path, event: EventConfig) -> None:
    # Build a template with a fully-opaque red slot fill and a semi-
    # transparent blue image overlay covering the same area; spot-check the
    # blended pixel.
    (tmp_path / "font.ttf").write_bytes(b"not-a-real-font")
    overlay_png = tmp_path / "overlay.png"
    # 4x4 solid blue, alpha=128 (~50%).
    blue_alpha = pyvips.Image.black(4, 4, bands=3) + [0, 0, 255]
    blue_alpha = blue_alpha.cast("uchar").bandjoin(pyvips.Image.black(4, 4, bands=1) + 128)
    blue_alpha = blue_alpha.copy(interpretation="srgb")
    blue_alpha.pngsave(str(overlay_png))

    data_yaml = {
        "name": "Alpha test",
        "canvas": {"width_mm": 25.4, "height_mm": 25.4, "dpi": 96, "background": "#ff0000"},
        "slots": [{"x": 0, "y": 0, "w": 1, "h": 1, "fit": "fill"}],
        "overlays": [
            {"type": "image", "src": "overlay.png", "x": 0, "y": 0, "w": 4, "h": 4},
        ],
        "variants": {
            "print": {"dpi": 96, "format": "png", "quality": 95},
            "web": {"long_edge": 20, "format": "png", "quality": 85},
            "thumb": {"long_edge": 8, "format": "png", "quality": 75},
        },
    }
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(data_yaml))

    data = render_variant(path, [SOURCE_IMAGE], "print", event)
    image = Image.open(io.BytesIO(data)).convert("RGB")
    r, g, b = image.getpixel((1, 1))
    # Red background (255,0,0) with ~50% blue (0,0,255) over it -> roughly
    # (128, 0, 128), allow tolerance for rounding/gamma.
    assert 100 <= r <= 160
    assert g == 0
    assert 100 <= b <= 160


def test_text_overlay_unresolved_placeholder_raises(tmp_path: Path) -> None:
    (tmp_path / "font.ttf").write_bytes(b"not-a-real-font")
    data_yaml = {
        "name": "Placeholder test",
        "canvas": {"width_mm": 25.4, "height_mm": 25.4, "dpi": 96, "background": "#ffffff"},
        "slots": [{"x": 0, "y": 0, "w": 1, "h": 1, "fit": "fill"}],
        "overlays": [
            {
                "type": "text",
                "content": "{event.does_not_exist}",
                "font": "font.ttf",
                "size": 20,
                "color": "#000000",
                "anchor": "center",
            },
        ],
        "variants": {
            "print": {"dpi": 96, "format": "png", "quality": 95},
            "web": {"long_edge": 20, "format": "png", "quality": 85},
            "thumb": {"long_edge": 8, "format": "png", "quality": 75},
        },
    }
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(data_yaml))

    event = EventConfig(id="e", title="T", template="template.yaml", vars={})
    with pytest.raises(PlaceholderResolutionError):
        render_variant(path, [SOURCE_IMAGE], "print", event)


@pytest.mark.parametrize(
    ("variant", "expected_size"),
    [
        ("print", (1795, 1205)),
        ("web", (2000, 1343)),
        ("thumb", (400, 269)),
    ],
)
def test_variant_dimensions(
    event: EventConfig, four_sources: list[Path], variant: str, expected_size: tuple[int, int]
) -> None:
    data = render_variant(TEMPLATE_PATH, four_sources, variant, event)
    assert isinstance(data, bytes)
    image = Image.open(io.BytesIO(data))
    assert image.size == expected_size


def test_composite_helper_is_reusable_across_variants(
    event: EventConfig, four_sources: list[Path]
) -> None:
    template = load_template(TEMPLATE_PATH)
    canvas = _composite(template, TEMPLATE_PATH.parent, four_sources, event)
    assert canvas.width == template.canvas.width_px
    assert canvas.height == template.canvas.height_px
