# Google Drive OAuth File-Trigger — Design

**Status:** Implemented (2026-07-16)
**Context:** Composer is a browser-only hosted SaaS. The `file-trigger` node's only backing implementation today is `composer watch` (`src/cli/watch.py`) — a long-lived local process that has to run on whatever machine holds the watched folder (`docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md` §B explicitly scopes it this way). That's a real gap for a hosted product: it cannot ask end users to install and run a CLI. This design adds a second `file-trigger` provider, `google-drive`, that Composer's own backend polls directly — no local software required. It extends, and does not modify, the `FileStorageProvider` framework from the 2026-07-11 design, which already named `google-drive` as a documented future provider.

Discovered during brainstorming: `n8n`'s equivalent ("Local File Trigger") only works in n8n's self-hosted mode, for the identical reason — a hosted backend has no visibility into a user's local disk. n8n Cloud's actual answer for file triggers is OAuth-connected cloud storage, polled server-side. This design follows that same shape.

## What already exists (do not re-derive)

- **MCP OAuth pattern** (`src/mcp/oauth.py`, Phase 3b) — the precedent this design's OAuth flow follows exactly: `McpOAuthToken` stores `encryptedAccessToken`/`encryptedRefreshToken` (via `src/security/encryption.py`'s `encrypt()`/`decrypt()`, AES-256-GCM), `expiresAt`, `scope`; `get_valid_access_token(server, user_id, db, expiry_buffer_seconds=60)` is the single entry point that refreshes-if-needed and raises typed errors (`McpTokenExpiredError`/`McpTokenMissingError`) otherwise. `refresh_token()` posts `grant_type=refresh_token` to the provider's token endpoint. This design's `CloudStorageConnection` model and `get_valid_access_token`-equivalent reuse this shape and the same encryption primitives — no new crypto.
- **Cloud Tasks + Cloud Scheduler infrastructure** (ADR-0033, `docs/decisions.md`) — `POST /internal/sweep` is the exact precedent for a Cloud-Scheduler-triggered internal endpoint: `_verify_internal_oidc` (`src/api/internal.py`) verifies the caller's OIDC bearer token via `google.oauth2.id_token.verify_oauth2_token`, checking the token's `email` claim against `settings.cloud_tasks_service_account`, dev-bypassed when that setting is unset. This design's poller follows the identical shape as a fifth sweep-style endpoint.
- **The internal seam for starting a run without HTTP self-calls** — `src/api/run.py`'s `run_external` route resolves a production `Workflow`, then calls `LangGraphExecutor.start_execution(workflow_id=, input=, user_id=, idempotency_key=)` followed by `await enqueue_execution(execution.id, kind="run")` (`src/execution/cloud_tasks.py`). Both are real methods/functions, not route-only logic — the new poller calls them directly, bypassing the `ck_` API-key/HTTP layer entirely.
- **`FileTriggerNodeData`** (`src/engine/workflow.py`) — currently `provider: Literal["local"]`, plus `source_path`/`dest_path`/`error_path`/`target_input_variable`/`poll_interval_seconds`. Visual-only; `graph_builder` skips it, the same way `note` is skipped. This design widens the `provider` literal and adds two fields (§A).
- **`FileStorageProvider` ABC** (`src/storage_providers/base.py`) — `list_new_files`/`read_file`/`move_file`/`write_file`, currently only implemented by `LocalFilesystemProvider`. This design adds `GoogleDriveProvider` as the second implementation.
- **Text extraction** — `composer watch`'s `_extract_text()` (txt/md/pdf/docx via `pypdf`/`python-docx`, already dependencies) is reused verbatim by the new poller; no new extraction code.

## A. Data model

New model, `prisma/schema.prisma`, styled identically to `McpOAuthToken` (plain `userId` string, no declared relation; `@map`/`@@map` snake_case throughout):

```prisma
model CloudStorageConnection {
  id                    String    @id @default(cuid())
  userId                String    @map("user_id")
  provider              String    // "google-drive" (widens later: "dropbox", "onedrive")
  accountEmail          String    @map("account_email")   // connected account, for display in the UI
  encryptedAccessToken  String    @map("encrypted_access_token")
  encryptedRefreshToken String?   @map("encrypted_refresh_token")
  expiresAt             DateTime? @map("expires_at")
  scope                 String?
  createdAt             DateTime  @default(now()) @map("created_at")
  updatedAt             DateTime  @updatedAt       @map("updated_at")

  @@unique([userId, provider, accountEmail])
  @@map("cloud_storage_connections")
  @@index([userId])
}
```

