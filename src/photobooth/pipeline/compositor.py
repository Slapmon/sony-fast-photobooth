"""Template-driven pyvips compositor: slots, fit modes, overlays, text.

Renders print / web / thumb variants from one template.yaml + source images.
See photobooth-plan.md §8 for the template schema and
IMPLEMENTATION_PLAN.md §8 (T-2.1..T-2.8).

Rendering pipeline (see `_composite` / `render_variant`):
1. Blank canvas at the template's native (print-dpi) pixel size, filled with
   `canvas.background`.
2. Each slot's source image is resized per its `fit` mode and composited
   onto the canvas.
3. Each overlay (image or text), in template order, is composited on top.
4. The finished canvas is encoded per the requested variant's spec — for
   `print` at native size, for `web`/`thumb` after a long-edge resize.

Font handling: `templates/collage-2x2.yaml` currently points its text
overlay's `font` field at `templates/assets/PLACEHOLDER_FONT.ttf`, which is
not a real font file (see T-2.1's note) — it exists only so
`load_template()`'s file-existence check passes. We validate the font file's
magic bytes before trusting it; when it doesn't look like a real
TTF/OTF/TTC, we log a warning and fall back to a generic system font family
("sans", resolved via fontconfig/pango) rather than failing the render.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

import pyvips
import structlog

from photobooth.config.event import EventConfig, resolve_placeholders
from photobooth.pipeline.template import (
    Canvas,
    ImageOverlay,
    LogoOverlay,
    PrintVariantSpec,
    Slot,
    TemplateConfig,
    TextOverlay,
    load_template,
)

logger = structlog.get_logger(__name__)

# Fallback font family used whenever a template's overlay `font` file isn't
# a real, loadable font. Resolved via fontconfig/pango at render time, so it
# just needs to be something virtually every system has.
_FALLBACK_FONT_FAMILY = "sans"

# Magic byte prefixes for font formats pyvips/pango (via freetype) can load.
_FONT_MAGIC_PREFIXES = (
    b"\x00\x01\x00\x00",  # TrueType (sfnt version 1.0)
    b"OTTO",  # OpenType (CFF outlines)
    b"true",  # older TrueType (Mac)
    b"ttcf",  # TrueType Collection
)


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _solid_color_image(width: int, height: int, color: str, bands: int = 3) -> pyvips.Image:
    r, g, b = _hex_to_rgb(color)
    rgb = (r, g, b)[:bands] if bands <= 3 else (r, g, b) + (255,) * (bands - 3)
    image = (pyvips.Image.black(width, height, bands=bands) + list(rgb)).cast("uchar")
    return image.copy(interpretation="srgb")


def _blank_canvas(canvas: Canvas) -> pyvips.Image:
    return _solid_color_image(canvas.width_px, canvas.height_px, canvas.background, bands=3)


def _resize_for_slot(image: pyvips.Image, slot: Slot) -> tuple[pyvips.Image, int, int]:
    """Resize/crop `image` per `slot.fit`. Returns (resized_image, paste_x, paste_y)
    relative to the slot's own top-left (i.e. add slot.x/slot.y for canvas coords)."""
    if slot.fit == "fill":
        hscale = slot.w / image.width
        vscale = slot.h / image.height
        resized = image.resize(hscale, vscale=vscale)
        return resized, 0, 0

    if slot.fit == "cover":
        scale = max(slot.w / image.width, slot.h / image.height)
        resized = image.resize(scale)
        left = round((resized.width - slot.w) / 2)
        top = round((resized.height - slot.h) / 2)
        cropped = resized.crop(left, top, slot.w, slot.h)
        return cropped, 0, 0

    # contain: scale to fit entirely within the slot, centered; the
    # letterbox area is left untouched, so it shows through as whatever the
    # canvas background already painted there (canvas is filled before any
    # slot is composited — see `_composite`).
    scale = min(slot.w / image.width, slot.h / image.height)
    resized = image.resize(scale)
    offset_x = round((slot.w - resized.width) / 2)
    offset_y = round((slot.h - resized.height) / 2)
    return resized, offset_x, offset_y


