"""Per-event config: `events/<event_id>/event.yaml` + `{event.*}` placeholder
resolution for template text overlays. See photobooth-plan.md §8 and
IMPLEMENTATION_PLAN.md §8 (T-2.7).

Not yet wired into the live capture flow — `web/session.py` still hardcodes
`event_id="dev"` (see the comment there); a later task rewires that to load
through `load_event()` using `Settings.events`.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel

# Fields resolvable directly off EventConfig (before falling back to `vars`).
_WELL_KNOWN_FIELDS = ("title", "date")

_PLACEHOLDER_RE = re.compile(r"\{event\.([A-Za-z0-9_]+)\}")


class CaptureMode(BaseModel):
    """One guest-facing button on the attract screen (e.g. "Single Photo",
    "Collage"), each driving a different template. Distinct from the
    per-event gallery link — see `EventConfig.gallery_enabled`."""

    id: str
    label: str
    template: str


class EventTheme(BaseModel):
    """Per-event color override. Empty `primary_color` means "use the app's
    default palette" (frontend/src/app.css's --color-primary) — this is the
    one thing an event customizes today; extend here if more knobs are
    ever needed rather than growing EventConfig directly."""

    primary_color: str = ""


class EventConfig(BaseModel):
    id: str
    title: str
    date: str = ""
    # Legacy/fallback template: used when `modes` is empty (old single-mode
    # events keep working unchanged) and as the default for admin surfaces
    # (template preview) that need "a" template rather than a guest choice.
    template: str
    # Explicit guest-facing capture modes. Empty by default so existing
    # events/tests with only `template` set are unaffected — the kiosk
    # synthesizes a single default mode from `template` in that case (see
    # web/routers/kiosk.py's GET /session/event).
    modes: list[CaptureMode] = []
    background_image: str = ""
    # Centered over the background on the attract screen and gallery page.
    # Optional — an event with no logo just shows title/date over the
    # background image.
    logo_image: str = ""
    theme: EventTheme = EventTheme()
    gallery_enabled: bool = True
    vars: dict[str, str] = {}
    # Guest-facing UI chrome (button labels, captions) — distinct from
    # `vars`, which only feeds `{event.*}` template text placeholders.
    # Sparse by design: only keys this event wants to override; anything
    # missing falls back to DEFAULT_UI_STRINGS (English) via
    # resolved_strings() below, so old events with no `strings` at all keep
    # working unchanged. Scoped to the guest-facing kiosk/gallery surfaces
    # only — the admin panel itself is not translated.
    strings: dict[str, str] = {}


# English defaults for every guest-facing string the kiosk/gallery UI
# renders that ISN'T already event-content (mode labels already come from
# CaptureMode.label; placeholder text already comes from `vars`). Each key
# here is a real, working UI string today — see the frontend components
# named alongside each for where it's consumed.
DEFAULT_UI_STRINGS: dict[str, str] = {
    "attract_cta": "Touch a button below to start",  # AttractScreen.svelte
    "gallery_word": "Gallery",  # BottomNav.svelte button, Gallery.svelte heading
    "capturing_label": "Capturing…",  # Kiosk.svelte
    "print_button": "Print",  # Kiosk.svelte
    "print_button_busy": "Printing…",  # Kiosk.svelte
    "qr_caption": "Scan to get your photo",  # Kiosk.svelte
    "gallery_loading": "Loading…",  # Gallery.svelte
    "gallery_empty": "No photos yet.",  # Gallery.svelte
    "gallery_not_available": "Gallery not available for this event.",  # Gallery.svelte
}


def resolved_strings(event: EventConfig) -> dict[str, str]:
    """DEFAULT_UI_STRINGS with this event's overrides layered on top —
    always returns every known key, so the frontend never needs its own
    fallback logic. An event with `strings: {}` (the default) gets pure
    English; one guest language needs only override the keys it wants to
    change."""
    return {**DEFAULT_UI_STRINGS, **event.strings}


class PlaceholderResolutionError(Exception):
    """A `{event.X}` placeholder in template text has no matching EventConfig
    field or `vars` entry. Raised eagerly (at render/load time) rather than
    letting `{event.X}` leak into a guest-facing print or gallery page."""


def load_event(events_dir: Path, event_id: str) -> EventConfig:
    path = events_dir / event_id / "event.yaml"
    data = yaml.safe_load(path.read_text())
    return EventConfig.model_validate(data)


def resolve_placeholders(text: str, event: EventConfig) -> str:
    """Replace every `{event.<key>}` in `text`. Well-known fields (title,
    date) are looked up as attributes first, then `event.vars[<key>]`."""

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in _WELL_KNOWN_FIELDS:
            return str(getattr(event, key))
        if key in event.vars:
            return event.vars[key]
        raise PlaceholderResolutionError(
            f"{{event.{key}}} has no matching EventConfig field or vars entry "
            f"(event id={event.id!r})"
        )

    return _PLACEHOLDER_RE.sub(_replace, text)
