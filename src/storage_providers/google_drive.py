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
from typing import Any

import httpx

from src.storage_providers.base import FileRef, FileStorageProvider, HealthStatus

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_API_BASE = "https://www.googleapis.com/upload/drive/v3"


class GoogleDriveProviderError(RuntimeError):
    """Raised when the Drive API rejects or cannot process a request. Also
    raised (instead of a raw httpx exception) when the request never made it
    to Google at all — connection refused, DNS failure, timeout — so a
    caller written to catch just this one type doesn't miss a network blip.
    See src/integrations/google_drive/oauth.py's TokenExchangeError /
    TokenRefreshError for the same pattern applied earlier in this feature.
    """


class GoogleDriveProvider(FileStorageProvider):
    name = "google-drive"

    def __init__(
        self,
        access_token: str,
        *,
        processed_folder_id: str | None = None,
        error_folder_id: str | None = None,
    ) -> None:
        self.access_token = access_token
        # Optional visible-move destinations (mirrors the local provider's
        # dest_path/error_path) -- both None by default, matching the
        # original marker-only behavior for existing node configs that
        # never set these fields.
        self.processed_folder_id = processed_folder_id
        self.error_folder_id = error_folder_id

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        error_prefix: str,
        timeout: httpx.Timeout,
        base_url: str = DRIVE_API_BASE,
        **kwargs: Any,
    ) -> httpx.Response:
        """Single choke point for all Drive API calls. Owns AsyncClient
        construction (one client per call — this provider is expected to be
        short-lived, constructed per already-resolved access token by the
        polling endpoint, so pooling a client across the provider's
        lifetime would add lifecycle-management complexity for little
        benefit here) and wraps both transport-level failures and
        HTTP-error responses into GoogleDriveProviderError.

        `base_url` defaults to the metadata API (DRIVE_API_BASE) that every
        existing call site relies on implicitly; write_file's content-upload
        call overrides it to UPLOAD_API_BASE, since uploadType=media/multipart
        endpoints live under /upload/drive/v3/, not /drive/v3/."""
        try:
            async with httpx.AsyncClient(
                base_url=base_url, headers=self._headers(), timeout=timeout
            ) as client:
                resp = await client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise GoogleDriveProviderError(f"{error_prefix} (request error): {exc}") from exc
        if resp.status_code >= 400:
            raise GoogleDriveProviderError(
                f"{error_prefix} (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return resp

    async def list_new_files(self, source: str) -> list[FileRef]:
        # Drive query language requires both key AND value inside `has{}` —
        # there is no "key present regardless of value" predicate — so
        # unclaimed means neither status value is set.
        #
        # Escape stray `'` in source (the folder id) before interpolating —
        # driveFolderId is stored as plain workflow JSON editable via the
        # Workflow CRUD API (not exclusively set via the trusted Google
        # Picker flow), so an unescaped quote could break out of the query
        # string literal. Drive query language escapes `'` as `\'`.
        #
        # Backslash MUST be escaped before quote, not after: escaping quote
        # first can't distinguish an original backslash from one just
        # inserted by that same replace, so a source ending in an odd
        # number of backslashes (e.g. "folder123\") would neutralize the
        # quote-escaping and corrupt the trailing `not appProperties
        # has {...}` exclusion clauses that implement never-re-claim.
        escaped_source = source.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"'{escaped_source}' in parents and trashed = false "
            "and mimeType != 'application/vnd.google-apps.folder' "
            "and not appProperties has { key='composerStatus' and value='processed' } "
            "and not appProperties has { key='composerStatus' and value='error' }"
        )
        params = {
            "q": query,
            "fields": "files(id,name,size,modifiedTime)",
            # Per-tick batch cap (design doc): bounds how many files a
            # single poll can claim in one request. Pagination via
            # nextPageToken is deliberately NOT followed — any files beyond
            # this page simply remain unmarked and surface again on the
            # next poll tick. That's the intended batching behavior, not
            # silent data loss; don't "fix" this into full pagination.
            "pageSize": "20",
        }
        resp = await self._request(
            "GET",
            "/files",
            error_prefix="Drive list failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            params=params,
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
        resp = await self._request(
            "GET",
            f"/files/{ref.identifier}",
            error_prefix="Drive read failed",
            timeout=httpx.Timeout(60.0, connect=5.0),
            params={"alt": "media"},
        )
        return resp.content

    async def move_file(self, ref: FileRef, dest: str) -> None:
        """`dest` is the `composerStatus` appProperty value to set
        ("processed" or "error") — NOT a path, unlike the local provider.
        This marker is always set first and is the permanent claim
        mechanism (list_new_files excludes marked files regardless of
        whether a visible move below succeeds) — this provider's
        never-re-claim guarantee does not depend on the move working.

        If processed_folder_id/error_folder_id is configured for this
        outcome, the file is ALSO visibly relocated there via Drive's
        addParents/removeParents, mirroring the local provider's
        dest_path/error_path move. Both are optional and independent —
        older node configs (or a deliberate choice to keep the invisible
        marker-only behavior) leave one or both unset, in which case only
        the marker is set and the file stays in its original folder."""
        await self._request(
            "PATCH",
            f"/files/{ref.identifier}",
            error_prefix="Drive mark-processed failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            json={"appProperties": {"composerStatus": dest}},
        )

        target_folder_id = self.processed_folder_id if dest == "processed" else self.error_folder_id
        if not target_folder_id:
            return

        # Drive's move semantics require explicitly removing the file's
        # current parent(s), not just adding the new one -- a file can
        # otherwise end up listed under both folders.
        parents_resp = await self._request(
            "GET",
            f"/files/{ref.identifier}",
            error_prefix="Drive parent lookup failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            params={"fields": "parents"},
        )
        current_parents = parents_resp.json().get("parents", [])
        await self._request(
            "PATCH",
            f"/files/{ref.identifier}",
            error_prefix="Drive move failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            params={
                "addParents": target_folder_id,
                "removeParents": ",".join(current_parents),
            },
        )

    async def write_file(self, dest: str, filename: str, content: bytes) -> None:
        """`dest` is a Drive folder id (unlike LocalFilesystemProvider,
        where `dest` is a directory path). Reruns with the same
        folder+filename update the existing file in place rather than
        creating a duplicate -- Drive has no filesystem-style uniqueness
        constraint on names within a folder, so without this search step
        every rerun would silently pile up a new file. Escaping mirrors
        list_new_files' existing escaping for the same injection-safety
        reason: both filename and dest can be attacker-reachable via
        workflow-state substitution."""
        escaped_name = filename.replace("\\", "\\\\").replace("'", "\\'")
        escaped_dest = dest.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            f"name = '{escaped_name}' and '{escaped_dest}' in parents "
            "and trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        )
        search_resp = await self._request(
            "GET",
            "/files",
            error_prefix="Drive find-existing failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            params={"q": query, "fields": "files(id)"},
        )
        existing = search_resp.json().get("files", [])

        if existing:
            file_id = existing[0]["id"]
        else:
            create_resp = await self._request(
                "POST",
                "/files",
                error_prefix="Drive create failed",
                timeout=httpx.Timeout(30.0, connect=5.0),
                json={"name": filename, "parents": [dest]},
            )
            file_id = create_resp.json()["id"]

        await self._request(
            "PATCH",
            f"/files/{file_id}",
            error_prefix="Drive content upload failed",
            timeout=httpx.Timeout(60.0, connect=5.0),
            base_url=UPLOAD_API_BASE,
            params={"uploadType": "media"},
            headers={"Content-Type": "application/octet-stream"},
            content=content,
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="no health check defined")


__all__ = ["GoogleDriveProvider", "GoogleDriveProviderError"]
