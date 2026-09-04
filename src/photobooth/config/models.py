"""Profile config models — one file (dev.yaml / pi.yaml) resolves to a Settings.

The dev/Pi split lives entirely in *which backend* is selected per component;
the shape is identical (IMPLEMENTATION_PLAN.md §3), so the app code never
branches on profile name, only on backend kind.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Literal

import structlog
import yaml
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

# Loud, obviously-wrong default PIN. Never mistaken for a real one, so a
# forgotten override is immediately visible in config review — and, on the
# `pi` profile specifically, Settings.load logs a warning at startup if this
# is still what's active (T-3.7).
_DEFAULT_PIN = "changeme"


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
    # Fault injection (IMPLEMENTATION_PLAN.md §4.4), matching how
    # MockCameraConfig exposes fault-injection knobs — off by default. When
    # true, NullPrinter.status() reports "red"/out-of-media and submit()
    # raises PrinterOfflineError, so the print-button-gating logic (T-4.8)
    # and reprint/dead-letter paths can be exercised without real hardware.
    simulate_out_of_media: bool = False


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
    port: int = 22
    username: str = ""
    # Plain config fields, not a secrets manager (IMPLEMENTATION_PLAN.md T-4.2
    # note) — this is a single-operator booth app, matching how AdminConfig.pin
    # is handled above. Prefer `private_key_path` over `password` where
    # possible; both are optional so key-based auth (the common case for a
    # dedicated upload account) needs no password at all.
    password: str = ""
    private_key_path: Path | None = None
    remote_path: str = ""


class S3DeliveryConfig(BaseModel):
    bucket: str = ""
    region: str = ""
    # Set for S3-compatible non-AWS targets (Hetzner, Cloudflare R2, Backblaze
    # B2 — see photobooth-plan.md §11's EU-hosting guidance). Left blank to
    # use AWS's regional default endpoint.
    endpoint_url: str = ""
    # Plain config fields, same rationale as SftpDeliveryConfig above. Left
    # blank to fall back to boto3's normal credential chain (env vars,
    # ~/.aws/credentials, instance profile) if the operator prefers that.
    access_key_id: str = ""
    secret_access_key: str = ""
    prefix: str = ""


class DeliveryConfig(BaseModel):
    backend: Literal["local", "sftp", "s3"] = "local"
    local: LocalDeliveryConfig = LocalDeliveryConfig()
    sftp: SftpDeliveryConfig = SftpDeliveryConfig()
    s3: S3DeliveryConfig = S3DeliveryConfig()
    # Overrides the host the share-link QR code (web/routers/share.py)
    # points guests at. Blank (default): the QR uses whatever host/port the
    # kiosk's own browser request came in on — fine for LAN-only testing,
    # but means a guest's phone must join the venue Wi-Fi to open it. Once
    # a real delivery target is configured (e.g. SFTP to a public server),
    # set this to that server's public URL (e.g. "https://photos.example.com")
    # so the QR sends guests straight there over their own mobile data —
    # no booth Wi-Fi required. Purely a URL prefix for /s/{token}; it does
    # not change where captures are actually uploaded (that's `backend`
    # above) or require the target to run this app itself.
    public_base_url: str = ""


class StorageConfig(BaseModel):
    sqlite_path: Path = Path("out/photobooth.db")


class WebConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class EventsConfig(BaseModel):
    base_dir: Path = Path("events")
    # "dev" matches web/session.py's current hardcoded placeholder (T-2.7's
    # docstring there) — kept as the literal default so nothing breaks until
    # a later wave rewires session.py to actually load through this.
    active_event_id: str = "dev"


class KioskConfig(BaseModel):
    """Attract-loop / idle-timeout knobs (IMPLEMENTATION_PLAN.md T-3.3)."""

    # How long the guest flow waits with no pointer/touch/key activity
    # before returning to the attract loop. The attract loop itself is
    # excluded from this timeout - it IS the idle state, not something to
    # time out of.
    idle_timeout_s: float = 60.0


class AdminConfig(BaseModel):
    """Shared-PIN admin auth (IMPLEMENTATION_PLAN.md T-3.7,
    photobooth-plan.md §7). Single booth, single operator role — no user
    accounts, no password hashing library needed for a numeric PIN check
    (it's still compared with `hmac.compare_digest` to avoid trivial timing
    attacks, see web/routers/admin_auth.py).

    `pin` defaults to the obviously-fake `_DEFAULT_PIN` rather than raising
    on a blank config, so `just dev` keeps working out of the box; dev.yaml
    sets a real (if trivial) dev PIN, and Settings.load logs a loud warning
    if the `pi` profile is still on the default when it starts — that's the
    actual gate against going live unconfigured.

    `secret_key` signs session tokens. If left unset it's generated randomly
    at process startup (see `default_factory` below) — meaning every restart
    invalidates all outstanding admin sessions. That's an accepted trade-off
    for v1: simpler than provisioning/rotating a persisted secret, and an
    8-hour admin session being forced to re-enter the PIN after a restart is
    a non-event for a kiosk that isn't restarted mid-shift. Set it explicitly
    in config to get sessions that survive a restart.
    """

    pin: str = _DEFAULT_PIN
    secret_key: str = Field(default_factory=lambda: secrets.token_hex(32))
    session_ttl_hours: float = 8.0


class RetentionConfig(BaseModel):
    """Automatic capture deletion (IMPLEMENTATION_PLAN.md T-4.5,
    photobooth-plan.md §11's GDPR retention requirement: "automatic deletion
    after a defined window, enforced in code, not by hand").

    Off by default (`enabled=False`) so a fresh `just dev` checkout and any
    existing deployment don't suddenly start deleting captures. A real event
    deployment should turn this on explicitly. `max_age_days` defaults to the
    30-day window photobooth-plan.md §11 gives as its example — long enough
    that a couple's photos don't vanish before they've had a chance to
    download them, short enough to bound how much guest data sits around.
    """

    enabled: bool = False
    max_age_days: int = 30


class LoggingConfig(BaseModel):
    """Per-module log levels (IMPLEMENTATION_PLAN.md T-5.2).

    Log *rotation* is deliberately not configured here: this app runs under
    systemd on the Pi (T-5.1), and journald already owns rotation/retention
    for anything written to stdout/stderr — see `deploy/systemd/README.md`
    for the `journald.conf` knobs. Nothing in this codebase writes logs to a
    plain file today, so there's no existing file-based sink this config
    needs to size/rotate; if that changes, revisit this decision rather than
    silently bolting on a `RotatingFileHandler` here.

    `level` is the root/default level. `module_levels` overrides it per
    logger name, applied via the standard library `logging` module (which
    structlog sits on top of for actual output — see
    `telemetry/logging_config.py`'s `configure_logging()`), e.g.:

        module_levels:
          photobooth.camera: DEBUG
    """

    level: str = "INFO"
    module_levels: dict[str, str] = Field(default_factory=dict)


class Settings(BaseModel):
    profile: Literal["dev", "pi"]
    camera: CameraConfig
    preview: PreviewConfig
    printing: PrintingConfig
    delivery: DeliveryConfig
    storage: StorageConfig
    web: WebConfig
    events: EventsConfig = EventsConfig()
    kiosk: KioskConfig = KioskConfig()
    admin: AdminConfig = AdminConfig()
    retention: RetentionConfig = RetentionConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, path: Path) -> Settings:
        data = yaml.safe_load(path.read_text())
        settings = cls.model_validate(data)
        if settings.profile == "pi" and settings.admin.pin == _DEFAULT_PIN:
            logger.warning(
                "admin_pin_is_default",
                message=(
                    "admin.pin is still the default placeholder on the pi profile — "
                    "set a real PIN in config before going live"
                ),
            )
        return settings
