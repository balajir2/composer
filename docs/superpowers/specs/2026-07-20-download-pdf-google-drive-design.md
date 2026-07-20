# Design: Google Drive write support for `download-pdf`

**Date:** 2026-07-20
**Status:** Approved (design phase) — implementation plan not yet written
**Origin:** Raised while testing the Macy's Feature 2 executive-report workflow — `download-pdf`
(and `file-write`) only support the `local` storage provider, so a file written on the deployed
Cloud Run instance (`flowcomposer.online`) is not retrievable through the product today. This is
new platform capability, not customer-flow-specific — no Macy's-specific literals appear anywhere
in this design.

## Why this spec exists

`file-write`/`download-pdf` currently hardcode `_PROVIDERS = {"local": LocalFilesystemProvider}`.
`GoogleDriveProvider` (`src/storage_providers/google_drive.py`) already exists, but its
`write_file()` is an explicit stub:

```python
async def write_file(self, dest: str, filename: str, content: bytes) -> None:
    raise NotImplementedError(
        "GoogleDriveProvider is trigger-only; unrelated to the file-write node"
    )
```

Everything else Drive-side already exists and is reusable as-is:

- **OAuth connect flow**: `src/api/cloud_storage_oauth.py` (`/cloud-storage/google-drive/authorize`,
  `/callback`, `/connections`, `/connections/{id}/picker-token`) — a full "connect your Google
  Drive" flow already backs `file-trigger`.
- **Connection storage**: `CloudStorageConnection` Prisma model (`prisma/schema.prisma`) — `id`,
  `userId`, `provider`, `accountEmail`, `encryptedAccessToken`, `encryptedRefreshToken`,
  `expiresAt`, `scope`.
- **Token resolution + refresh**: `get_valid_drive_access_token(connection_id, db)`
  (`src/integrations/google_drive/oauth.py`) — refreshes within 60s of expiry, persists the new
  token, returns the decrypted plaintext access token. Needs only a connection id + a Prisma `db`
  handle.
- **DB access inside an executor at execution time**: not threaded through `WorkflowStateDict` or
  executor constructors. `src/engine/context.py`'s `get_current_db()`/`set_current_db()` is a
  `ContextVar` set once per execution in `LangGraphExecutor` (`src/engine/langgraph_executor.py`)
  before any node runs. `src/executors/mcp.py`'s `_require_db()` already calls
  `get_current_db()` for exactly this purpose (server-side OAuth token retrieval — CLAUDE.md fix
  #4). Any executor can call it the same way; no new plumbing through `build_executor` or the
  executor Protocol is needed.
- **Designer UI picker**: `frontend/components/composer/canvas/node-panels/google-drive-connect.tsx`
  — a generic component (`connectionId`, `driveFolderId`, `driveProcessedFolderId`,
  `driveErrorFolderId`, `onChange` props) already used by `file-trigger.tsx`, not tied to the
  trigger use case.

So this spec is narrowly scoped to: (1) implementing the one missing piece,
`GoogleDriveProvider.write_file()`; (2) wiring `download-pdf`'s executor/schema to use it; (3) a
small Designer panel change reusing the existing picker. `file-write`'s Drive support and the
OAuth/connection infrastructure itself are explicitly out of scope — the latter is already built.

## Decisions locked by user Q&A during brainstorming

- **Rerun semantics: update-in-place.** Since a Drive folder can hold multiple files with an
  identical name (no filesystem-style uniqueness), `write_file()` searches the destination folder
  for an existing file named `filename` first. If found, its content is replaced (same file ID,
  new revision) rather than creating a duplicate. If not found, a new file is created. This
  mirrors `confluence.py`'s `_create_or_update_page`/`_find_page` pattern (find-then-branch),
  applied to Drive's API instead of Confluence's.
- **Scope: `download-pdf` only for this pass.** `file-write`'s Drive support is deferred to a
  later, small follow-up — the schema/executor changes below are written generically enough
  (mirroring `FileTriggerNodeData`'s field names) that extending to `file-write` later is not a
  redesign.
- **Sharing/permissions**: not configured explicitly — uploaded files inherit the destination
  folder's existing Drive sharing permissions (Drive's default behavior for a file created inside
  a folder). No new permission-setting API call is added.

## Component 1 — `GoogleDriveProvider.write_file()`

Replace the `NotImplementedError` stub in `src/storage_providers/google_drive.py`:

Deliberately avoids Drive's `multipart/related` single-call upload contract (metadata + binary in
one request) — `httpx` has no native support for that content type, and hand-rolling a multipart
body with a manual boundary is exactly the kind of fiddly-to-get-right, easy-to-corrupt-silently
code this codebase avoids elsewhere (see `_pdf_security.py`'s preference for one well-understood
mechanism over a fragile hand-built one). Instead, both branches use Drive's documented two-call
`uploadType=media` contract, which is plain JSON + plain binary, both already comfortably within
`httpx`'s normal request shapes:

