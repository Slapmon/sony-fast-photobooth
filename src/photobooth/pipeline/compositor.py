"""Template-driven pyvips compositor: slots, fit modes, overlays, text.

Renders print / web / thumb variants from one template.yaml + source images.
See photobooth-plan.md §8 for the template schema and
IMPLEMENTATION_PLAN.md §8 (T-2.1..T-2.8).
"""

from __future__ import annotations

from pathlib import Path


def render_variant(template_path: Path, source_images: list[Path], variant: str) -> bytes:
    raise NotImplementedError("T-2.1: template YAML schema + pydantic models first")
