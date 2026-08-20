"""Printer backend interface — NullPrinter (dev) / CupsBackend (Pi) behind
one interface. Print jobs are queued, never inline with the UI
(photobooth-plan.md §9). See IMPLEMENTATION_PLAN.md T-4.6..T-4.9.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class PrinterBackend(ABC):
    @abstractmethod
    async def submit(self, image_path: Path, session_id: str) -> str:
        """Queue a print job, returning a job id."""

    @abstractmethod
    async def status(self) -> dict[str, object]:
        """Media remaining / paper out / error — gates the print button."""


class NullPrinter(PrinterBackend):
    def __init__(self, output_dir: Path, simulated_job_seconds: int = 13) -> None:
        self._output_dir = output_dir
        self._simulated_job_seconds = simulated_job_seconds

    async def submit(self, image_path: Path, session_id: str) -> str:
        raise NotImplementedError("T-4.6: NullPrinter writes PDFs to out/prints/")

    async def status(self) -> dict[str, object]:
        raise NotImplementedError
