"""configure_logging() (IMPLEMENTATION_PLAN.md T-5.2): root level applies,
per-module overrides actually take effect on the stdlib logger namespace
structlog's stdlib-backed loggers sit on top of.
"""

from __future__ import annotations

import logging

import pytest

from photobooth.config.models import LoggingConfig
from photobooth.telemetry.logging_config import configure_logging


@pytest.fixture(autouse=True)
def _restore_logging_state() -> None:
    """Each test mutates process-global logging state — snapshot and restore
    so tests don't leak levels/handlers into each other or into other test
    modules in the same pytest run.
    """
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    tracked_loggers = ["photobooth.camera", "photobooth.web", "some.other.module"]
    original_module_levels = {name: logging.getLogger(name).level for name in tracked_loggers}
    yield
    root.setLevel(original_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in original_handlers:
        root.addHandler(handler)
    for name, level in original_module_levels.items():
        logging.getLogger(name).setLevel(level)


def test_root_level_applied() -> None:
    configure_logging(LoggingConfig(level="WARNING"))
    assert logging.getLogger().level == logging.WARNING


def test_module_level_override_applied() -> None:
    configure_logging(LoggingConfig(level="INFO", module_levels={"photobooth.camera": "DEBUG"}))
    assert logging.getLogger("photobooth.camera").level == logging.DEBUG
    # A module with no explicit override is untouched (inherits from root
    # via the standard logging hierarchy, i.e. its own .level stays NOTSET).
    assert logging.getLogger("some.other.module").level == logging.NOTSET


def test_multiple_module_overrides_applied() -> None:
    configure_logging(
        LoggingConfig(
            level="INFO",
            module_levels={"photobooth.camera": "DEBUG", "photobooth.web": "ERROR"},
        )
    )
    assert logging.getLogger("photobooth.camera").level == logging.DEBUG
    assert logging.getLogger("photobooth.web").level == logging.ERROR


def test_invalid_level_raises() -> None:
    with pytest.raises(ValueError, match="invalid log level"):
        configure_logging(LoggingConfig(level="NOT_A_LEVEL"))


def test_repeated_calls_do_not_duplicate_handlers() -> None:
    configure_logging(LoggingConfig(level="INFO"))
    configure_logging(LoggingConfig(level="INFO"))
    assert len(logging.getLogger().handlers) == 1