def _load_font(font_path: Path) -> bool:
    """Return True if `font_path` looks like a real, loadable font file
    (checked via magic bytes), False otherwise."""
    try:
        with font_path.open("rb") as f:
            header = f.read(4)
    except OSError:
        return False
    return any(header.startswith(prefix) for prefix in _FONT_MAGIC_PREFIXES)


def _render_text_overlay(
    overlay: TextOverlay, template_dir: Path, event: EventConfig
) -> pyvips.Image:
    # pyvips.Image.text() always parses `text` as Pango markup, so resolved
    # content (which may contain "&", "<", ">" from event vars) must be
    # XML-escaped first or libvips raises "invalid markup in text".
    content = _xml_escape(resolve_placeholders(overlay.content, event))
    font_path = template_dir / overlay.font

    if _load_font(font_path):
        font_spec = f"{font_path.stem} {overlay.size}"
        try:
            mask = pyvips.Image.text(content, font=font_spec, fontfile=str(font_path), dpi=72)
        except pyvips.Error:
            logger.warning(
                "compositor.font_load_failed",
                font=str(font_path),
                fallback=_FALLBACK_FONT_FAMILY,
            )
            mask = pyvips.Image.text(
                content, font=f"{_FALLBACK_FONT_FAMILY} {overlay.size}", dpi=72
            )
    else:
        logger.warning(
            "compositor.font_not_a_real_font_file",
            font=str(font_path),
            fallback=_FALLBACK_FONT_FAMILY,
        )
        mask = pyvips.Image.text(content, font=f"{_FALLBACK_FONT_FAMILY} {overlay.size}", dpi=72)

    color_layer = _solid_color_image(mask.width, mask.height, overlay.color, bands=3)
    return color_layer.bandjoin(mask).copy(interpretation="srgb")


def _anchor_position(
    overlay_w: int, overlay_h: int, canvas_w: int, canvas_h: int, overlay: TextOverlay
) -> tuple[int, int]:
    anchor = overlay.anchor
    if "left" in anchor:
        x = 0
    elif "right" in anchor:
        x = canvas_w - overlay_w
    else:
        x = round((canvas_w - overlay_w) / 2)

    if anchor.startswith("top"):
        y = 0
    elif anchor.startswith("bottom"):
        y = canvas_h - overlay_h
    else:
        y = round((canvas_h - overlay_h) / 2)

    return x + overlay.x_offset, y + overlay.y_offset


def _logo_path(event: EventConfig, events_dir: Path | None) -> Path | None:
    if not event.include_logo_in_prints or not event.logo_image or events_dir is None:
        return None
    path = events_dir / event.id / event.logo_image
    return path if path.is_file() else None


def _render_logo_overlay(overlay: LogoOverlay, logo_path: Path) -> tuple[pyvips.Image, int, int]:
    """Fit the event's logo within `overlay`'s box (aspect preserved, never
    upscaled past the box) and bottom-right-align it inside that box —
    matching the "logo in the corner" convention rather than stretching it
    to fill an arbitrary rectangle."""
    logo = pyvips.Image.new_from_file(str(logo_path))
    if logo.interpretation != "srgb":
        logo = logo.colourspace("srgb")
    logo = logo.thumbnail_image(overlay.w, height=overlay.h, size="down")
    paste_x = overlay.x + (overlay.w - logo.width)
    paste_y = overlay.y + (overlay.h - logo.height)
    return logo, paste_x, paste_y


