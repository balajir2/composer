"""GoogleDriveProvider — FileStorageProvider implementation against the
Drive v3 REST API via direct httpx calls (no google-api-python-client —
matches this codebase's manual-HTTP-over-SDK preference, see CLAUDE.md §1
fix #2 and this feature's design doc §C).

Claim mechanism: instead of moving files (the local provider's approach),
this provider marks a claimed file with a `composerStatus` appProperty
("processed" or "error") — that mark IS the state list_new_files excludes
on future polls, permanent regardless of outcome, mirroring local's
never-re-claim semantics.
"""

from datetime import datetime

import httpx

from src.storage_providers.base import FileRef, FileStorageProvider, HealthStatus

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


class GoogleDriveProviderError(RuntimeError):
    """Raised when the Drive API rejects or cannot process a request."""


class GoogleDriveProvider(FileStorageProvider):
    name = "google-drive"

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def list_new_files(self, source: str) -> list[FileRef]:
        # Drive query language requires both key AND value inside `has{}` —
        # there is no "key present regardless of value" predicate — so
        # unclaimed means neither status value is set.
        query = (
            f"'{source}' in parents and trashed = false "
            "and not appProperties has { key='composerStatus' and value='processed' } "
            "and not appProperties has { key='composerStatus' and value='error' }"
        )
        params = {"q": query, "fields": "files(id,name,size,modifiedTime)", "pageSize": "20"}
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE,
            headers=self._headers(),
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            resp = await client.get("/files", params=params)
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"Drive list failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        files = resp.json().get("files", [])
        return [
            FileRef(
                identifier=f["id"],
                name=f["name"],
                size_bytes=int(f.get("size", 0)),
                modified_at=datetime.fromisoformat(f["modifiedTime"].replace("Z", "+00:00")),
            )
            for f in files
        ]

    async def read_file(self, ref: FileRef) -> bytes:
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE,
            headers=self._headers(),
            timeout=httpx.Timeout(60.0, connect=5.0),
        ) as client:
            resp = await client.get(f"/files/{ref.identifier}", params={"alt": "media"})
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"Drive read failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp.content

    async def move_file(self, ref: FileRef, dest: str) -> None:
        """Repurposed for this provider: `dest` is NOT a path/folder to move
        the file into. It is the `composerStatus` appProperty value to set
        ("processed" or "error") — this provider's claim mechanism is an
        in-place marker, not a relocation. Task 8 (the polling endpoint)
        calls this with dest="processed" on success or dest="error" on
        failure; the file stays in its original Drive folder either way."""
        async with httpx.AsyncClient(
            base_url=DRIVE_API_BASE,
            headers=self._headers(),
            timeout=httpx.Timeout(30.0, connect=5.0),
        ) as client:
            resp = await client.patch(
                f"/files/{ref.identifier}", json={"appProperties": {"composerStatus": dest}}
            )
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"Drive mark-processed failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        raise NotImplementedError(
            "GoogleDriveProvider is trigger-only; unrelated to the file-write node"
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="no health check defined")


__all__ = ["GoogleDriveProvider", "GoogleDriveProviderError"]
