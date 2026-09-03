"""Unit tests for config/event.py: EventConfig loading + {event.*} placeholder
resolution (IMPLEMENTATION_PLAN.md §8 T-2.7)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from photobooth.config.event import (
    EventConfig,
    PlaceholderResolutionError,
    load_event,
    resolve_placeholders,
)


def _event(**overrides: object) -> EventConfig:
    data: dict[str, object] = {
        "id": "test-event",
        "title": "Anna & Ben's Wedding",
        "date": "2026-09-12",
        "template": "collage-2x2.yaml",
        "vars": {"couple": "Anna & Ben"},
    }
    data.update(overrides)
    return EventConfig.model_validate(data)


def test_load_event(tmp_path: Path) -> None:
    event_dir = tmp_path / "example-event"
    event_dir.mkdir()
    (event_dir / "event.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "example-event",
                "title": "Anna & Ben's Wedding",
                "date": "2026-09-12",
                "template": "collage-2x2.yaml",
                "gallery_enabled": True,
                "vars": {"couple": "Anna & Ben"},
            }
        )
    )
    event = load_event(tmp_path, "example-event")
    assert event.id == "example-event"
    assert event.title == "Anna & Ben's Wedding"
    assert event.vars["couple"] == "Anna & Ben"


def test_load_real_example_event() -> None:
    """The shipped events/example-event must actually load."""
    repo_root = Path(__file__).resolve().parents[2]
    event = load_event(repo_root / "events", "example-event")
    assert event.id == "example-event"
    assert event.template == "collage-2x2.yaml"
    assert "couple" in event.vars


def test_resolve_well_known_field() -> None:
    event = _event()
    assert resolve_placeholders("{event.title}", event) == "Anna & Ben's Wedding"


def test_resolve_date_field() -> None:
    event = _event()
    assert resolve_placeholders("{event.date}", event) == "2026-09-12"


def test_resolve_vars_fallback() -> None:
    event = _event()
    assert resolve_placeholders("{event.couple}", event) == "Anna & Ben"


def test_resolve_multiple_placeholders() -> None:
    event = _event()
    result = resolve_placeholders("{event.couple} · {event.date}", event)
    assert result == "Anna & Ben · 2026-09-12"


def test_resolve_unknown_placeholder_raises() -> None:
    event = _event()
    with pytest.raises(PlaceholderResolutionError, match="hashtag"):
        resolve_placeholders("{event.hashtag}", event)


def test_resolve_no_placeholders_passthrough() -> None:
    event = _event()
    assert resolve_placeholders("plain text", event) == "plain text"