No `isShared`/service-account fallback — deferred (see Non-goals). Per-user-only connections for this pass; additive to add sharing later, same as `McpServer.isShared` was added on top of a simpler base shape.

`FileTriggerNodeData` (`src/engine/workflow.py`) widens:

```python
class FileTriggerNodeData(BaseNodeData):
    provider: Literal["local", "google-drive"] = "local"
    source_path: str | None = Field(default=None, alias="sourcePath")       # local only
    dest_path: str | None = Field(default=None, alias="destPath")           # local only
    error_path: str | None = Field(default=None, alias="errorPath")         # local only
    target_input_variable: str | None = Field(default=None, alias="targetInputVariable")
    poll_interval_seconds: int = Field(default=30, alias="pollIntervalSeconds")  # local (CLI) only, see §D
    connection_id: str | None = Field(default=None, alias="connectionId")   # google-drive only
    drive_folder_id: str | None = Field(default=None, alias="driveFolderId")  # google-drive only
```

`source_path`/`dest_path`/`error_path` keep their existing local-only meaning (no move/error-folder concept for Drive — see §C's claim mechanism instead). Still visual-only; no `graph_builder` change needed.

## B. OAuth connect flow

New `src/api/cloud_storage_oauth.py`, structurally identical to `src/mcp/oauth.py`'s authorize/callback pair, but a standard three-legged Google OAuth2 flow (no RFC 8707 `resource` param — that was an MCP/Highspot-specific requirement, not applicable to Google's OAuth server):

