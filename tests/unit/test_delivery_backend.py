"""Tests for delivery/backend.py — LocalDir/Sftp/S3 upload backends behind
one interface (IMPLEMENTATION_PLAN.md T-4.2).

SFTP and S3 backends are tested against mocked transports (monkeypatched
`paramiko`/`boto3` clients), since this dev environment can't reach a real
SFTP/S3 endpoint — the goal is proving the upload call is shaped correctly
and that errors propagate as exceptions (so storage/queue.py's `fail()`
catches them) rather than being silently swallowed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import paramiko as real_paramiko
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


# ---------------------------------------------------------------------------
# test_sftp_connection + _load_private_key (admin panel "Test Connection")
# ---------------------------------------------------------------------------


def test_sftp_connection_succeeds_when_remote_path_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_sftp_client = MagicMock()
    fake_sftp_client.stat.return_value = MagicMock()  # remote_path already exists
    fake_transport = MagicMock()
    fake_paramiko = MagicMock()
    fake_paramiko.Transport.return_value = fake_transport
    fake_paramiko.SFTPClient.from_transport.return_value = fake_sftp_client
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    cfg = SftpDeliveryConfig(
        host="sftp.example.com", username="booth", password="secret", remote_path="/uploads"
    )

    backend_module.test_sftp_connection(cfg)  # must not raise

    fake_transport.connect.assert_called_once_with(username="booth", password="secret")
    fake_sftp_client.close.assert_called_once()
    fake_transport.close.assert_called_once()


def test_sftp_connection_raises_on_connect_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paramiko = MagicMock()
    fake_paramiko.Transport.side_effect = OSError("connection refused")
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    cfg = SftpDeliveryConfig(host="unreachable.example.com", username="booth")

    with pytest.raises(OSError, match="connection refused"):
        backend_module.test_sftp_connection(cfg)


def test_sftp_falls_back_to_keyboard_interactive_when_password_auth_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real report: many SFTP hosts (shared
    hosting/cPanel-style accounts especially) only accept
    keyboard-interactive auth, not plain "password" — paramiko's
    `Transport.connect()` shortcut only ever tries one method, so a
    perfectly correct password against one of these hosts surfaces as
    `AuthenticationException` here. `_authenticate()` must retry the same
    password via `auth_interactive` on the SAME transport before giving up.
    """
    fake_sftp_client = MagicMock()
    fake_transport = MagicMock()
    fake_transport.connect.side_effect = real_paramiko.AuthenticationException(
        "authentication failed"
    )
    fake_paramiko = MagicMock()
    fake_paramiko.AuthenticationException = real_paramiko.AuthenticationException
    fake_paramiko.Transport.return_value = fake_transport
    fake_paramiko.SFTPClient.from_transport.return_value = fake_sftp_client
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    cfg = SftpDeliveryConfig(host="sftp.example.com", username="booth", password="secret")

    backend_module.test_sftp_connection(cfg)  # must not raise

    fake_transport.connect.assert_called_once_with(username="booth", password="secret")
    fake_transport.auth_interactive.assert_called_once()
    call_args = fake_transport.auth_interactive.call_args
    assert call_args[0][0] == "booth"
    handler = call_args[0][1]
    assert handler("title", "instructions", [("Password:", False)]) == ["secret"]


def test_sftp_raises_when_keyboard_interactive_fallback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_transport = MagicMock()
    fake_transport.connect.side_effect = real_paramiko.AuthenticationException(
        "authentication failed"
    )
    fake_transport.auth_interactive.side_effect = real_paramiko.AuthenticationException(
        "authentication failed"
    )
    fake_paramiko = MagicMock()
    fake_paramiko.AuthenticationException = real_paramiko.AuthenticationException
    fake_paramiko.Transport.return_value = fake_transport
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    cfg = SftpDeliveryConfig(host="sftp.example.com", username="booth", password="wrong")

    with pytest.raises(real_paramiko.AuthenticationException):
        backend_module.test_sftp_connection(cfg)


def test_load_private_key_falls_back_through_key_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paramiko = MagicMock()
    fake_paramiko.SSHException = real_paramiko.SSHException
    fake_paramiko.Ed25519Key.from_private_key_file.side_effect = real_paramiko.SSHException(
        "not ed25519"
    )
    fake_paramiko.ECDSAKey.from_private_key_file.side_effect = real_paramiko.SSHException(
        "not ecdsa"
    )
    fake_rsa_key = MagicMock()
    fake_paramiko.RSAKey.from_private_key_file.return_value = fake_rsa_key
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    key_path = tmp_path / "id_rsa"
    key_path.write_text("fake key material")

    result = backend_module._load_private_key(key_path)

    assert result is fake_rsa_key
    fake_paramiko.DSSKey.from_private_key_file.assert_not_called()


def test_load_private_key_works_when_paramiko_has_no_dsskey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the real bug this shipped with: paramiko 5.0+
    removed `DSSKey` entirely (DSA is obsolete/insecure) — a hardcoded
    `paramiko.DSSKey` reference in the key-type fallback list raised
    `AttributeError` on that version instead of falling through to a key
    type that actually exists. `spec=[...]` here (unlike the plain
    `MagicMock()` above) means accessing `.DSSKey` genuinely raises
    `AttributeError`, matching real paramiko 5.0+.
    """
    fake_paramiko = MagicMock(spec=["SSHException", "Ed25519Key", "ECDSAKey", "RSAKey"])
    fake_paramiko.SSHException = real_paramiko.SSHException
    fake_paramiko.Ed25519Key.from_private_key_file.side_effect = real_paramiko.SSHException(
        "not ed25519"
    )
    fake_paramiko.ECDSAKey.from_private_key_file.side_effect = real_paramiko.SSHException(
        "not ecdsa"
    )
    fake_rsa_key = MagicMock()
    fake_paramiko.RSAKey.from_private_key_file.return_value = fake_rsa_key
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    key_path = tmp_path / "id_rsa"
    key_path.write_text("fake key material")

    result = backend_module._load_private_key(key_path)

    assert result is fake_rsa_key


def test_load_private_key_raises_last_error_when_no_type_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paramiko = MagicMock()
    fake_paramiko.SSHException = real_paramiko.SSHException
    for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey"):
        getattr(fake_paramiko, name).from_private_key_file.side_effect = (
            real_paramiko.SSHException(f"not {name}")
        )
    monkeypatch.setitem(sys.modules, "paramiko", fake_paramiko)

    import photobooth.delivery.backend as backend_module

    key_path = tmp_path / "id_bad"
    key_path.write_text("garbage")

    with pytest.raises(real_paramiko.SSHException, match="not DSSKey"):
        backend_module._load_private_key(key_path)


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
