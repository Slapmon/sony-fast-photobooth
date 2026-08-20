# Photobooth

Rentable event photobooth software: fast tethered capture, live preview,
templated compositing, printing, and delivery.

Planning docs (read first):

- [`photobooth-plan.md`](photobooth-plan.md) — goals, architecture, hardware, legal
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — stack, project layout, phased task list (the living checklist)

## Status

Pre-Phase-0. No application code runs against real hardware yet. See
`IMPLEMENTATION_PLAN.md` §6 for the benchmark spike that has to happen on the
Pi before Phase 1 starts.

## Layout

```
src/photobooth/   application package (see IMPLEMENTATION_PLAN.md §2)
frontend/         kiosk + gallery + admin (Svelte 5 + Vite)
tests/            contract / unit / golden / e2e
tools/            standalone Phase-0 benchmark + soak scripts (no app deps)
fixtures/shots/   sample JPEGs for the mock camera backend
events/           per-event config + assets (gitignored content, tracked dir)
templates/        layout YAMLs for the compositor
config/           dev.yaml / pi.yaml profiles
```

## Getting started (dev laptop, mock backends)

```
pip install uv        # if not already installed
uv sync --extra dev
just dev               # or: uv run uvicorn photobooth.web.app:app --reload
just test
```

Real camera/printer/vips backends (`pi` extra) require system libraries
(libgphoto2, libvips, CUPS) and are meant to run on the Pi — see
`IMPLEMENTATION_PLAN.md` §3 for the dev/Pi split.
