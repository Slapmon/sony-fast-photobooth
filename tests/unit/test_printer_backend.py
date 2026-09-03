"""Tests for printing/backend.py — NullPrinter, CupsBackend (against a
monkeypatched `cups.Connection`), and the `build_printer_backend` factory
(T-4.6).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from photobooth.config.models import CupsConfig, NullPrinterConfig, PrintingConfig
from photobooth.printing.backend import (
    CupsBackend,
    NullPrinter,
    PrinterOfflineError,
    build_printer_backend,
)


@pytest.fixture
def sample_jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "shot.jpg"
    Image.new("RGB", (32, 24), color="red").save(path, "JPEG")
    return path


# ---------------------------------------------------------------------------
# NullPrinter
# ---------------------------------------------------------------------------


async def test_null_printer_submit_writes_pdf_and_returns_job_id(
    tmp_path: Path, sample_jpeg: Path
) -> None:
    printer = NullPrinter(output_dir=tmp_path / "prints", simulated_job_seconds=0)
    job_id = await printer.submit(sample_jpeg, "session-1")

    assert isinstance(job_id, str) and job_id
    pdf_path = tmp_path / "prints" / f"{job_id}.pdf"
    assert pdf_path.exists()
    assert printer.jobs[job_id] in ("printing", "done")


async def test_null_printer_submit_returns_before_simulated_duration_elapses(
    tmp_path: Path, sample_jpeg: Path
) -> None:
    """submit() must not block the caller for `simulated_job_seconds` — it
    returns immediately and the job finishes in the background."""
    printer = NullPrinter(output_dir=tmp_path / "prints", simulated_job_seconds=60)
    job_id = await printer.submit(sample_jpeg, "session-1")
    # If submit() blocked on the simulated duration this test would hang
    # (or time out under pytest-asyncio's default), so simply returning
    # here proves it didn't.
    assert printer.jobs[job_id] == "printing"


async def test_null_printer_status_reports_online_by_default(tmp_path: Path) -> None:
    printer = NullPrinter(output_dir=tmp_path / "prints")
    status = await printer.status()
    assert status["status"] == "green"
    assert isinstance(status["detail"], str)


async def test_null_printer_simulate_out_of_media(tmp_path: Path, sample_jpeg: Path) -> None:
    printer = NullPrinter(output_dir=tmp_path / "prints", simulate_out_of_media=True)

    status = await printer.status()
    assert status["status"] == "red"
    assert status["media_remaining"] == 0

    with pytest.raises(PrinterOfflineError):
        await printer.submit(sample_jpeg, "session-1")


# ---------------------------------------------------------------------------
# build_printer_backend
# ---------------------------------------------------------------------------


def test_build_printer_backend_null(tmp_path: Path) -> None:
    config = PrintingConfig(backend="null", null_backend=NullPrinterConfig(output_dir=tmp_path))
    backend = build_printer_backend(config)
    assert isinstance(backend, NullPrinter)


def test_build_printer_backend_none_disables_printing() -> None:
    config = PrintingConfig(backend=None)
    assert build_printer_backend(config) is None


def test_build_printer_backend_cups(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_cups = _make_fake_cups_module({})
    monkeypatch.setitem(sys.modules, "cups", fake_cups)

    config = PrintingConfig(backend="cups", cups=CupsConfig(printer_name="DNP-DS620"))
    backend = build_printer_backend(config)
    assert isinstance(backend, CupsBackend)


def test_build_printer_backend_cups_requires_printer_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_cups = _make_fake_cups_module({})
    monkeypatch.setitem(sys.modules, "cups", fake_cups)

    config = PrintingConfig(backend="cups", cups=CupsConfig(printer_name=""))
    with pytest.raises(RuntimeError):
        build_printer_backend(config)


def test_cups_backend_raises_clear_error_without_pycups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cups", None)  # simulates "not installed"
    with pytest.raises(RuntimeError, match="pycups"):
        CupsBackend(printer_name="DNP-DS620")


# ---------------------------------------------------------------------------
# CupsBackend against a monkeypatched cups.Connection
# ---------------------------------------------------------------------------


class _FakeCupsConnection:
    def __init__(self, printers: dict[str, dict[str, Any]]) -> None:
        self._printers = printers
        self.printed: list[tuple[str, str, str, dict[str, Any]]] = []
        self._next_job_id = 100

    def getPrinters(self) -> dict[str, dict[str, Any]]:
        return self._printers

    def printFile(
        self, printer_name: str, filename: str, title: str, options: dict[str, Any]
    ) -> int:
        self.printed.append((printer_name, filename, title, options))
        job_id = self._next_job_id
        self._next_job_id += 1
        return job_id


def _make_fake_cups_module(printers: dict[str, dict[str, Any]]) -> types.ModuleType:
    module = types.ModuleType("cups")
    module.Connection = lambda: _FakeCupsConnection(printers)  # type: ignore[attr-defined]
    return module


def _install_fake_cups(
    monkeypatch: pytest.MonkeyPatch, printers: dict[str, dict[str, Any]]
) -> _FakeCupsConnection:
    conn = _FakeCupsConnection(printers)
    module = types.ModuleType("cups")
    module.Connection = lambda: conn  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cups", module)
    return conn


async def test_cups_backend_submit_calls_print_file(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _install_fake_cups(monkeypatch, {"DNP-DS620": {"printer-state": 3}})
    backend = CupsBackend(printer_name="DNP-DS620")

    job_id = await backend.submit(Path("shot.jpg"), "session-42")

    assert job_id == "100"
    assert len(conn.printed) == 1
    printer_name, filename, title, _options = conn.printed[0]
    assert printer_name == "DNP-DS620"
    assert filename == "shot.jpg"
    assert "session-42" in title


async def test_cups_backend_status_idle_is_green(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cups(
        monkeypatch, {"DNP-DS620": {"printer-state": 3, "printer-state-reasons": ["none"]}}
    )
    backend = CupsBackend(printer_name="DNP-DS620")
    status = await backend.status()
    assert status["status"] == "green"


async def test_cups_backend_status_media_empty_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cups(
        monkeypatch,
        {
            "DNP-DS620": {
                "printer-state": 3,
                "printer-state-reasons": ["media-empty-warning"],
            }
        },
    )
    backend = CupsBackend(printer_name="DNP-DS620")
    status = await backend.status()
    assert status["status"] == "red"
    assert "media-empty" in status["detail"]


async def test_cups_backend_status_stopped_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cups(monkeypatch, {"DNP-DS620": {"printer-state": 5}})
    backend = CupsBackend(printer_name="DNP-DS620")
    status = await backend.status()
    assert status["status"] == "red"


async def test_cups_backend_status_unknown_printer_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_cups(monkeypatch, {})
    backend = CupsBackend(printer_name="DNP-DS620")
    status = await backend.status()
    assert status["status"] == "red"
    assert "not found" in status["detail"]


async def test_cups_backend_status_connection_error_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrokenConnection:
        def getPrinters(self) -> dict[str, Any]:
            raise RuntimeError("CUPS daemon unreachable")

    module = types.ModuleType("cups")
    module.Connection = lambda: _BrokenConnection()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cups", module)

    backend = CupsBackend(printer_name="DNP-DS620")
    status = await backend.status()
    assert status["status"] == "red"
    assert "unreachable" in status["detail"]
