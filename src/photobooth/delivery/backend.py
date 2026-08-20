"""Upload backend interface — LocalDir / Sftp / S3 behind one interface,
each with a persistent retry queue (SQLite-backed, storage/queue.py) so a
failed upload retries rather than vanishing. See
IMPLEMENTATION_PLAN.md T-4.1/T-4.2.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class DeliveryBackend(ABC):
    @abstractmethod
    async def upload(self, local_path: Path, remote_key: str) -> str:
        """Upload a file, returning a guest-facing URL/token."""


class LocalDirBackend(DeliveryBackend):
    def __init__(self, output_dir: Path) -> None:
        self._output_dir = output_dir

    async def upload(self, local_path: Path, remote_key: str) -> str:
        raise NotImplementedError("T-4.2: LocalDir/Sftp/S3 behind one interface")
