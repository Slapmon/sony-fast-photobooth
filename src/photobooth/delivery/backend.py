"""Upload backend interface — LocalDir / Sftp / S3 behind one interface,
each with a persistent retry queue (SQLite-backed, storage/queue.py) so a
failed upload retries rather than vanishing. See
IMPLEMENTATION_PLAN.md T-4.1/T-4.2.

Async strategy: `paramiko` (SFTP) and `boto3` (S3) are both synchronous,
blocking libraries. Rather than adding an async-native SFTP client
(`asyncssh`) as a second dependency alongside the already-present
`paramiko`/`boto3` (see pyproject.toml's `delivery` extra, added ahead of
this task), both backends wrap their blocking calls with
`asyncio.to_thread()` — the same "keep blocking work off the event loop"
convention `pipeline/pool.py`'s `RenderPool` and `camera/worker.py`'s
subprocess boundary already establish in this codebase. `boto3` in
particular has no well-established async-native equivalent (`aioboto3` wraps
`aiobotocore`, itself a shim over `botocore`, and would be a heavier, less
battle-tested dependency for marginal benefit over a thread hop). One
`asyncio.to_thread()` call per upload is simple, needs no extra pool
bookkeeping (the default loop executor already bounds concurrency sanely for
a single booth's upload volume), and keeps both backends symmetric.
"""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from photobooth.config.models import DeliveryConfig, SftpDeliveryConfig

if TYPE_CHECKING:
    import paramiko

logger = structlog.get_logger(__name__)


class DeliveryBackend(ABC):
    """Uploads one local file, returning a guest-facing URL/token.

    Kept to a single `str` return (the URL) rather than a richer structured
    result: every backend either returns a usable URL or raises — there is
    no "partially delivered" state worth modeling here, and the caller
    (`delivery/worker.py`'s job handler) only ever needs the URL to record
    against the capture. Raising is deliberate: `storage/queue.py`'s
    `run_worker()` catches any exception from the handler and routes it to
    `JobQueue.fail()`'s backoff/dead-letter logic (T-4.4) — a backend that
    swallowed its own errors would silently break retry.
    """

    @abstractmethod
    async def upload(self, local_path: Path, remote_key: str) -> str:
        """Upload a file, returning a guest-facing URL/token."""


class LocalDirBackend(DeliveryBackend):
    """Copies into a local directory rather than uploading anywhere.

    Why this is still meaningful even though `web/app.py` already serves
    captures at `/captures/*`: `LocalDirBackend` is not a shortcut for "the
    file is already there" — it's the "no real upload target" backend, used
    for (a) local dev/testing of the queue + retry machinery without needing
    real SFTP/S3 credentials, and (b) a venue with no configured delivery
    target at all, where "delivery" degrades gracefully to "the file lives
    in a known place with a stable URL" instead of erroring or silently
    doing nothing. Making it a real (if trivial) copy — rather than a
    no-op that always "succeeds" immediately — keeps its behavior
    consistent with the other backends: it exercises the actual file I/O
    path, so a bug in how `local_path` is read wouldn't be masked by this
    backend alone.

    The returned URL assumes a later wave mounts `output_dir` at `/uploads`
    in `web/app.py`, the same way `CAPTURES_DIR` is mounted at `/captures`
    today (see this module's "Integration notes" — duplicated in the task
    report for the wave that owns `web/app.py`).
    """

    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def upload(self, local_path: Path, remote_key: str) -> str:
        def _copy() -> None:
            dest = self._output_dir / remote_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, dest)

        await asyncio.to_thread(_copy)
        return f"/uploads/{remote_key}"


class SftpBackend(DeliveryBackend):
    """Real SFTP upload via `paramiko`, wrapped off the event loop.

    A fresh SSH+SFTP connection is opened per `upload()` call rather than
    held open across calls: uploads are infrequent (one per capture, seconds
    apart at most) and a booth can sit idle for long stretches between
    guests, so a long-lived connection would mostly just be something to
    detect staleness on and reconnect anyway. Simpler to open, use, close.
    """

    def __init__(self, config: DeliveryConfig) -> None:
        self._config = config.sftp

    def _upload_sync(self, local_path: Path, remote_key: str) -> None:
        cfg = self._config
        transport, sftp = _open_sftp(cfg)
        try:
            remote_path = f"{cfg.remote_path.rstrip('/')}/{remote_key}"
            # Ensure the remote directory exists — mkdir per missing
            # path segment, ignoring "already exists" since SFTP has no
            # mkdir -p.
            remote_dir = remote_path.rsplit("/", 1)[0]
            _sftp_makedirs(sftp, remote_dir)
            sftp.put(str(local_path), remote_path)
        finally:
            sftp.close()
            transport.close()

    async def upload(self, local_path: Path, remote_key: str) -> str:
        await asyncio.to_thread(self._upload_sync, local_path, remote_key)
        cfg = self._config
        remote_path = f"{cfg.remote_path.rstrip('/')}/{remote_key}"
        return f"sftp://{cfg.host}{remote_path}"


