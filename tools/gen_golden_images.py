"""One-off / re-run-on-purpose generator for tests/golden/ reference images.

Renders the `print` variant of every collage template against the fixture
source image and event, and writes the result into tests/golden/. Only run
this deliberately (a genuine, reviewed change to the compositor or a
template) -- it overwrites the checked-in references that
tests/golden/test_golden_images.py diffs against.

Usage: .venv/Scripts/python.exe tools/gen_golden_images.py
"""

from __future__ import annotations

from pathlib import Path

from photobooth.config.event import load_event
from photobooth.pipeline.compositor import render_variant

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "templates"
GOLDEN_DIR = REPO_ROOT / "tests" / "golden"
SOURCE_IMAGE = REPO_ROOT / "fixtures" / "shots" / "sample-01.jpg"

TEMPLATES = [
    ("collage-2x2.yaml", 4),
    ("strip-1plus2.yaml", 3),
    ("strip-3strip.yaml", 3),
]


def main() -> None:
    event = load_event(REPO_ROOT / "events", "example-event")
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for template_name, slot_count in TEMPLATES:
        sources = [SOURCE_IMAGE] * slot_count
        data = render_variant(TEMPLATES_DIR / template_name, sources, "print", event)
        stem = Path(template_name).stem
        out_path = GOLDEN_DIR / f"{stem}-print.jpg"
        out_path.write_bytes(data)
        print(f"wrote {out_path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