```python
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

async def write_file(self, dest: str, filename: str, content: bytes) -> None:
    # dest = a Drive folder ID (unlike LocalFilesystemProvider, where dest is a directory path).
    escaped_name = filename.replace("\\", "\\\\").replace("'", "\\'")
    escaped_dest = dest.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name = '{escaped_name}' and '{escaped_dest}' in parents and trashed = false"
    resp = await self._request(
        "GET", "/files", error_prefix="Drive find-existing failed",
        timeout=httpx.Timeout(30.0, connect=5.0),
        params={"q": query, "fields": "files(id)"},
    )
    existing = resp.json().get("files", [])

    if existing:
        file_id = existing[0]["id"]
    else:
        # Step 1 of create: metadata-only POST against the regular (non-upload) API,
        # establishing the file's name/parent and returning its new id.
        create_resp = await self._request(
            "POST", "/files", error_prefix="Drive create failed",
            timeout=httpx.Timeout(30.0, connect=5.0),
            json={"name": filename, "parents": [dest]},
        )
        file_id = create_resp.json()["id"]

    # Step 2 (both branches): upload/replace the binary content against the upload-specific
    # base URL, via a plain octet-stream PATCH -- no multipart body needed for either the
    # create or the update case.
    async with httpx.AsyncClient(
        base_url=UPLOAD_BASE, headers=self._headers(), timeout=httpx.Timeout(60.0, connect=5.0)
    ) as client:
        media_resp = await client.request(
            "PATCH", f"/files/{file_id}",
            params={"uploadType": "media"},
            headers={"Content-Type": "application/octet-stream"},
            content=content,
        )
    if media_resp.status_code >= 400:
        raise GoogleDriveProviderError(
            f"Drive content upload failed (HTTP {media_resp.status_code}): "
            f"{media_resp.text[:300]}"
        )
```

The media-upload step constructs its own short-lived `httpx.AsyncClient` against `UPLOAD_BASE`
rather than going through `_request()` (which is hardcoded to `DRIVE_API_BASE`) — the
implementation task may find a cleaner way to share the transport-error-wrapping behavior between
the two base URLs without duplicating it, but the two-call, plain-body contract above is the
locked design (not `multipart/related`).

Existing name/folder-id escaping logic (already present in `list_new_files` for the same
injection-safety reason — `CloudStorageConnection`-derived folder ids are plain workflow JSON, not
exclusively set via the trusted Picker flow) is reused verbatim for the new query.

## Component 2 — `DownloadPdfNodeData` schema additions

In `src/engine/workflow.py`, add two new optional fields to `DownloadPdfNodeData`, mirroring
`FileTriggerNodeData`'s exact naming:

```python
connection_id: str | None = Field(default=None, alias="connectionId")
drive_folder_id: str | None = Field(default=None, alias="driveFolderId")
```

`destination_path`/`filename` are unchanged and still present — `destination_path` is simply
unused when `provider == "google-drive"` (the destination is `drive_folder_id` instead), matching
how `file-trigger` already treats `sourcePath`/`destPath` as inert when its own provider is
`"google-drive"`. `provider` itself stays a plain `str` (already the case, for the same
"executor must be able to receive an unrecognized value and raise at runtime" reasoning documented
on `FileWriteNodeData.provider`) — no `Literal` change needed.

## Component 3 — Executor wiring

