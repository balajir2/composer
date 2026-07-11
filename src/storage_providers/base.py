"""FileStorageProvider ABC — modeled on src/tools/base.py's ToolProvider."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, kw_only=True)
class FileRef:
    """Opaque handle a provider hands back — identifier semantics are
    provider-specific (local: absolute path; s3 future: object key)."""

    identifier: str
    name: str
    size_bytes: int
    modified_at: datetime


@dataclass(frozen=True, kw_only=True)
class HealthStatus:
    ok: bool
    message: str


class FileStorageProvider(ABC):
    name: str

    @abstractmethod
    async def list_new_files(self, source: str) -> list[FileRef]:
        """Return files ready to be claimed — implementations decide their
        own 'ready' criteria (e.g. local: stable across two polls)."""

    @abstractmethod
    async def read_file(self, ref: FileRef) -> bytes: ...

    @abstractmethod
    async def move_file(self, ref: FileRef, dest: str) -> None: ...

    @abstractmethod
    async def write_file(self, dest: str, filename: str, content: bytes) -> None: ...

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="no health check defined")


__all__ = ["FileRef", "FileStorageProvider", "HealthStatus"]