def _load_private_key(path: Path) -> paramiko.PKey:
    """Try every key type paramiko supports, in rough order of how common
    each is for a freshly generated "upload account" key today (Ed25519 is
    the modern default from `ssh-keygen`, RSA the long-standing one; ECDSA
    rarer but cheap to also try). Raises the last error if none load — a
    single hardcoded `RSAKey.from_private_key_file` (the previous behavior)
    would silently fail for anyone who generated an Ed25519 key, which is
    now the common case.

    `DSSKey` (DSA) is deliberately NOT in this list: DSA is obsolete/
    insecure, and paramiko 5.0+ removed the class entirely — a hardcoded
    reference to `paramiko.DSSKey` raised `AttributeError` on any
    environment running that version rather than falling through cleanly.
    `getattr` here means this also works fine on an older paramiko that
    still has it, without hardcoding a version check.
    """
    import paramiko

    key_classes = [paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey]
    dss_key_class = getattr(paramiko, "DSSKey", None)
    if dss_key_class is not None:
        key_classes.append(dss_key_class)

    last_error: Exception | None = None
    for key_class in key_classes:
        try:
            return key_class.from_private_key_file(str(path))
        except paramiko.SSHException as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _open_sftp(cfg: SftpDeliveryConfig) -> tuple[paramiko.Transport, paramiko.SFTPClient]:
    """Shared connect+auth routine for both a real upload (`SftpBackend`)
    and the admin panel's "Test Connection" check (`test_sftp_connection`)
    — one place to get auth right, matching either code path exactly."""
    import paramiko

    transport = paramiko.Transport((cfg.host, cfg.port))
    try:
        if cfg.private_key_path:
            pkey = _load_private_key(cfg.private_key_path)
            transport.connect(username=cfg.username, pkey=pkey)
        else:
            transport.connect(username=cfg.username, password=cfg.password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            raise OSError(f"could not open SFTP session to {cfg.host}")
    except Exception:
        transport.close()
        raise
    return transport, sftp


def test_sftp_connection(cfg: SftpDeliveryConfig) -> None:
    """Connects, authenticates, and confirms `cfg.remote_path` is reachable
    and writable (via the same `_sftp_makedirs` the real upload uses) —
    proves more than "auth succeeded," since a wrong remote path is just as
    common a misconfiguration as a wrong password. Raises on any failure;
    returning normally IS the success signal (admin.py's route wraps this
    in try/except and reports ok/error to the operator)."""
    transport, sftp = _open_sftp(cfg)
    try:
        _sftp_makedirs(sftp, cfg.remote_path)
    finally:
        sftp.close()
        transport.close()


def _sftp_makedirs(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    """`mkdir -p` over SFTP — paramiko has no built-in equivalent."""
    if not remote_dir or remote_dir == "/":
        return
    parts = remote_dir.strip("/").split("/")
    path = ""
    for part in parts:
        path += f"/{part}"
        try:
            sftp.stat(path)
        except OSError:
            sftp.mkdir(path)


class S3Backend(DeliveryBackend):
    """Real S3-compatible upload via `boto3`, wrapped off the event loop.

    A fresh client is constructed per `upload()` call for the same reason as
    `SftpBackend` — simplicity over connection reuse for a low-volume,
    bursty workload. `boto3` clients are cheap to construct (no network
    round-trip at construction time).
    """

    def __init__(self, config: DeliveryConfig) -> None:
        self._config = config.s3

    def _upload_sync(self, local_path: Path, remote_key: str) -> str:
        import boto3

        cfg = self._config
        client_kwargs: dict[str, object] = {}
        if cfg.region:
            client_kwargs["region_name"] = cfg.region
        if cfg.endpoint_url:
            client_kwargs["endpoint_url"] = cfg.endpoint_url
        if cfg.access_key_id:
            client_kwargs["aws_access_key_id"] = cfg.access_key_id
        if cfg.secret_access_key:
            client_kwargs["aws_secret_access_key"] = cfg.secret_access_key

        client = boto3.client("s3", **client_kwargs)
        key = f"{cfg.prefix.rstrip('/')}/{remote_key}" if cfg.prefix else remote_key
        client.upload_file(str(local_path), cfg.bucket, key)
        if cfg.endpoint_url:
            return f"{cfg.endpoint_url.rstrip('/')}/{cfg.bucket}/{key}"
        region_part = f".{cfg.region}" if cfg.region else ""
        return f"https://{cfg.bucket}.s3{region_part}.amazonaws.com/{key}"

    async def upload(self, local_path: Path, remote_key: str) -> str:
        return await asyncio.to_thread(self._upload_sync, local_path, remote_key)


def build_delivery_backend(config: DeliveryConfig) -> DeliveryBackend:
    """Select a `DeliveryBackend` by `config.backend`, matching the pattern
    `camera/worker.py`'s `_build_backend()` uses for camera backend
    selection."""
    if config.backend == "local":
        return LocalDirBackend(config.local.output_dir)
    if config.backend == "sftp":
        return SftpBackend(config)
    return S3Backend(config)
