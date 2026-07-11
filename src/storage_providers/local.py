"""Local-filesystem FileStorageProvider — polling-based, partial-write safe."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from src.storage_providers.base import FileRef, FileStorageProvider, HealthStatus


class LocalFilesystemProvider(FileStorageProvider):
    name = "local"

    def __init__(self) -> None:
        # {absolute_path: (size_bytes, mtime)} seen on the previous poll of
        # each source directory. A file must appear identical across two
        # consecutive list_new_files() calls before it's returned — guards
        # against claiming a file that's still being copied/written.
        self._seen: dict[str, tuple[int, float]] = {}

    async def list_new_files(self, source: str) -> list[FileRef]:
        source_path = Path(source)
        if not source_path.is_dir():
            return []

        stable: list[FileRef] = []
        current_snapshot: dict[str, tuple[int, float]] = {}
        for entry in source_path.iterdir():
            if not entry.is_file():
                continue
            stat = entry.stat()
            key = str(entry.resolve())
            current_snapshot[key] = (stat.st_size, stat.st_mtime)
            previous = self._seen.get(key)
            if previous is not None and previous == current_snapshot[key]:
                stable.append(
                    FileRef(
                        identifier=key,
                        name=entry.name,
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                    )
                )

        self._seen = current_snapshot
        return stable

    async def read_file(self, ref: FileRef) -> bytes:
        return Path(ref.identifier).read_bytes()

    async def move_file(self, ref: FileRef, dest: str) -> None:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(ref.identifier, str(dest_dir / ref.name))
        self._seen.pop(ref.identifier, None)

    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        dest_dir = Path(dest)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / filename
        target.write_bytes(content)
        # Files written through this provider are known-complete the moment
        # write_file returns (unlike externally-dropped files, which may
        # still be mid-copy) — so pre-seed _seen with their post-write stat.
        # A single subsequent list_new_files() poll then confirms stability
        # immediately, instead of requiring two full polling cycles.
        stat = target.stat()
        self._seen[str(target.resolve())] = (stat.st_size, stat.st_mtime)

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="local filesystem — always available")


__all__ = ["LocalFilesystemProvider"]
