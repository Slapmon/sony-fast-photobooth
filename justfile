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

# Run tools/cam_test.py on the Pi over SSH (real camera, real gphoto2 —
# see IMPLEMENTATION_PLAN.md §6). The Pi runs a plain venv, not uv (uv was
# never installed there; a Phase 0 finding, not a design choice).
# Usage: just bench-pi admin@pi-host "soak --shots 10 --size L"
bench-pi host *args:
    ssh {{host}} 'cd photobooth && source .venv/bin/activate && python tools/cam_test.py {{args}}'

# Pull captured test JPEGs back from the Pi to out/cam_test/ for a look.
# Usage: just pull-pi-captures admin@pi-host
pull-pi-captures host:
    scp '{{host}}:photobooth/out/cam_test/*.jpg' out/cam_test/

# Frontend dev server (Svelte + Vite).
frontend-dev:
    cd frontend && npm run dev

# Install/update the systemd unit on the Pi and (re)start it. Assumes the
# repo is already deployed at ~/photobooth on the Pi (deploy/systemd's
# photobooth.service defaults to /opt/photobooth — adjust the unit file's
# WorkingDirectory/ExecStart if your deploy path differs before running
# this). See deploy/systemd/README.md for the one-time `useradd` step.
# Usage: just install-systemd admin@pi-host
install-systemd host:
    scp deploy/systemd/photobooth.service '{{host}}:/tmp/photobooth.service'
    ssh {{host}} 'sudo mv /tmp/photobooth.service /etc/systemd/system/photobooth.service && sudo systemctl daemon-reload && sudo systemctl enable --now photobooth.service'

# Run the soak harness (T-5.3) against a running app instance. Defaults to
# the local dev server; point --base-url at the Pi for a real soak.
# Usage: just soak *args (e.g. `just soak --minutes 5`)
soak *args:
    uv run python tools/soak.py {{args}}
