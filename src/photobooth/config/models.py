"""Profile config models — one file (dev.yaml / pi.yaml) resolves to a Settings.

The dev/Pi split lives entirely in *which backend* is selected per component;
the shape is identical (IMPLEMENTATION_PLAN.md §3), so the app code never
branches on profile name, only on backend kind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class MockCameraConfig(BaseModel):
    fixtures_dir: Path = Path("fixtures/shots")
    trigger_delay_ms: int = 250
    thumb_latency_ms: int = 150
    full_download_mbps: float = 40.0
    # Fault injection (IMPLEMENTATION_PLAN.md §4.4) — all off by default.
    disconnect_every_n: int | None = None
    download_timeout_pct: float = 0.0
    slow_download_pct: float = 0.0


class GphotoCameraConfig(BaseModel):
    jpeg_size: Literal["S", "M", "L"] = "S"


class CameraConfig(BaseModel):
    backend: Literal["mock", "gphoto"] = "mock"
    worker_port: int = 8765
    mock: MockCameraConfig = MockCameraConfig()
    gphoto: GphotoCameraConfig = GphotoCameraConfig()


class PreviewConfig(BaseModel):
    stream_url: str
    connect_timeout_s: float = 5.0


class NullPrinterConfig(BaseModel):
    output_dir: Path = Path("out/prints")
    simulated_job_seconds: int = 13


class CupsConfig(BaseModel):
    printer_name: str = ""
    print_limit_per_session: int = 2


class PrintingConfig(BaseModel):
    backend: Literal["null", "cups"] | None = "null"
    null_backend: NullPrinterConfig = NullPrinterConfig()
    cups: CupsConfig = CupsConfig()


class LocalDeliveryConfig(BaseModel):
    output_dir: Path = Path("out/uploads")


class SftpDeliveryConfig(BaseModel):
    host: str = ""
    remote_path: str = ""


class DeliveryConfig(BaseModel):
    backend: Literal["local", "sftp", "s3"] = "local"
    local: LocalDeliveryConfig = LocalDeliveryConfig()
    sftp: SftpDeliveryConfig = SftpDeliveryConfig()


class StorageConfig(BaseModel):
    sqlite_path: Path = Path("out/photobooth.db")


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class Settings(BaseModel):
    profile: Literal["dev", "pi"]
    camera: CameraConfig
    preview: PreviewConfig
    printing: PrintingConfig
    delivery: DeliveryConfig
    storage: StorageConfig
    web: WebConfig

    @classmethod
    def load(cls, path: Path) -> Settings:
        data = yaml.safe_load(path.read_text())
        return cls.model_validate(data)
