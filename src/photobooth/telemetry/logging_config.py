"""Log setup — JSON lines via `structlog`, per-module levels
(IMPLEMENTATION_PLAN.md T-5.2).

**Not called from anywhere yet.** `configure_logging()` needs to run once,
very early in the process, before anything else logs — the natural spot is
the top of `web/app.py`'s `lifespan()` (right after `Settings.load()`
resolves the config, since it needs `settings.logging`), or even earlier at
module import time if pre-startup logging matters. Wiring that call in is
explicitly out of scope for this task (constraints say not to edit
`web/app.py`) — a later wave should add:

    from photobooth.telemetry.logging_config import configure_logging
    ...
    settings = Settings.load(config_path)
    configure_logging(settings.logging)

as the very first lines inside `lifespan()`, before `app.state.settings` is
even set. The camera-worker subprocess (`camera/worker.py`'s `main()`) is a
separate process with its own Python interpreter — if per-module levels
should apply there too, `main()` needs its own `configure_logging()` call;
not done here since the worker doesn't take a `Settings` object today (it's
driven by argparse flags built in `web/app.py`'s `_worker_args()`).

## Why no file-based rotation

This app runs under systemd on the Pi (T-5.1's `deploy/systemd/`).
journald already owns rotation/retention for anything written to
stdout/stderr — `structlog`'s default output goes there. Nothing in this
codebase writes logs to a plain file today (checked: no `logging.FileHandler`
/ `RotatingFileHandler` / `open(..., "a")`-style log sink exists anywhere in
`src/photobooth/`), so there's no existing file sink this needs to
size/rotate, and building one redundantly would just be a second rotation
policy to keep in sync with journald's. See `deploy/systemd/README.md` for
the `journald.conf` knobs (`SystemMaxUse=`, `MaxRetentionSec=`) that do this
job on the Pi. If a future need arises for logs to be readable outside of
systemd/journald (e.g. shipping them off-box, or running under a plain
`python -m uvicorn` with no supervisor), revisit this file and add a
`logging.handlers.TimedRotatingFileHandler` sink at that point rather than
building it speculatively now.
"""

from __future__ import annotations

import logging
import sys

import structlog

from photobooth.config.models import LoggingConfig


def configure_logging(config: LoggingConfig) -> None:
    """Configure stdlib `logging` + `structlog` for JSON-lines output.

    Idempotent-ish: safe to call more than once (e.g. in tests) — it always
    resets the root logger's handlers rather than appending duplicates.

    `config.level` sets the root logger's level (and therefore the default
    for every module logger that doesn't have its own explicit level).
    `config.module_levels` then overrides specific logger names via the
    standard `logging.getLogger(name).setLevel(...)` mechanism — this is
    exactly how Python's logging hierarchy is meant to be tuned per module,
    and it's the layer structlog's stdlib-backed loggers ultimately sit on.
    """
    root_level = _resolve_level(config.level)

    root_logger = logging.getLogger()
    root_logger.setLevel(root_level)
    # Drop any handlers from a previous configure_logging() call (or from
    # logging.basicConfig() having been called implicitly by something else)
    # so repeated calls don't emit each line multiple times.
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    root_logger.addHandler(handler)

    for module_name, level_name in config.module_levels.items():
        logging.getLogger(module_name).setLevel(_resolve_level(level_name))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(root_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _resolve_level(level_name: str) -> int:
    level = logging.getLevelName(level_name.upper())
    if not isinstance(level, int):
        raise ValueError(f"invalid log level: {level_name!r}")
    return level