- `GET /cloud-storage/google-drive/authorize` — builds the Google consent URL (`https://accounts.google.com/o/oauth2/v2/auth`) with `scope=https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/drive.metadata https://www.googleapis.com/auth/userinfo.email`, `access_type=offline` (required to receive a refresh token), `prompt=consent`, redirects the browser. Originally shipped with `drive.file` + `userinfo.email` only; both `drive.readonly`/`drive.metadata` (replacing `drive.file` — see §C's OAuth scope note) and `userinfo.email` were added 2026-07-18 after production testing found real gaps: `userinfo.email` because the callback's userinfo lookup (below) fails with a 401 on `drive.file` alone (that scope grants no access to the userinfo endpoint, even though the token exchange itself succeeds); `drive.readonly`/`drive.metadata` because `drive.file`'s Picker-granted access to a folder didn't survive to server-side polling minutes later (confirmed via direct Drive API testing — the folder itself became a 404 to the token, not just its contents).
- `GET /cloud-storage/google-drive/callback` — exchanges the returned `code` for tokens via a direct `httpx` POST to `https://oauth2.googleapis.com/token` (manual HTTP, not the `google-auth-oauthlib` SDK — consistent with this codebase's established preference for manual OAuth/HTTP over heavy client libraries, per the MCP "six fixes" precedent in `CLAUDE.md` §1). Fetches the connected account's email via `GET https://www.googleapis.com/oauth2/v2/userinfo`, encrypts both tokens with the existing `encrypt()` primitive, upserts `CloudStorageConnection` keyed on `(userId, provider, accountEmail)`.
- `get_valid_drive_access_token(connection_id, db, expiry_buffer_seconds=60)` — the `get_valid_access_token()` equivalent: refresh-if-needed, raise typed errors otherwise. Refresh is a direct `httpx` POST with `grant_type=refresh_token` (no `resource` param, unlike MCP's Highspot-driven requirement).

## C. `GoogleDriveProvider` (`src/storage_providers/google_drive.py`)

Implements `FileStorageProvider` via direct `httpx` calls to the Drive v3 REST API (`https://www.googleapis.com/drive/v3/...`) with the connection's access token as a `Bearer` header — **no `google-api-python-client` dependency**, matching the "manual HTTP over heavy SDK" preference above and keeping this consistent with the MCP integration's own manual `tools/list` JSON-RPC approach.

- **`list_new_files(folder_id)`** — `GET /files?q='{folder_id}' in parents and trashed=false and not appProperties has {key='composerStatus'}` (Drive query language supports `appProperties` filters directly), capped at 20 results per call; remainder is naturally picked up on the next poll since unmarked files stay in the query.
- **`read_file(ref)`** — `GET /files/{id}?alt=media` downloads raw bytes, then reuses `composer watch`'s `_extract_text()` unchanged. **MVP scope: plain text/PDF/DOCX only** — native Google Docs/Sheets/Slides need a separate `files.export` call with a MIME-type map (Drive doesn't serve raw bytes for its own document types); explicitly deferred (§ Non-goals).
- **`move_file(ref, status)`** — `PATCH /files/{id}` with `appProperties={"composerStatus": status}` (`"processed"` or `"error"`). This *is* the claim mechanism, replacing the local provider's move-to-folder semantics: once marked, the file is permanently excluded from `list_new_files`'s query regardless of outcome — identical "never re-claim a claimed file" behavior to local's dest-or-error move, just expressed as a property instead of a location.
- **`write_file`** — raises `NotImplementedError`. This provider is trigger-only; unrelated to the separate `file-write` node, which stays local-only for now.

**OAuth scope note (revised 2026-07-18 — supersedes the original `drive.file` design):** The original design used `drive.file`, reasoning that Google Picker's folder-select mode would grant durable, ongoing access to a folder's current and future contents per Google's general documentation. **Confirmed false in production**: a folder picked via Picker became unreachable (`404 File not found` on the folder object itself, not just its contents) to the server-side token within minutes — the Picker session's live access to browse the folder did not persist for later polling. Root-caused via direct Drive API calls using the stored connection's own token, repeated over 20+ minutes to rule out a transient issue.

Replaced with two narrower non-`drive.file` scopes instead of the unrestricted `drive` scope: **`drive.readonly`** (durable read access for `list_new_files`'s metadata query and `read_file`'s `alt=media` content download — not tied to any Picker session) plus **`drive.metadata`** (metadata read/write across Drive, needed because `move_file`'s `appProperties` PATCH is a write `drive.readonly` alone can't perform, but which doesn't require the full-content-write access the unrestricted `drive` scope would also grant). Both `drive.readonly` and `drive.metadata` are Google "restricted" scopes requiring a CASA security assessment to verify for public production use beyond 100 test users — the same tier `drive.file` was originally chosen to avoid. Since this app's OAuth consent screen stays in **Testing** status (a small number of known test users, not seeking public verification), that requirement doesn't apply either way, so there was no actual cost to using the correctly-scoped combination over the single scope that didn't work. Google Picker is still used for folder *selection* (§E) — it no longer needs to carry the access grant itself, since `drive.readonly`/`drive.metadata` already cover the whole Drive account.

## D. Server-side scheduled polling

New `POST /internal/poll-file-triggers` (`src/api/internal.py`), OIDC-verified by the exact same `_verify_internal_oidc` every other internal endpoint uses — a fifth sweep-style target alongside ADR-0033's four, added to the same Cloud Scheduler job pattern (new cron entry, flat 5-minute cadence).

Logic per invocation:
1. Query production workflows (`isProduction=true`) whose `nodes` JSON contains a `file-trigger` node with `data.provider = "google-drive"`.
2. For each: resolve `CloudStorageConnection` via `connection_id`, call `get_valid_drive_access_token()`, `list_new_files(drive_folder_id)`.
3. For each new file: `read_file()` → extract text → `LangGraphExecutor.start_execution(workflow_id=, input={target_input_variable: text}, user_id=<connection owner>)` → `enqueue_execution(execution.id, kind="run")` (§ "What already exists," third bullet) → `move_file(ref, "processed")`.
4. On extraction or start_execution failure for a given file: `move_file(ref, "error")`, log, continue — one file's failure never blocks the rest of the batch (mirrors `composer watch`'s per-file try/except).

`poll_interval_seconds` is **not** honored for Drive triggers in this pass — the flat 5-minute Cloud Scheduler cadence applies uniformly; per-node custom cadence is deferred (§ Non-goals). It remains meaningful for the local/CLI case only.

## E. Frontend

`node-panels/file-trigger.tsx` gains a provider picker (`Local` / `Google Drive`) in the existing "Watch configuration" panel. Selecting `Google Drive` shows:
1. A **"Connect Google Drive"** button — opens `GET /cloud-storage/google-drive/authorize` in a popup, same pattern as the existing MCP server connect flow. On success, shows the connected account's email (`CloudStorageConnection.accountEmail`).
2. Once connected, an embedded **Google Picker** (folder-select mode, using the connection's access token + the new `GOOGLE_PICKER_API_KEY`) to choose the watched folder, storing `driveFolderId` and `connectionId` on the node.

No changes to `graph_builder.py`, `COMPOSER_NODE_TYPES`, or the palette — `file-trigger` is already fully registered; this only extends its property panel.

## F. Google Cloud provisioning (manual, one-time)

On the same GCP project already used for Cloud Tasks/Scheduler (ADR-0033):
1. Enable the **Drive API**.
2. Configure the **OAuth consent screen** — External, **Testing** publishing status (no Google verification required for up to 100 test users; appropriate for internal Bounteous use). Add each connecting user as a test user.
3. Create an **OAuth 2.0 Client ID** (Web application type), redirect URI → `{backend_public_url}/cloud-storage/google-drive/callback`.
4. Create an **API key**, restricted to the **Picker API**, for frontend use.
5. New env vars: `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_PICKER_API_KEY`.

**New dependencies: none.** OAuth token exchange/refresh and all Drive API calls go through `httpx`, already a dependency — no `google-api-python-client`, `google-auth-oauthlib`, or similar SDK addition (flagged per `CLAUDE.md`'s "ask before adding a dependency not in the design doc" rule — this design doc is the confirmation that none is needed).

## G. Error handling & idempotency

- **Per-file claim is permanent** — a file marked `processed` or `error` via `appProperties` is never re-picked-up, matching local's move-based semantics. No retry-with-backoff for a failed file; a user can manually clear the `composerStatus` property in Drive (or, future work, from Composer's UI) to force a re-attempt.
- **Batch cap** — 20 files per node per poll tick, to bound a single scheduler invocation's duration; a folder with more than 20 unclaimed files drains across multiple ticks.
- **Token expiry mid-poll** — `get_valid_drive_access_token()` refreshes proactively (60s buffer, same as MCP); a hard failure (revoked grant) skips that node for the tick and logs, without blocking other workflows' triggers in the same batch.
- **Duplicate execution prevention** — relies on the same `appProperties` marker as claim-tracking; since marking happens only after `enqueue_execution()` succeeds, a crash between "file listed" and "marked processed" could in principle re-list the same file on the next tick and start a second execution. Accepted for this pass (`start_execution`'s existing idempotency-key path is oriented around external-caller-supplied keys, not applicable here); noted as a known edge case, not a design gap to silently ignore.

## H. Testing strategy

- Unit tests for `GoogleDriveProvider` against a mocked `httpx` transport (`respx` or equivalent, matching how `HttpExecutor`/MCP tests already mock outbound calls) — list/read/move against representative Drive API responses, including the `appProperties`-filtered query shape.
- Unit tests for the OAuth connect/callback/refresh flow, mirroring `src/mcp/oauth.py`'s existing test structure.
- Unit tests for `POST /internal/poll-file-triggers`: OIDC auth rejection (reuse the existing `/internal/sweep` auth test pattern), multi-workflow iteration, per-file failure isolation (one bad file doesn't block the batch), batch cap enforcement.
- No real-Google-account integration test in this pass (mirrors how MCP's OAuth flow was unit-tested against mocks, with the real-Highspot verification done manually) — end-to-end verification against a real connected Drive account is a manual smoke test once GCP provisioning (§F) is complete.

## Non-goals (explicitly out of scope for this pass)

- Shared/service-account `CloudStorageConnection`s (`McpServer.isShared`'s equivalent) — per-user-only connections for now.
- Dropbox, OneDrive, S3 providers — `google-drive` only; the `FileStorageProvider` interface already supports adding these later without change.
- Native Google Docs/Sheets/Slides export (`files.export`) — plain text/PDF/DOCX only, matching `composer watch`'s existing extraction support.
- Drive Changes API (cursor-based change feed) — the simpler `appProperties`-filtered `files.list` query is sufficient at this scale; Changes API is a possible future efficiency optimization, not required.
- Per-node custom poll cadence for Drive triggers — flat 5-minute Cloud Scheduler cadence for all cloud triggers in this pass; `pollIntervalSeconds` stays meaningful for the local/CLI case only.
- A UI affordance to manually clear a file's `composerStatus` to force reprocessing — noted in §G as a future improvement, not built now.