`src/executors/download_pdf.py`'s `arun()`, when `self.node.data.provider == "google-drive"`:

```python
from src.engine.context import get_current_db
from src.integrations.google_drive.oauth import get_valid_drive_access_token
from src.storage_providers.google_drive import GoogleDriveProvider

...
if provider_name == "google-drive":
    connection_id = substitute(self.node.data.connection_id or "", state)
    folder_id = substitute(self.node.data.drive_folder_id or "", state)
    if not connection_id or not folder_id:
        raise MissingDriveConfigError(
            f"download-pdf node {self.node.id!r}: google-drive provider requires "
            "connectionId and driveFolderId"
        )
    db = get_current_db()
    access_token = await get_valid_drive_access_token(connection_id, db)
    provider = GoogleDriveProvider(access_token)
    destination = folder_id  # write_file's `dest` param is the folder id, not a path
else:
    provider = _PROVIDERS[provider_name]()
    destination = substitute(self.node.data.destination_path or "", state)
    ... existing InvalidDestinationError validation, unchanged for "local" ...
```

`MissingDriveConfigError(ValueError)` is a new exception class alongside the existing
`UnknownStorageProviderError`/`InvalidDestinationError`/`InvalidFilenameError` (both of which also
subclass `ValueError` — same convention). The existing
`_validate_filename` guard still applies to `filename` regardless of provider (a bare filename with
no path separators/`..` — Drive's `name` field has no directory semantics, but the same
untrusted-upstream-content threat model applies since `filename` can still originate from
substituted workflow state).

## Component 4 — Designer panel

`frontend/components/composer/canvas/node-panels/download-pdf.tsx`:

- Add `{ value: "google-drive", label: "Google Drive" }` to the provider `NativeSelect`'s options.
- When `data.provider === "google-drive"`, render the existing `GoogleDriveConnect` component
  (imported from `./google-drive-connect`, already generic) with `connectionId`/`driveFolderId`
  wired to `data.connectionId`/`data.driveFolderId`, in place of the `destinationPath` `Input` —
  same conditional structure `file-trigger.tsx` already uses. The `driveProcessedFolderId`/
  `driveErrorFolderId` props are omitted (trigger-only concepts, not applicable to a write
  destination) — `GoogleDriveConnect` already treats them as optional.

## Testing

- **`GoogleDriveProvider.write_file()`**: `pytest-httpx`-mocked unit tests covering (a) create path
  (no existing file matches the query → multipart-upload POST), (b) update path (existing file
  found → media PATCH to the same file id, no duplicate created), (c) name/folder-id
  apostrophe/backslash escaping in the search query (mirroring `list_new_files`'s existing escaping
  tests), (d) `GoogleDriveProviderError` raised on a non-2xx response from either the search or the
  upload call.
- **`DownloadPdfExecutor` with `provider: "google-drive"`**: mock `get_current_db`/
  `get_valid_drive_access_token` to verify the executor constructs `GoogleDriveProvider` correctly
  and calls `write_file(folder_id, filename, pdf_bytes)`; a missing `connectionId`/`driveFolderId`
  raises `MissingDriveConfigError` before any network call; existing `"local"`-provider behavior
  (all current tests in `tests/unit/executors/test_download_pdf.py`) is unchanged — this is a
  purely additive branch.
- **Frontend**: new tests in `download-pdf.test.tsx` confirming the provider dropdown offers
  `google-drive`, and that selecting it swaps in `GoogleDriveConnect` (can assert on props passed,
  mirroring however `file-trigger.test.tsx`, if one exists, already verifies the same swap).
- Real end-to-end verification against an actual Google Drive account (OAuth connect → run a
  workflow → confirm the file lands/updates in Drive) is the user's to perform, same as the
  Jira/Confluence integrations earlier in this project — mocked tests structurally cannot catch
  live Drive API drift.

## Explicitly deferred to later sub-projects

- `file-write`'s Google Drive support (per user Q&A) — same pattern, small follow-up.
- Explicit Drive sharing/permission configuration beyond folder-inherited defaults.
- Any UI for browsing/selecting an *existing* Drive file to overwrite by ID directly (this design
  always resolves by folder + name, never lets the user point at an arbitrary existing file id).
