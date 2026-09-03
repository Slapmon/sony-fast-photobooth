"""Template YAML schema: pydantic models + load-time validation.

A template describes one printable layout: canvas size/dpi, image slots
(where captured photos go), overlays (frame art, text with placeholders),
and output variants (print/web/thumb). See photobooth-plan.md §8 for the
schema rationale and IMPLEMENTATION_PLAN.md §8 (T-2.1).

Validation happens in two layers:
1. pydantic field/type validation (shapes, literals, required keys).
2. `load_template()`'s own checks, which need the *file system* and *other
   fields* to evaluate (slot bounds vs. canvas size, referenced asset files
   existing, variants covering print/web/thumb) — this is the "catch a
   broken font path in the workshop, not at 8pm on a Saturday" requirement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

REQUIRED_VARIANTS = ("print", "web", "thumb")


class TemplateValidationError(Exception):
    """Raised for template problems pydantic's type checking can't catch:
    out-of-bounds slots, missing asset files, missing variant keys."""


class Canvas(BaseModel):
    width_mm: float
    height_mm: float
    dpi: int = Field(gt=0)
    background: str

    @property
    def width_px(self) -> int:
        return round(self.width_mm / 25.4 * self.dpi)

    @property
    def height_px(self) -> int:
        return round(self.height_mm / 25.4 * self.dpi)


class Slot(BaseModel):
    x: int
    y: int
    w: int
    h: int
    fit: Literal["cover", "contain", "fill"]


class ImageOverlay(BaseModel):
    type: Literal["image"] = "image"
    src: str
    x: int
    y: int
    w: int
    h: int


class TextOverlay(BaseModel):
    type: Literal["text"] = "text"
    content: str
    font: str
    size: int
    color: str
    anchor: Literal[
        "top-left",
        "top-center",
        "top-right",
        "center",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ]
    x_offset: int = 0
    y_offset: int = 0


Overlay = Annotated[ImageOverlay | TextOverlay, Field(discriminator="type")]


class PrintVariantSpec(BaseModel):
    dpi: int
    format: Literal["jpeg", "png"] = "jpeg"
    quality: int = 95


class ScaledVariantSpec(BaseModel):
    long_edge: int
    format: Literal["jpeg", "png"] = "jpeg"
    quality: int = 95


VariantSpec = PrintVariantSpec | ScaledVariantSpec


class TemplateConfig(BaseModel):
    name: str
    canvas: Canvas
    slots: list[Slot] = Field(min_length=1)
    overlays: list[Overlay] = []
    # NOTE: presence of "print"/"web"/"thumb" keys is intentionally *not*
    # enforced here at the pydantic level — it's checked in load_template()
    # below as a TemplateValidationError, per T-2.1's "validate at load
    # time, not just at type level" requirement.
    variants: dict[str, VariantSpec]


def load_template(path: Path) -> TemplateConfig:
    """Parse, validate, and cross-check a template YAML file.

    Raises `TemplateValidationError` for anything pydantic's type-level
    validation can't catch: slots that overflow the canvas, overlay
    src/font paths that don't exist on disk, or (belt-and-braces, already
    enforced by the model) a variants dict missing print/web/thumb.
    """
    data = yaml.safe_load(path.read_text())
    template = TemplateConfig.model_validate(data)

    canvas_w = template.canvas.width_px
    canvas_h = template.canvas.height_px
    for i, slot in enumerate(template.slots):
        if slot.x + slot.w > canvas_w or slot.y + slot.h > canvas_h:
            raise TemplateValidationError(
                f"{path}: slot[{i}] ({slot.x},{slot.y},{slot.w}x{slot.h}) exceeds canvas "
                f"bounds ({canvas_w}x{canvas_h}px at {template.canvas.dpi}dpi)"
            )

    template_dir = path.parent
    for i, overlay in enumerate(template.overlays):
        rel = overlay.src if isinstance(overlay, ImageOverlay) else overlay.font
        field_name = "src" if isinstance(overlay, ImageOverlay) else "font"
        resolved = template_dir / rel
        if not resolved.is_file():
            raise TemplateValidationError(
                f"{path}: overlay[{i}].{field_name} = {rel!r} does not exist "
                f"(resolved to {resolved})"
            )

    missing = [name for name in REQUIRED_VARIANTS if name not in template.variants]
    if missing:
        raise TemplateValidationError(f"{path}: variants missing required key(s): {missing}")

    return template
