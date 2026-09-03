"""Printer backend interface — NullPrinter (dev) / CupsBackend (Pi) behind
one interface. Print jobs are queued, never inline with the UI
(photobooth-plan.md §9). See IMPLEMENTATION_PLAN.md T-4.6..T-4.9.

`status()` returns the SAME shape as web/health_checks.py's convention:
`{"status": "green" | "red" | "gray", "detail": str, ...}` (extra keys, e.g.
`media_remaining`, are additive) — so a later wave can plug a
`PrinterBackend` straight into `/admin/status` and `/debug/health`'s
printer checks without reshaping anything. "gray" means "not meaningfully
checkable right now" (mirrors health_checks.py's own doc for that value),
which is what a `None`-configured printer resolves to (see
`build_printer_backend` below) rather than the ad-hoc `"not_configured"`
literal `health_checks.NOT_CONFIGURED_PRINTER` currently hardcodes — wiring
that constant to a real backend is left to the wave that does the wiring.
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

if TYPE_CHECKING:
    import cups

    from photobooth.config.models import PrintingConfig


class PrinterOfflineError(Exception):
    """Raised by `submit()` when the backend is known to be unable to print
    right now (out of media, printer stopped, etc). `status()` is the
    primary gating mechanism (a later wave uses it to disable the guest
    print button, T-4.8) — this exception is the belt-and-braces case where
    `submit()` is called anyway (a race between the status check and the
    tap, or a queued retry hitting a printer that went offline meanwhile).
    """


class PrinterBackend(ABC):
    @abstractmethod
    async def submit(self, image_path: Path, session_id: str) -> str:
        """Queue a print job, returning a job id."""

    @abstractmethod
    async def status(self) -> dict[str, object]:
        """Media remaining / paper out / error — gates the print button."""


class NullPrinter(PrinterBackend):
    """Dev/test backend. `submit()` "prints" by converting the JPEG to a PDF
    under `output_dir` — config/dev.yaml documents this as the dev-mode
    printing behaviour. Returns its job id immediately (never blocks the
    caller on `simulated_job_seconds`); a background asyncio task marks the
    job "done" internally after that delay, giving the backend real
    async-job fidelity (an id you get back right away, completion that
    lands later) without building out a full status-per-job API that
    nothing yet consumes.
    """

    def __init__(
        self,
        output_dir: Path,
        simulated_job_seconds: int = 13,
        simulate_out_of_media: bool = False,
    ) -> None:
        self._output_dir = output_dir
        self._simulated_job_seconds = simulated_job_seconds
        self.simulate_out_of_media = simulate_out_of_media
        # job_id -> "printing" | "done". Not exposed via the PrinterBackend
        # interface (nothing needs per-job polling yet) but kept so tests
        # and any future admin surface can inspect what happened.
        self.jobs: dict[str, str] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def submit(self, image_path: Path, session_id: str) -> str:
        if self.simulate_out_of_media:
            raise PrinterOfflineError("NullPrinter: out of media (simulated)")

        job_id = uuid.uuid4().hex
        self._output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = self._output_dir / f"{job_id}.pdf"
        with Image.open(image_path) as im:
            im.convert("RGB").save(pdf_path, "PDF")

        self.jobs[job_id] = "printing"
        task = asyncio.create_task(self._finish_after_delay(job_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return job_id

    async def _finish_after_delay(self, job_id: str) -> None:
        await asyncio.sleep(self._simulated_job_seconds)
        if job_id in self.jobs:
            self.jobs[job_id] = "done"

    async def status(self) -> dict[str, object]:
        if self.simulate_out_of_media:
            return {"status": "red", "detail": "out of media (simulated)", "media_remaining": 0}
        return {"status": "green", "detail": "online (simulated)", "media_remaining": 9999}


class CupsBackend(PrinterBackend):
    """Real CUPS integration via `pycups`. Only usable on a machine with a
    real CUPS install and the `pi` extra installed (`pycups`) — import is
    deferred into `__init__`, same guard style as `camera/gphoto.py`'s
    `gphoto2` import, so this module stays importable (for typing/tests) on
    a machine without CUPS.
    """

    # CUPS `printer-state` IPP values (RFC 8011 §5.4.12).
    _STATE_IDLE = 3
    _STATE_PROCESSING = 4
    _STATE_STOPPED = 5

    def __init__(self, printer_name: str) -> None:
        try:
            import cups
        except ImportError as exc:
            raise RuntimeError(
                "CupsBackend requires the `pycups` package and a working CUPS "
                "install — install the `pi` extra (`pip install '.[pi]'`) on a "
                "machine with libcups installed. Not available in this environment."
            ) from exc
        if not printer_name:
            raise RuntimeError(
                "CupsBackend requires printing.cups.printer_name to be set "
                "(see config/pi.yaml: fill it in after `lpstat -p`)"
            )
        self._printer_name = printer_name
        self._conn: cups.Connection = cups.Connection()

    async def submit(self, image_path: Path, session_id: str) -> str:
        """Hands the JPEG straight to CUPS — most Gutenprint/dye-sub drivers
        rasterize JPEG directly, so no conversion pipeline is built here
        (photobooth-plan.md §9: "keep it simple"). `printFile` is a
        blocking IPP call, so it's pushed off the event loop.
        """
        job_id = await asyncio.to_thread(
            self._conn.printFile,
            self._printer_name,
            str(image_path),
            f"photobooth-{session_id}",
            {},
        )
        return str(job_id)

    async def status(self) -> dict[str, object]:
        return await asyncio.to_thread(self._status_sync)

    def _status_sync(self) -> dict[str, Any]:
        try:
            printers = self._conn.getPrinters()
        except Exception as exc:  # pycups raises cups.IPPError / RuntimeError etc.
            return {"status": "red", "detail": f"CUPS unreachable: {exc}"}

        info = printers.get(self._printer_name)
        if info is None:
            return {
                "status": "red",
                "detail": f"printer {self._printer_name!r} not found in CUPS",
            }

        state = info.get("printer-state")
        reasons = info.get("printer-state-reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        detail = ", ".join(reasons) if reasons else f"printer-state={state}"

        media_low = any("media-empty" in r or "media-needed" in r for r in reasons)
        if media_low or state == self._STATE_STOPPED:
            return {"status": "red", "detail": detail, "printer_state": state}
        if state in (self._STATE_IDLE, self._STATE_PROCESSING):
            return {"status": "green", "detail": detail, "printer_state": state}
        return {"status": "gray", "detail": detail, "printer_state": state}


def build_printer_backend(config: PrintingConfig) -> PrinterBackend | None:
    """Selects a `PrinterBackend` from `PrintingConfig`, matching the
    `_build_backend`-style factory pattern used in camera/worker.py.

    `config.backend is None` means printing is disabled entirely for this
    profile/event — the factory returns `None` rather than a `NoPrinter`
    stub object. Callers (the print queue, the guest UI's print-button
    gating, admin status) must treat `printer_backend is None` as "no
    print path configured": hide/disable the print affordance and report
    the printer line as not configured, without ever calling `submit()`/
    `status()` on anything.
    """
    if config.backend is None:
        return None
    if config.backend == "null":
        return NullPrinter(
            output_dir=config.null_backend.output_dir,
            simulated_job_seconds=config.null_backend.simulated_job_seconds,
            simulate_out_of_media=config.null_backend.simulate_out_of_media,
        )
    if config.backend == "cups":
        return CupsBackend(printer_name=config.cups.printer_name)
    raise ValueError(f"unknown printer backend: {config.backend!r}")