def _composite(
    template: TemplateConfig,
    template_dir: Path,
    source_images: list[Path],
    event: EventConfig,
    events_dir: Path | None = None,
) -> pyvips.Image:
    """Render the full slot + overlay composite at the template's native
    (print-dpi) pixel size. Shared by every variant so callers rendering
    multiple variants from one shoot only need to redo the cheap per-variant
    resize/encode step, not this whole composite."""
    if len(source_images) != len(template.slots):
        raise ValueError(
            f"expected {len(template.slots)} source image(s) for template "
            f"{template.name!r} (one per slot), got {len(source_images)}"
        )

    canvas = _blank_canvas(template.canvas)

    for slot, source_path in zip(template.slots, source_images, strict=True):
        source = pyvips.Image.new_from_file(str(source_path))
        if source.bands >= 3 and source.interpretation != "srgb":
            source = source.colourspace("srgb")
        resized, offset_x, offset_y = _resize_for_slot(source, slot)
        canvas = canvas.composite2(resized, "over", x=slot.x + offset_x, y=slot.y + offset_y)

    for overlay in template.overlays:
        if isinstance(overlay, ImageOverlay):
            overlay_path = template_dir / overlay.src
            overlay_image = pyvips.Image.new_from_file(str(overlay_path))
            overlay_image = overlay_image.thumbnail_image(overlay.w, height=overlay.h, size="force")
            if overlay_image.interpretation != "srgb":
                overlay_image = overlay_image.colourspace("srgb")
            canvas = canvas.composite2(overlay_image, "over", x=overlay.x, y=overlay.y)
        elif isinstance(overlay, LogoOverlay):
            logo_path = _logo_path(event, events_dir)
            if logo_path is not None:
                logo_image, paste_x, paste_y = _render_logo_overlay(overlay, logo_path)
                canvas = canvas.composite2(logo_image, "over", x=paste_x, y=paste_y)
        else:
            text_image = _render_text_overlay(overlay, template_dir, event)
            x, y = _anchor_position(
                text_image.width, text_image.height, canvas.width, canvas.height, overlay
            )
            canvas = canvas.composite2(text_image, "over", x=x, y=y)

    return canvas


def _encode(image: pyvips.Image, fmt: str, quality: int) -> bytes:
    if fmt == "png":
        result: bytes = image.pngsave_buffer()
        return result
    result = image.jpegsave_buffer(Q=quality)
    return result


def render_variant(
    template_path: Path,
    source_images: list[Path],
    variant: str,
    event: EventConfig,
    events_dir: Path | None = None,
) -> bytes:
    """Render one output variant (print/web/thumb) of `template_path`,
    compositing `source_images` (one per slot, in slot order) and resolving
    `{event.*}` text placeholders against `event`. `events_dir` is only
    needed when the template declares a `LogoOverlay` — it resolves
    `events_dir/event.id/event.logo_image`; omit it (or pass an event with
    no logo) and any `LogoOverlay` in the template simply renders nothing.

    Raises `ValueError` if `variant` isn't one of the template's declared
    variant keys, or if `len(source_images) != len(template.slots)`.
    """
    template = load_template(template_path)
    if variant not in template.variants:
        raise ValueError(
            f"unknown variant {variant!r} for template {template.name!r}; "
            f"available: {sorted(template.variants)}"
        )

    template_dir = template_path.parent
    canvas = _composite(template, template_dir, source_images, event, events_dir)

    spec = template.variants[variant]
    if isinstance(spec, PrintVariantSpec):
        # Already rendered at canvas.dpi (native pixel size) — just embed the
        # DPI in the output's metadata (best-effort; not all encoders/readers
        # agree on units, so this is a nice-to-have, not load-bearing).
        dpi = spec.dpi
        try:
            canvas = canvas.copy()
            canvas.set_type(pyvips.GValue.gdouble_type, "xres", dpi / 25.4)
            canvas.set_type(pyvips.GValue.gdouble_type, "yres", dpi / 25.4)
        except pyvips.Error:
            pass
        return _encode(canvas, spec.format, spec.quality)

    # ScaledVariantSpec (web/thumb): resize so the long edge matches.
    long_edge = spec.long_edge
    scale = long_edge / max(canvas.width, canvas.height)
    resized = canvas.resize(scale)
    return _encode(resized, spec.format, spec.quality)
