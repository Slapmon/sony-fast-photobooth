# systemd deployment (T-5.1)

`photobooth.service` runs the FastAPI app (which spawns the camera-worker
subprocess itself — see `src/photobooth/web/app.py`'s lifespan) under
systemd on the Pi.

**go2rtc has no unit here on purpose.** Per `IMPLEMENTATION_PLAN.md` T-1.10
and `photobooth-plan.md` §0, go2rtc already runs as its own systemd service
on the Pi, confirmed working in Phase 0. Creating a second unit for it here
would either conflict with that existing service or silently duplicate it.
If your Pi's go2rtc service has a different unit name than `go2rtc.service`,
edit `photobooth.service`'s `After=`/`Wants=` line to match (or just check
what's already there: `systemctl list-units | grep -i go2rtc`).

## Install

```bash
# One-time: create the dedicated service user and grant hardware access
sudo useradd --system --home /opt/photobooth --shell /usr/sbin/nologin photobooth
sudo usermod -aG video,dialout,plugdev photobooth

# Deploy the code to /opt/photobooth (adjust to your actual deploy path —
# and update photobooth.service's WorkingDirectory/ExecStart if you use a
# different one), with a venv at /opt/photobooth/.venv containing the
# `pi` extra installed (see pyproject.toml).

sudo cp deploy/systemd/photobooth.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now photobooth.service
```

`just install-systemd <host>` automates the `cp` + `daemon-reload` +
`enable --now` steps over SSH — see the justfile.

## Logs

journald owns stdout/stderr capture and rotation for this unit (see
`src/photobooth/telemetry/logging_config.py` for why we didn't also build
file-based rotation):

```bash
journalctl -u photobooth -f          # follow live
journalctl -u photobooth --since "1 hour ago"
journalctl -u photobooth -o json     # structlog JSON lines pass through as
                                      # the MESSAGE field on each journal entry
```

To bound journald's own disk usage (it isn't unlimited by default, but the
default cap is often larger than makes sense on a Pi's SD/SSD), set in
`/etc/systemd/journald.conf`:

```ini
[Journal]
SystemMaxUse=500M
MaxRetentionSec=30day
```

then `sudo systemctl restart systemd-journald`. Pick numbers that fit your
boot media — the values above are a reasonable starting point for a
several-GB SD/SSD, not a measured requirement.

## What this unit does and does not cover

- **Does:** `Restart=always` with a 5s backoff, so a crashed app (or a
  crashed camera-worker subprocess that takes the app down with it) comes
  back automatically without hammering the USB/camera stack on every
  restart. Graceful shutdown: SIGTERM → uvicorn's normal shutdown → the
  lifespan handler's `worker_process.terminate()` / `.wait(timeout=5)` /
  `.kill()` (already implemented in `web/app.py`) → journald sees a clean
  exit. `TimeoutStopSec=15` gives that whole sequence room to finish before
  systemd escalates to SIGKILL.
- **Does not:** true systemd watchdog liveness-pinging (`Type=notify` +
  `WatchdogSec=` + the app calling `sd_notify(WATCHDOG=1)` periodically).
  That needs a real code change inside `web/app.py`'s lifespan/startup
  (a background task pinging systemd on a live event loop), which is out of
  scope for this unit-file-only task — see the unit file's own comment
  block for exactly where that would go. Until it's wired in, `Restart=always`
  still recovers from a hard crash, it just can't detect a hang where the
  process is alive but wedged (e.g. deadlocked on the camera-worker TCP
  socket without exiting).
