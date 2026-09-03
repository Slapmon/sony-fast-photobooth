"""Unit tests for pipeline/template.py: schema validation + load_template's
load-time cross-checks (IMPLEMENTATION_PLAN.md §8 T-2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from photobooth.pipeline.template import TemplateConfig, TemplateValidationError, load_template

VALID_TEMPLATE: dict = {
    "name": "Classic 2x2",
    "canvas": {"width_mm": 152, "height_mm": 102, "dpi": 300, "background": "#ffffff"},
    "slots": [
        {"x": 40, "y": 40, "w": 840, "h": 560, "fit": "cover"},
        {"x": 915, "y": 40, "w": 840, "h": 560, "fit": "cover"},
    ],
    "overlays": [
        {"type": "image", "src": "frame.png", "x": 0, "y": 0, "w": 1795, "h": 1205},
        {
            "type": "text",
            "content": "{event.couple} · {event.date}",
            "font": "font.ttf",
            "size": 64,
            "color": "#3a3a3a",
            "anchor": "bottom-center",
            "y_offset": -30,
        },
    ],
    "variants": {
        "print": {"dpi": 300, "format": "jpeg", "quality": 95},
        "web": {"long_edge": 2000, "format": "jpeg", "quality": 85},
        "thumb": {"long_edge": 400, "format": "jpeg", "quality": 75},
    },
}


def _write_template(tmp_path: Path, data: dict, *, with_assets: bool = True) -> Path:
    if with_assets:
        (tmp_path / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "font.ttf").write_bytes(b"placeholder-font-bytes")
    path = tmp_path / "template.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_load_valid_template(tmp_path: Path) -> None:
    path = _write_template(tmp_path, VALID_TEMPLATE)
    template = load_template(path)
    assert isinstance(template, TemplateConfig)
    assert template.name == "Classic 2x2"
    assert len(template.slots) == 2
    assert set(template.variants) == {"print", "web", "thumb"}


def test_real_shipped_template_loads(tmp_path: Path) -> None:
    """The template shipped in templates/collage-2x2.yaml must actually load
    — this is the regression guard for T-2.1's "don't leave the example
    unloadable" requirement."""
    repo_root = Path(__file__).resolve().parents[2]
    template = load_template(repo_root / "templates" / "collage-2x2.yaml")
    assert template.name == "Classic 2x2"
    assert len(template.slots) == 4


def test_missing_overlay_image_file_raises(tmp_path: Path) -> None:
    data = {**VALID_TEMPLATE}
    path = _write_template(tmp_path, data, with_assets=False)
    (tmp_path / "font.ttf").write_bytes(b"x")
    # frame.png intentionally not created
    with pytest.raises(TemplateValidationError, match="frame.png"):
        load_template(path)


def test_missing_font_file_raises(tmp_path: Path) -> None:
    data = {**VALID_TEMPLATE}
    path = _write_template(tmp_path, data, with_assets=False)
    (tmp_path / "frame.png").write_bytes(b"x")
    # font.ttf intentionally not created
    with pytest.raises(TemplateValidationError, match="font.ttf"):
        load_template(path)


def test_slot_out_of_bounds_x_raises(tmp_path: Path) -> None:
    data = {
        **VALID_TEMPLATE,
        "slots": [{"x": 1000, "y": 0, "w": 1000, "h": 100, "fit": "cover"}],
        "overlays": [],
    }
    path = _write_template(tmp_path, data)
    with pytest.raises(TemplateValidationError, match="exceeds canvas bounds"):
        load_template(path)


def test_slot_out_of_bounds_y_raises(tmp_path: Path) -> None:
    data = {
        **VALID_TEMPLATE,
        "slots": [{"x": 0, "y": 1100, "w": 100, "h": 200, "fit": "cover"}],
        "overlays": [],
    }
    path = _write_template(tmp_path, data)
    with pytest.raises(TemplateValidationError, match="exceeds canvas bounds"):
        load_template(path)


def test_missing_variant_key_raises(tmp_path: Path) -> None:
    data = {
        **VALID_TEMPLATE,
        "overlays": [],
        "variants": {
            "print": {"dpi": 300, "format": "jpeg", "quality": 95},
            "web": {"long_edge": 2000, "format": "jpeg", "quality": 85},
            # "thumb" missing
        },
    }
    path = _write_template(tmp_path, data)
    with pytest.raises(TemplateValidationError, match="thumb"):
        load_template(path)


def test_empty_slots_rejected_by_pydantic(tmp_path: Path) -> None:
    data = {**VALID_TEMPLATE, "slots": [], "overlays": []}
    path = _write_template(tmp_path, data)
    with pytest.raises(ValidationError):
        load_template(path)


def test_invalid_fit_literal_rejected(tmp_path: Path) -> None:
    data = {
        **VALID_TEMPLATE,
        "slots": [{"x": 0, "y": 0, "w": 100, "h": 100, "fit": "stretch"}],
        "overlays": [],
    }
    path = _write_template(tmp_path, data)
    with pytest.raises(ValidationError):
        load_template(path)


def test_invalid_anchor_literal_rejected(tmp_path: Path) -> None:
    data = {**VALID_TEMPLATE, "overlays": [{**VALID_TEMPLATE["overlays"][1], "anchor": "middle"}]}
    path = _write_template(tmp_path, data)
    with pytest.raises(ValidationError):
        load_template(path)


def test_canvas_px_conversion() -> None:
    template = TemplateConfig.model_validate(VALID_TEMPLATE)
    # 152mm / 25.4 * 300dpi = 1795.27... -> rounds to 1795
    assert template.canvas.width_px == 1795
    assert template.canvas.height_px == 1205
