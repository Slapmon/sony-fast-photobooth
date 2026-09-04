"""Built-in event starting points for the admin "New Event" wizard.

Each preset seeds `EventTheme` + `CaptureMode`s + a `vars` hint dict onto a
freshly created `event.yaml` (see `web/routers/admin.py`'s `POST
/admin/events`). "Start from scratch" isn't a fourth preset here — the
wizard simply omits `based_on` and the admin route falls back to
`EventConfig`/`EventTheme`'s own schema defaults.
"""

from __future__ import annotations

from pydantic import BaseModel

from .event import CaptureMode


class EventTemplatePreset(BaseModel):
    id: str
    label: str
    description: str
    scrim_color: str
    primary_color: str
    modes: list[CaptureMode]
    vars_hint: dict[str, str]


EVENT_TEMPLATE_PRESETS: list[EventTemplatePreset] = [
    EventTemplatePreset(
        id="wedding",
        label="Wedding",
        description="Rose-charcoal backdrop with a dusty-rose accent.",
        scrim_color="#241A1C",
        primary_color="#C98A93",
        modes=[
            CaptureMode(id="single", label="Single Photo", template="single.yaml"),
            CaptureMode(id="collage", label="Collage", template="collage-2x2.yaml"),
        ],
        vars_hint={"couple": "", "hashtag": ""},
    ),
    EventTemplatePreset(
        id="birthday",
        label="Birthday",
        description="Teal-charcoal backdrop with a coral accent.",
        scrim_color="#152420",
        primary_color="#FF7A50",
        modes=[
            CaptureMode(id="single", label="Single Photo", template="single.yaml"),
            CaptureMode(id="collage", label="Collage", template="collage-2x2.yaml"),
            CaptureMode(id="strip", label="Photo Strip", template="strip-3strip.yaml"),
        ],
        vars_hint={"name": ""},
    ),
    EventTemplatePreset(
        id="corporate",
        label="Corporate",
        description="Slate-charcoal backdrop with a slate-blue accent.",
        scrim_color="#181D24",
        primary_color="#5B84B0",
        modes=[
            CaptureMode(id="single", label="Single Photo", template="single.yaml"),
            CaptureMode(id="team", label="Team Photo", template="strip-1plus2.yaml"),
        ],
        vars_hint={"company": ""},
    ),
]

_PRESETS_BY_ID = {preset.id: preset for preset in EVENT_TEMPLATE_PRESETS}


def get_preset(preset_id: str) -> EventTemplatePreset | None:
    return _PRESETS_BY_ID.get(preset_id)
