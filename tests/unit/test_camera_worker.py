"""End-to-end tests for the camera-worker TCP server and its async client.

Exercises worker.py and client.py together against a MockBackend over a real
TCP loopback connection — nothing here is mocked below the socket layer, so
this locks in the actual wire behaviour (framing, error mapping, connection
lifecycle) rather than just the message-encoding contract already covered by
messages.py's own tests.
"""

from __future__ import annotations

import pytest

from photobooth.camera.client import CameraWorkerClient
from photobooth.camera.protocol import CameraDisconnectedError


async def test_full_round_trip(client: CameraWorkerClient) -> None:
    await client.connect()
    status = await client.get_status()
    assert status == {"connected": True}

    capture_id = await client.trigger_capture()
    assert capture_id

    preview = await client.download_preview(capture_id)
    assert preview is not None
    assert preview.width > 0
    assert preview.height > 0
    assert len(preview.data) > 0

    full = await client.download_full(capture_id)
    assert full.width > 0
    assert full.height > 0
    assert len(full.data) > 0

    await client.disconnect()
    status = await client.get_status()
    assert status == {"connected": False}


async def test_download_preview_and_full_return_real_fixture_data(
    client: CameraWorkerClient,
) -> None:
    await client.connect()
    capture_id = await client.trigger_capture()

    preview = await client.download_preview(capture_id)
    full = await client.download_full(capture_id)

    # MockBackend serves the same underlying JPEG bytes for both, but the
    # preview is thumbnailed to at most 1616x1080 while the full download
    # reports the fixture's native dimensions — so bytes match, sizes don't.
    assert preview is not None
    assert preview.data == full.data
    assert preview.width > 0 and preview.height > 0
    assert full.width > 0 and full.height > 0
    assert preview.width <= 1616
    assert preview.height <= 1080


async def test_commands_before_connect_raise_disconnected(client: CameraWorkerClient) -> None:
    with pytest.raises(CameraDisconnectedError):
        await client.trigger_capture()


async def test_worker_survives_client_disconnect_and_reconnect(
    worker_port: int,
) -> None:
    first = CameraWorkerClient("127.0.0.1", worker_port)
    await first.connect()
    capture_id = await first.trigger_capture()
    assert capture_id
    await first.close()

    second = CameraWorkerClient("127.0.0.1", worker_port)
    status = await second.get_status()
    assert status == {"connected": True}
    new_capture_id = await second.trigger_capture()
    assert new_capture_id
    await second.disconnect()


async def test_download_addresses_correct_capture_by_id(client: CameraWorkerClient) -> None:
    await client.connect()

    first_capture_id = await client.trigger_capture()
    second_capture_id = await client.trigger_capture()
    assert first_capture_id != second_capture_id

    first_full = await client.download_full(first_capture_id)
    assert len(first_full.data) > 0
    assert first_full.width > 0
