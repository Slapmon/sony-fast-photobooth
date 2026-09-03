"""Tests for delivery/backend.py — LocalDir/Sftp/S3 upload backends behind
one interface (IMPLEMENTATION_PLAN.md T-4.2).

SFTP and S3 backends are tested against mocked transports (monkeypatched
`paramiko`/`boto3` clients), since this dev environment can't reach a real
SFTP/S3 endpoint — the goal is proving the upload call is shaped correctly
and that errors propagate as exceptions (so storage/queue.py's `fail()`
catches them) rather than being silently swallowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from photobooth.config.models import DeliveryConfig, S3DeliveryConfig, SftpDeliveryConfig
from photobooth.delivery.backend import (
    LocalDirBackend,
    S3Backend,
    SftpBackend,
    build_delivery_backend,
)


async def test_local_dir_backend_copies_file_and_returns_url(tmp_path: Path) -> None:
    src = tmp_path / "src" / "abc123.jpg"
    src.parent.mkdir()
    src.write_bytes(b"fake jpeg bytes")

    output_dir = tmp_path / "uploads"
    backend = LocalDirBackend(output_dir)

    url = await backend.upload(src, "abc123.jpg")

    dest = output_dir / "abc123.jpg"
    assert dest.exists()
    assert dest.read_bytes() == b"fake jpeg bytes"
    assert url == "/uploads/abc123.jpg"


async def test_local_dir_backend_creates_output_dir_if_missing(tmp_path: Path) -> None:
    src = tmp_path / "src.jpg"
    src.write_bytes(b"x")
    output_dir = tmp_path / "does" / "not" / "exist"
    backend = LocalDirBackend(output_dir)

    await backend.upload(src, "nested/key.jpg")

    assert (output_dir / "nested" / "key.jpg").exists()


def test_build_delivery_backend_selects_local() -> None:
    config = DeliveryConfig(backend="local")
    backend = build_delivery_backend(config)
    assert isinstance(backend, LocalDirBackend)


def test_build_delivery_backend_selects_sftp() -> None:
    config = DeliveryConfig(backend="sftp", sftp=SftpDeliveryConfig(host="example.com"))
    backend = build_delivery_backend(config)
    assert isinstance(backend, SftpBackend)


def test_build_delivery_backend_selects_s3() -> None:
    config = DeliveryConfig(backend="s3", s3=S3DeliveryConfig(bucket="my-bucket"))
    backend = build_delivery_backend(config)
    assert isinstance(backend, S3Backend)


async def test_sftp_backend_uploads_via_paramiko(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_path = tmp_path / "photo.jpg"
    local_path.write_bytes(b"data")

    fake_sftp_client = MagicMock()
    fake_sftp_client.stat.side_effect = OSError("no such file")

    fake_transport = MagicMock()

    import photobooth.delivery.backend as backend_module

    fake_paramiko = MagicMock()
    fake_paramiko.Transport.return_value = fake_transport
    fake_paramiko.SFTPClient.from_transport.return_value = fake_sftp_client
    monkeypatch.setitem(__import__("sys").modules, "paramiko", fake_paramiko)

    config = DeliveryConfig(
        backend="sftp",
        sftp=SftpDeliveryConfig(
            host="sftp.example.com", port=22, username="booth", password="secret",
            remote_path="/uploads",
        ),
    )
    backend = backend_module.SftpBackend(config)

    url = await backend.upload(local_path, "abc123.jpg")

    fake_paramiko.Transport.assert_called_once_with(("sftp.example.com", 22))
    fake_transport.connect.assert_called_once_with(username="booth", password="secret")
    fake_sftp_client.put.assert_called_once_with(str(local_path), "/uploads/abc123.jpg")
    fake_sftp_client.close.assert_called_once()
    fake_transport.close.assert_called_once()
    assert url == "sftp://sftp.example.com/uploads/abc123.jpg"


async def test_sftp_backend_propagates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_path = tmp_path / "photo.jpg"
    local_path.write_bytes(b"data")

    import photobooth.delivery.backend as backend_module

    fake_paramiko = MagicMock()
    fake_paramiko.Transport.side_effect = OSError("connection refused")
    monkeypatch.setitem(__import__("sys").modules, "paramiko", fake_paramiko)

    config = DeliveryConfig(
        backend="sftp",
        sftp=SftpDeliveryConfig(host="unreachable.example.com", username="booth"),
    )
    backend = backend_module.SftpBackend(config)

    with pytest.raises(OSError, match="connection refused"):
        await backend.upload(local_path, "abc123.jpg")


async def test_s3_backend_uploads_via_boto3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_path = tmp_path / "photo.jpg"
    local_path.write_bytes(b"data")

    fake_client = MagicMock()
    fake_boto3: Any = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    config = DeliveryConfig(
        backend="s3",
        s3=S3DeliveryConfig(bucket="my-bucket", region="eu-central-1"),
    )
    import photobooth.delivery.backend as backend_module

    backend = backend_module.S3Backend(config)

    url = await backend.upload(local_path, "abc123.jpg")

    fake_boto3.client.assert_called_once_with("s3", region_name="eu-central-1")
    fake_client.upload_file.assert_called_once_with(str(local_path), "my-bucket", "abc123.jpg")
    assert url == "https://my-bucket.s3.eu-central-1.amazonaws.com/abc123.jpg"


async def test_s3_backend_uses_endpoint_url_for_s3_compatible_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_path = tmp_path / "photo.jpg"
    local_path.write_bytes(b"data")

    fake_client = MagicMock()
    fake_boto3: Any = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    config = DeliveryConfig(
        backend="s3",
        s3=S3DeliveryConfig(
            bucket="my-bucket", endpoint_url="https://r2.example.com", prefix="events/wedding"
        ),
    )
    import photobooth.delivery.backend as backend_module

    backend = backend_module.S3Backend(config)

    url = await backend.upload(local_path, "abc123.jpg")

    fake_client.upload_file.assert_called_once_with(
        str(local_path), "my-bucket", "events/wedding/abc123.jpg"
    )
    assert url == "https://r2.example.com/my-bucket/events/wedding/abc123.jpg"


async def test_s3_backend_propagates_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_path = tmp_path / "photo.jpg"
    local_path.write_bytes(b"data")

    fake_client = MagicMock()
    fake_client.upload_file.side_effect = RuntimeError("bucket not found")
    fake_boto3: Any = MagicMock()
    fake_boto3.client.return_value = fake_client
    monkeypatch.setitem(__import__("sys").modules, "boto3", fake_boto3)

    config = DeliveryConfig(backend="s3", s3=S3DeliveryConfig(bucket="missing-bucket"))
    import photobooth.delivery.backend as backend_module

    backend = backend_module.S3Backend(config)

    with pytest.raises(RuntimeError, match="bucket not found"):
        await backend.upload(local_path, "abc123.jpg")
