# Task runner for the photobooth project. Install `just`: https://github.com/casey/just
#
# Local dev (laptop, mock backends) vs Pi (real hardware) are separate profiles
# selected via config/dev.yaml / config/pi.yaml — see IMPLEMENTATION_PLAN.md §3.

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# Install dependencies (core + dev extras) into a local venv via uv.
install:
    uv sync --extra dev

# Run the app against the dev profile (mock camera/printer/upload backends).
dev:
    uv run uvicorn photobooth.web.app:app --reload --port 8000

# Run the full test suite (hardware-marked tests skipped by default).
test:
    uv run pytest

# Run only the contract tests (same assertions against every backend impl).
test-contract:
    uv run pytest tests/contract -v

# Lint + format check.
lint:
    uv run ruff check .
    uv run ruff format --check .

# Static type check on the hot-path packages (core, camera).
typecheck:
    uv run mypy

# Run everything CI runs, locally.
check: lint typecheck test

# Run the Phase 0 benchmark harness over SSH on the Pi and pull results back.
# Usage: just bench-pi user@pi-host
bench-pi host:
    ssh {{host}} 'cd photobooth && uv run python tools/bench_camera.py'
    scp {{host}}:photobooth/bench_results.db ./bench_results.pi.db

# Frontend dev server (Svelte + Vite).
frontend-dev:
    cd frontend && npm run dev
