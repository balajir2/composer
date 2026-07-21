# Changelog

All notable changes to Composer will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added — Test-only hard-delete for Playwright e2e accounts (2026-07-21)

User-reported (again): Playwright test users/workflows left behind in the shared dev Neon database. Investigation found the root cause: `frontend/e2e/fixtures/test-user.ts`'s `createTestUser()` registers a fresh `pw-*@example.com` account via `POST /auth/register` for every e2e run, but nothing ever deleted it afterward — none of the five specs using it (`date-field`, `designer`, `admin`, `end-user`, `auth`) or the sixth spec with its own inline registration (`external-invoke`) had teardown. There was also no way to fix this after the fact: `DELETE /admin/users/{id}` (`src/api/admin_users.py`) is a deliberate soft-delete that preserves workflows for audit trail, by design, for real users. 21 accounts (and 14 owned workflows, cascading to their executions/approvals) had accumulated between 2026-07-10 and 2026-07-20 before this was caught; cleaned up by hand via a one-off DB script.

Adds `DELETE /internal/test-users/me` (`src/api/test_cleanup.py`), a narrowly-scoped self-service hard-delete: registered only when `settings.environment != "production"` (never reachable in prod, checked both at router-inclusion time in `src/main.py` and again inside the handler), authenticated via the normal `get_current_user_id` dependency so a caller can only ever delete the account whose own token they hold (no target-user-id parameter), and additionally gated on the calling account's email matching the exact `pw-<token>@example.com` shape the e2e fixtures generate — even a real developer's dev-mode session can't be deleted through it. `frontend/e2e/fixtures/test-user.ts` now tracks every user `createTestUser()` registers and exposes `cleanupTestUsers()`; all six specs call it from a `test.afterEach`. `external-invoke.spec.ts` also switched from its own inline `/auth/register` call to the shared fixture, removing the duplication.

6 new backend unit tests (`tests/unit/api/test_test_cleanup.py`) covering the happy path, the email-pattern guard, missing-user 404, the production kill-switch, the auth requirement, and that the route can never target another account. Full ruff + pyright (strict) + `tsc --noEmit` + eslint + prettier clean.

### Added — Calendar pickers for Start-node `date`/`datetime` field types (2026-07-20)

Start-node input fields (e.g. `report_date`, `extract_timestamp` in the Macy's Jira Weekly Delivery Tracking workflow) previously only had `text`/`number`/`boolean`/`json`/`document` types, forcing end-users to type dates by hand. Adds `date` and `datetime` as two new field types, each rendering a calendar picker instead of a plain text box, everywhere a Start field's value is entered.

`StartInputVariable.type` (`src/engine/workflow.py`) needed no change — it's already a free-form string, so `date`/`datetime` are new accepted values, purely a frontend rendering hint; submitted execution values stay plain ISO-8601 text (`YYYY-MM-DD` / `YYYY-MM-DDTHH:mm:ss`) exactly like a `text` field. New frontend primitives: `components/ui/calendar.tsx` (a hand-rolled month-grid — no `react-day-picker`/`date-fns` dependency, since the repo's shadcn-derived UI kit is built on Base UI, not Radix) and `components/ui/popover.tsx` (the repo's first Popover, wrapping `@base-ui/react/popover`, mirroring `dropdown-menu.tsx`'s composition pattern). `components/composer/date-field.tsx` composes these into `DatePickerButton`/`DateTimePickerButton` (controlled widgets) and `DatePickerInput`/`DateTimePickerInput` (react-hook-form wrappers matching `DocumentField`'s register/setValue convention), wired into all three Start-field editing surfaces: the Designer panel's default-value editor, the production run form, and the "Run draft" dialog.

Full TDD; unit tests for the date-formatting helpers, the `Calendar` primitive, the composed picker widgets, and each of the three wiring sites; Playwright e2e test drives the real calendar popover against live servers (the one interaction jsdom couldn't reliably cover — see `native-select.tsx`'s comment on why `Select` was replaced with a native `<select>` for the same reason).

### Added — `download-pdf` node, `file-write` gains an `html` format (2026-07-20)

A new `download-pdf` node type: renders HTML or Markdown content directly to a PDF and writes it via a storage provider (`local` or `google-drive`) — broken out from `file-write` as its own node rather than another format option on that one, since PDF rendering needs an explicit input-format choice (`inputFormat: html | markdown`) up front. Shares `file-write`'s SSRF-blocking PDF-rendering guarantee (`src/conversion/_pdf_security.py`) rather than a second copy, since that's the security-critical piece where drift would matter; destination/filename validation is intentionally duplicated locally (small, ~10 lines) to keep the two node types' modules decoupled. Google Drive write support resolves an OAuth access token at execution time via the same `get_current_db()`/`get_valid_drive_access_token()` mechanism `mcp.py` and the Drive file-trigger polling endpoint already use — no new plumbing, no token ever transits through the client.

`file-write`'s `format` field also gained `html` (alongside existing `md`/`docx`/`pdf`) — written through as-is like `md`, no Markdown parsing.

`DownloadPdfNodeData`/`DownloadPdfNode` (`src/engine/workflow.py`), `DownloadPdfExecutor` (`src/executors/download_pdf.py`), Designer property panel, wired through the executor registry, graph builder, tools palette, node visuals, and canvas type registry.

This entry was written retroactively (2026-07-21), alongside the equivalent fix for the Confluence node below — both features shipped with no CHANGELOG entry and no designer-guide.md node-reference coverage, caught during a full documentation audit. Both gaps are now closed (see `docs/designer-guide.md`'s `download-pdf` and `file-write` sections; node count corrected from the stale "23" to 24). No ADR was written for this feature either — see the note on the Confluence entry below for why one wasn't fabricated retroactively.

### Added — Confluence node (2026-07-19)

A new `confluence` node type, sibling to `jira`: deterministic (no LLM) Confluence Cloud page operations — create/update a page, get a page, get/set a page content-property — via REST API v1. Same per-node, per-workflow credential model as `jira` (domain/email/API token entered directly on the node, no shared MCP registration or admin-managed key), and the same encrypt-at-rest + redact-on-read pattern for the API token, though implemented via the newer generic `encrypt_marked`/`decrypt_marked` primitives (`src/security/encryption.py`, added for the 2026-07-13 credential-disclosure fix) rather than Jira's older bespoke helpers.

`ConfluenceNodeData`/`ConfluenceNode` (`src/engine/workflow.py`), `ConfluenceExecutor` (`src/executors/confluence.py`), Designer property panel (`frontend/components/composer/canvas/node-panels/confluence.tsx`), wired through the executor registry, graph builder, property panel, node visuals, and canvas type registry. `propertyValue` (used by `get_property`/`set_property`) is typed `Any` on the backend, so the panel round-trips it as parsed JSON in local component state rather than a raw string bind (the `arcade.tsx` stringify/parse pattern).

This entry was written retroactively (2026-07-21) — the feature shipped 2026-07-19 (`feature/confluence-node` branch, merged `dcfa0e6`) with no CHANGELOG entry and no designer-guide.md node-reference section, caught during a full documentation audit. Both gaps are now closed; see `docs/designer-guide.md`'s `confluence` section (node count corrected from the stale "22" to 23) and `docs/superpowers/plans/2026-07-19-confluence-node.md` for the full task-by-task implementation record. No ADR was written for this feature (unlike the comparable `jira`/file-storage/approval-email additions, each of which got one) — flagging that gap rather than fabricating retroactive design rationale for one now.

### Fixed — Agent node's Provider dropdown showed empty for already-configured nodes (2026-07-18)

User-reported: "I did not select a provider and yet it worked." Traced to `AgentNodeData` (`src/engine/workflow.py`) having no `provider` field at all — only a combined `model` string (e.g. `"anthropic/claude-haiku-4-5-20251001"`) that the executor (`src/executors/agent.py`) reads directly. The frontend's Provider dropdown (`agent.tsx`) is a UI-only convenience for filtering the Model dropdown, initialized from `data.provider` alone. Every seeded template (`scripts/seed_templates.py`) pre-populates `model` but never `provider`, so opening an already-configured Agent node showed a misleading blank "Select provider" placeholder — the node was fully valid and ran correctly (the backend never reads `provider`), but the UI looked unconfigured.

`agent.tsx` now derives its initial provider from the stored `model` string's `"<provider>/<modelId>"` prefix (`providerFromModel()`) whenever `data.provider` itself is unset, so opening a template-seeded or previously-saved node shows the correct Provider *and* Model selected instead of appearing empty. An explicitly-stored `provider` (only ever written by the dropdown's own `onChange`) still takes precedence. 4 new unit tests added.

### Added — Visible Processed/Error folder moves for the Google Drive file-trigger (2026-07-18)

Follow-up to the Google Drive OAuth file-trigger feature (below). A user reported that, unlike the local provider (which visibly moves files into `destPath`/`errorPath`), successfully-processed Drive files stayed put in the watched folder with no visible sign they'd been handled — the `appProperties` claim marker (§C of the design doc) is invisible in the Drive UI. Verified the underlying claim/never-reprocess mechanism was working correctly (direct Drive API check confirmed the marker was being set); the gap was purely a lack of user-visible feedback.

`FileTriggerNodeData` gains two new optional fields, `driveProcessedFolderId`/`driveErrorFolderId`, mirroring the local provider's `destPath`/`errorPath` semantics as Drive folder IDs. `GoogleDriveProvider.__init__` accepts them as keyword-only `processed_folder_id`/`error_folder_id`; `move_file()` still sets the `appProperties` marker first (unchanged, still the permanent claim mechanism), then — only if the matching folder ID is configured — additionally relocates the file there via `GET .../parents` + `PATCH ...addParents/removeParents`. Both fields are optional and independent; leaving either unset preserves the original marker-only behavior for existing node configs. `POST /internal/poll-file-triggers` now reads both fields off the node and passes them into the provider constructor. Frontend's `GoogleDriveConnect` gains two more Picker-backed folder selectors ("Processed folder", "Error folder") alongside the existing watch-folder picker.

Full TDD; `GoogleDriveProvider` unit tests, `FileTriggerNodeData` model tests, `poll_file_triggers` wiring test, and frontend component tests all added/passing.

### Fixed — Local dev/CI no longer requires real Cloud Tasks infra to run a workflow (2026-07-16)

Root cause of `500: Execution ... could not be enqueued for processing` / `400 service_account_email must be set`: ADR-0033's Cloud Tasks migration (previous entry) made `enqueue_execution()` unconditionally build a real Cloud Tasks task with an OIDC token, even when `CLOUD_TASKS_SERVICE_ACCOUNT`/`GCP_PROJECT_ID` aren't configured — true for any machine where the GCP infra (`scripts/gcp-bootstrap.ps1`) hasn't been provisioned, which was deliberately deferred (deploy paused pending go-ahead). Every execution/resume path (`POST /executions`, resume, external-invoke, approval-email confirm, lease-expiry re-enqueue) hit this with no fallback — unlike `_verify_internal_oidc` (src/api/internal.py), which already no-ops in this exact state (ADR-0015 pattern), `enqueue_execution` had no equivalent.

Factored the claim-and-run logic out of the `claim_and_run` HTTP handler into a standalone `claim_and_run_execution()` (src/api/internal.py) callable without a `Request`. `enqueue_execution()` (src/execution/cloud_tasks.py) now dispatches it in-process via `asyncio.create_task` (fire-and-forget, matching Cloud Tasks' own async-return semantics) whenever `CLOUD_TASKS_SERVICE_ACCOUNT` is unset, instead of attempting a real Cloud Tasks RPC. Production behavior (real GCP infra configured) is unchanged. Full TDD; all 5 production call sites and their test doubles updated; 1104 unit tests passing, full-repo ruff + pyright (strict) clean.

### Added — Google Drive OAuth file-trigger (server-side polling, no local agent) (2026-07-16)

Implements the design in `docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md`: a `file-trigger` node can now point at a Google Drive folder instead of a local one. New `CloudStorageConnection` model stores per-user OAuth tokens (mirrors `McpOAuthToken`'s encryption pattern); `src/integrations/google_drive/oauth.py` handles the OAuth dance via direct `httpx` calls (no SDK); `GoogleDriveProvider` implements the existing `FileStorageProvider` ABC, using a Drive `appProperties` marker as its claim mechanism instead of local's move-to-folder. `POST /internal/poll-file-triggers` — a fifth sibling to ADR-0033's `claim-and-run`/`sweep`, same OIDC auth — is the Cloud-Scheduler-triggered poll loop; a flat 5-minute cadence applies to all Drive triggers (per-node `pollIntervalSeconds` stays meaningful for the local/CLI case only). Frontend gains a "Connect Google Drive" + Google Picker folder-select flow in the `file-trigger` node's property panel. No new dependencies — OAuth and Drive API calls go through `httpx`, already a dependency, consistent with the MCP integration's manual-HTTP-over-SDK precedent.

Deliberately out of scope for this pass (see design doc's Non-goals): shared/service-account connections, Dropbox/OneDrive/S3 providers, native Google Docs export, Drive Changes API cursoring, per-node custom poll cadence for cloud triggers.

### Added — Example 20 template: File Watch to Summarize and Email (2026-07-15)

New reference template (`scripts/seed_templates.py`, `template-20-file-watch-summarizer`): `file-trigger` (visual-only) → Start → Agent (summarize) → Email → End. Demonstrates the `file-trigger` node alongside the existing `composer watch` CLI (`src/cli/watch.py`), which remains the only supported watcher today — it's a local process that has to run on whatever machine can see the folder, since the hosted backend has no visibility into a user's local disk (confirmed against `docs/archive/phase-history/specs/2026-07-11-file-storage-provider-framework-design.md` §B, which explicitly scopes it as "a long-lived local process on the operator's machine"). A server-side, OAuth-connected cloud-storage watcher (Drive/Dropbox/S3, polled via a Cloud-Scheduler-triggered endpoint following the `POST /internal/sweep` pattern from ADR-0033) is planned separately — this template is unaffected either way, since the workflow only consumes `file_content` regardless of which watcher produced it. No executor or schema changes; template-only addition, structurally validated against `Workflow`/`validate_workflow_shape`.

### Added — Durable execution via Cloud Tasks, Postgres-backed events + rate limits (2026-07-15)

Resolved P1-2 and P1-4 from `docs/deferred-backlog.md`. Root cause: Composer's execution model (`BackgroundTasks.add_task()`, bare `asyncio.create_task()`) and cross-cutting infrastructure (in-process event bus, in-memory rate limiter) were process-local, correct only for a single, continuously-running backend instance — verified against the live deploy config (`scripts/gcp-bootstrap.ps1`, `--min-instances=0`) that the execution risk was active today, not theoretical: Cloud Run can scale an instance to zero mid-flight the moment its HTTP response is sent, regardless of whether a detached background task is still running.

`POST /executions` now creates a `status='queued'` row and enqueues a Google Cloud Tasks task instead of a background task; Cloud Tasks delivers an OIDC-authenticated HTTP push to a new `POST /internal/claim-and-run`, which claims the row via `SELECT ... FOR UPDATE SKIP LOCKED` (the pattern validated in ADR-0031) before running it — because it's a real inbound request, Cloud Run keeps the instance alive for the execution's full duration. Approval resume and external-invoke route through the same mechanism. A lease/heartbeat on `WorkflowExecution` plus a new Cloud-Scheduler-triggered `POST /internal/sweep` (not an in-process loop) recovers executions whose worker died mid-run, letting `--no-cpu-throttling` be dropped from the Cloud Run deploy — the actual dominant cost driver uncovered mid-implementation (see ADR-0033's 2026-07-14 addendum).

Events moved from an in-process `ExecutionEventBus` to a durable, sequence-numbered `execution_events` table plus Postgres `LISTEN/NOTIFY` as a low-latency (non-durable) wake-up signal; `GET /executions/{id}/ws` replays from a reconnect cursor before subscribing live, closing the missed-event and no-cross-instance-visibility gaps the old bus had. Rate limiting moved from an in-memory token bucket to a Postgres-backed atomic bucket table, correct across Cloud Run's multiple instances.

Real-Postgres integration testing caught two genuine production bugs invisible to any mocked-DB unit test. First: the claim-and-run lease `UPDATE` sent a Python `datetime` as `text`, rejected by the `timestamp without time zone` column — fixed with an explicit `timestamptz` cast. Second, found during a final holistic review of the whole branch rather than any single task: `resume_execution` and the emailed-approval confirm endpoint flipped a paused execution back to `running` and enqueued a fresh Cloud Task without clearing the stale lease left over from the *original* claim — since that lease often hadn't expired yet, the fresh claim attempt silently found nothing to claim (`already_claimed`), and the resume stalled until `sweep_expired_leases` eventually noticed, up to an hour later. Both fixes are proven end-to-end against real Postgres, not just asserted against mocks; see ADR-0033's completion addendum for details.

Full TDD across all 16 tasks, 39 commits, 1101 tests passing. See ADR-0033 in `docs/decisions.md` for the complete design record (options considered, the cost-driven sweeper redesign, and the completion summary) and `docs/architecture.md`'s new "Durable execution, events, and rate limits" section for the resulting flow.

### Security — Close credential-disclosure gap for vector-DB/HTTP/MCP fields (2026-07-13)

Resolved the second item from `docs/deferred-backlog.md` (P0-5), split per user decision into "patch the live disclosure gap now, defer the full Credential/Connection model." Verified against the code (not just the audit) that `GET /workflows`, `/workflows/search`, `/workflows/{id}`, and `GET /mcp-servers` all returned vector-DB API keys, embedding API keys, HTTP node headers, and MCP server headers in plaintext — the only field with encryption/redaction was Jira's `apiToken` (ADR-0028). `McpServer.oauthConfig.clientSecret` was also stored as plaintext JSON at rest even though `McpOAuthToken` access/refresh tokens were already encrypted. Given `isShared` MCP servers are discoverable by any user, the headers gap meant one user's secret headers could be visible to every other user who could see that shared server.

Extended Jira's exact encrypt-at-rest + redact-on-read pattern to all three gaps via new generic, reusable primitives in `src/security/encryption.py` (`encrypt_marked`/`decrypt_marked`, wire-compatible with Jira's existing `enc:v1:` prefix; `redact_sensitive_headers`/`encrypt_sensitive_headers`/`decrypt_sensitive_headers` for header-dict fields, keyed off a `SENSITIVE_HEADER_NAMES` set mirroring the HTTP-node SSRF work's `_SENSITIVE_QUERY_PARAMS` convention) rather than four more copy-pasted implementations. `VectorDbExecutor` and `HttpExecutor` decrypt before `{{variable}}` substitution (decrypt-then-substitute is correct regardless of whether the original field held a literal secret or a template reference); `src/mcp/oauth.py`'s token-exchange and refresh flows decrypt `clientSecret` before building the outbound form. No schema migration — same JSON-column approach Jira already uses. Full TDD; 25 new/updated tests, full suite green (1038 passed). See ADR-0032 in `docs/decisions.md` for the complete design record.

### Docs — Persistence stack ADR: stay on Prisma Python (2026-07-13)

Resolved the first item from `docs/deferred-backlog.md` (P3-1). Verified the archived-repo risk directly (`gh api`: archived, no commits since 2025-04-10), then built and ran a proof-of-concept (`scripts/poc_persistence_row_lock.py`) against the real dev database to test the strongest technical case for migrating — durable-worker row-claiming via `SELECT ... FOR UPDATE SKIP LOCKED`. Result: two concurrent Prisma connections claimed two distinct rows with zero double-claims, confirming Prisma Python's raw-SQL escape hatch already supports this pattern. Decision (ADR-0031 in `docs/decisions.md`): stay on Prisma Python, don't migrate now — the risk is real but hasn't blocked anything concrete, and a 14-model/30-file rewrite isn't justified pre-emptively. Documents concrete trigger conditions for revisiting and an incremental (new-tables-first) migration path if one fires. Unblocks P1-2 (durable workers) to proceed on Prisma directly.

### Fixed — Codex audit remediation: security, correctness, execution-API semantics (2026-07-12)

Working through `docs/claude-improvement-backlog.md` (an independent Codex-authored audit of the codebase). Each item below was implemented test-first (RED/GREEN), verified against the full non-integration suite, and committed separately — see the referenced commits for full rationale and diffs.

#### Security
- **P0-1** — `{{variable}}` references are validated against the workflow's actual node outputs before publish; an unresolved placeholder used to render as literal text at runtime instead of failing fast (`f723efb`).
- **P0-2** — Jira tool calls now return structured outcomes (`ToolCallRecord`: `ok`, `resource_id`, summary) instead of a bare string, so a text-only LLM response with zero tool calls can no longer be mistaken for a successful create/comment. New `action_policy` (`require_tool_call` / `require_successful_tool_call`) makes the distinction enforceable per node (`8ccf91b`).
- **P0-4** — `validate_production_config()` runs at FastAPI startup and refuses to boot with an unsafe production configuration (e.g. missing JWT secret, dev-mode auth fallback enabled) rather than silently running insecurely (`311828f`).
- **P0-6** — SSRF guard (IP-literal + DNS-resolution + cloud-metadata-hostname blocking), a response-size cap, and URL redaction (credentials/sensitive query params stripped before appearing in errors or `node_results`) on the `http` node (`999c5ac`).
- **P0-7** — Jira credential precedence fixed: a `setdefault` bug meant a caller-supplied `variables.jira_api_token` could silently override the node's own configured token. Execution-input boundary hardened alongside it (`7b5c828`). `finalOutput`/`lastOutput` resolution changed from an `or`-fallback to a presence check, so a legitimately falsy `finalOutput` (`0`, `False`, `""`, `[]`, `{}`) isn't discarded in favor of `lastOutput` (`d9b0933`).
- **P0-8** — `_check_reachability`'s validation-time BFS now filters visual-only node types the same way `build_graph`'s compile-time edge projection does, closing a mismatch where a workflow could pass validation but fail to compile (`6697ac3`).
- **P1-1** — Email approval links are scanner-safe: `GET /approvals/email/{token}` now only validates and redirects to a confirmation page; the actual approve/reject decision requires a `POST` from a real form submission. Previously a bare `GET` mutated state, so an email-security scanner or link-preview service auto-following the link could silently resolve the approval before a human saw it. The in-app resume endpoint gained the same atomic conditional-transition guard against a double-click/race (`a8237ac`).
- **P1-3** — Optional `idempotencyKey` on `POST /executions` and `POST /api/run/{slug}`, scoped per workflow: a caller retrying after an HTTP timeout gets the original execution back instead of a duplicate that fires every side-effecting node a second time. Backed by a DB-level unique constraint on `(workflow_id, idempotency_key)`, not application-level check-then-act, so it also closes the race between two genuinely concurrent requests (`cf8f84c`).

#### Fixed
- **P0-3** — Applied outcome-aware routing to the live "BRD to Jira Tickets + Notification" production workflow: an `if-else` node now checks whether Jira actually created issues before sending the success notification, using P0-2's structured outcomes.
- **P1-5** — Every node's `node_results` entry and `node_completed`/`node_failed` WebSocket event now carry `startedAt`/`completedAt`/`durationMs`, added once in `wrap_executor_with_events` (the single wrapper every node type flows through) rather than per-executor. The API docs had already promised a `duration_ms` field that the code never actually produced (`73473ee`).
- **P1-6** — Four execution-API inconsistencies: sync `POST /api/run/{slug}` no longer polls uselessly to `timeoutSeconds` before reporting a hard-coded `running` status for a workflow actually paused at `waiting_approval`; both execution-input size checks now measure true UTF-8 byte length instead of the character length of `json.dumps()`'s escaped (`ensure_ascii=True`) output, which was over-counting non-ASCII payloads 2-6x; `DELETE /executions/{id}` and `POST /executions/delete-bulk` now reject/skip active (`running`/`waiting_approval`) executions instead of deleting live checkpoints out from under an in-flight background task; added `POST /executions/{id}/cancel` — the `canceled` status was always in the public vocabulary but nothing had ever written it (`b3e31a3`).

#### Deferred (require a design/product decision, not silently implemented)
- **P0-5** (credential centralization), **P1-2** (durable workers), **P1-4** (distributed rate limiting/events), **P2-1** (dry-run execution mode — needs a decision on how a fabricated preview outcome should flow into downstream `if-else` branching), **P2-2** (connection management), **P2-3** (module splits), **P2-4** (dependency reproducibility), **P3-1** (persistence-stack ADR).

### Added — File storage provider framework (2026-07-11)

Two new node types built on a shared, pluggable `FileStorageProvider` abstraction: `file-trigger` watches a folder and (via a separate CLI) kicks off a production workflow when a file lands; `file-write` writes generated content out to a file as Markdown, Word, or PDF. Only a local-filesystem provider is implemented today — S3/Google Drive/OneDrive are documented extension points behind the same interface.

#### Added
- `FileStorageProvider` ABC + `FileRef`/`HealthStatus` in [src/storage_providers/base.py](src/storage_providers/base.py), modeled on the existing `ToolProvider` pattern; `LocalFilesystemProvider` in [src/storage_providers/local.py](src/storage_providers/local.py) — polling-based, partial-write-safe (a file must be stable across two consecutive polls before it's claimed).
- `file-trigger` node type — `FileTriggerNodeData`/`FileTriggerNode` in [src/engine/workflow.py](src/engine/workflow.py) (fields: `provider`, `sourcePath`, `destPath`, `errorPath`, `targetInputVariable`, `pollIntervalSeconds`). Visual-only like `note` — never executes; `graph_builder.py`'s new `_VISUAL_ONLY_TYPES` set (`{"note", "file-trigger"}`) skips it during graph construction. Frontend: `node-panels/file-trigger.tsx` + the usual palette/visuals/canvas-type registries.
- `file-write` node type — `FileWriteNodeData`/`FileWriteNode` in [src/engine/workflow.py](src/engine/workflow.py) (fields: `provider`, `destinationPath`, `filename`, `format` — `md`/`docx`/`pdf`, default `md` — `content`) + `FileWriteExecutor` in [src/executors/file_write.py](src/executors/file_write.py), a real executing node. Writes via `LocalFilesystemProvider`; `lastOutput` is the full written path. Frontend: `node-panels/file-write.tsx` + registries.
- Markdown → DOCX renderer, [src/conversion/markdown_to_docx.py](src/conversion/markdown_to_docx.py) — hand-rolled `markdown-it-py` token-walker onto `python-docx`, covering headings/paragraphs/lists/tables/bold/italic/inline-code (table cells get full run-level formatting, not just plain text).
- Markdown → PDF renderer, [src/conversion/markdown_to_pdf.py](src/conversion/markdown_to_pdf.py) — `markdown-it-py` → HTML → `xhtml2pdf`.
- `composer watch` CLI subcommand — [src/cli/watch.py](src/cli/watch.py) + new `watch` parser in [src/cli/main.py](src/cli/main.py) (`--api-url`, `--slug`, `--api-key`, `--provider`, `--source`, `--dest`, `--error`, `--target-var`, `--interval`). Polls a folder, extracts text (`.txt`/`.md`/`.pdf`/`.docx`), triggers the target **production** workflow through the existing `POST /api/run/{slug}` external-invoke endpoint (no new backend endpoint), and moves each file to a dest or error folder based on outcome.
- Two new dependencies in `pyproject.toml`: `markdown-it-py`, `xhtml2pdf`.
- `docs/designer-guide.md` gained `file-trigger`/`file-write` node-reference sections (now 22 node types).

#### Security
- `file-write` rejects filenames containing `/`, `\`, or `..` (`InvalidFilenameError`) and rejects a blank/empty `destinationPath` (`InvalidDestinationError`) — both raise before any write happens. `filename`/`destinationPath` are populated via `{{variable}}` substitution from upstream node output, so an unchecked value could otherwise escape the intended output directory.
- The PDF export path blocks all external resource resolution — `markdown_to_pdf` passes a deny-all `link_callback` into `xhtml2pdf.pisa.CreatePDF` — preventing SSRF via a crafted Markdown image reference (mirrors the HTTP-node SSRF guard, commit `999c5ac`). Both the DOCX and PDF renderers construct their `MarkdownIt` parser with `html: False`, disabling CommonMark's raw-HTML passthrough, so bracket placeholders like `<Client Name>` render as literal text instead of being silently dropped.

#### Notes
- See ADR-0030 in [docs/decisions.md](docs/decisions.md) for the full design record, including why `file-trigger` is visual-only, why `composer watch` reuses the existing external-invoke endpoint rather than a new trigger endpoint, why conversion is pure-Python rather than pandoc-based, and why storage config is inline-per-node rather than a shared connection registry.

### Added — Approve-via-email + waiting-approval auto-expiry (2026-07-11)

A `user-approval` node can now also notify the reviewer by email: set **Approver email** (and optionally **Approver CC**) on the node, and the moment the graph pauses, the reviewer gets a message with two signed one-click links (Approve / Reject) — no Composer login required. Separately, a `waiting_approval` execution that sits with no decision at all (in-app or via email) for more than 7 days is now auto-failed by a background sweeper, purely to bound Postgres/checkpoint row growth; a paused execution costs zero compute while waiting (its full state is checkpointed to Postgres), so this is hygiene, not a resource necessity.

#### Added
- New `approval_email` JWT type — `create_approval_email_token`/`verify_approval_email_token` in [src/security/jwt.py](src/security/jwt.py); payload bakes in `sub` (execution id), `node_id`, `decision`, `approver_email`, `pending_since` so the decision itself is signed, not a mutable query parameter.
- `UserApprovalNodeData.approver_email`/`approver_cc` (aliases `approverEmail`/`approverCc`) in [src/engine/workflow.py](src/engine/workflow.py); `UserApprovalExecutor` (`src/executors/user_approval.py`) folds the substituted values into the `interrupt()` payload.
- `send_approval_email` in [src/engine/approval_email.py](src/engine/approval_email.py) — no-ops if `approverEmail` is empty; otherwise sends two signed approve/reject links via the existing Resend integration. Called only from the one-shot "just detected a fresh pause" branches in [src/engine/langgraph_executor.py](src/engine/langgraph_executor.py)'s `run()`/`.resume()` — never from inside the executor itself, since LangGraph replays a node's function body on resume and a send placed earlier would re-fire on every resume.
- Public, unauthenticated `GET /approvals/email/{token}` in [src/api/approval_email.py](src/api/approval_email.py) (`resolve_approval_email`) — verifies the token, checks it matches the execution's current pause (`node_id` AND `pending_since`, so a `while`-loop node pausing repeatedly at the same `node_id` can't have a stale token resolve a later pause), atomically transitions status via a conditional `update_many` (`WHERE id=... AND status='waiting_approval'`) to prevent double-processing (e.g. an email-security-gateway link-scanner prefetching both links, or a duplicate click), records an `Approval` row, schedules `resume()` in the background, and redirects (303) to `{FRONTEND_URL}/approval-result?status=approved|rejected|invalid`. Rate-limited via new `rate_limit_approval_email_per_minute` setting (default 20/min).
- `Approval.approverUserId` is now nullable, plus new `approverEmail`/`viaEmailLink` columns in `prisma/schema.prisma` — an email-link decision has no authenticated Composer user behind it.
- New sweeper function `sweep_expired_approvals` in [src/maintenance/execution_sweeper.py](src/maintenance/execution_sweeper.py) — auto-fails `waiting_approval` executions past `approval_wait_timeout_hours` (default 168h/7 days) since `variables._pending_approval_since` was stamped, independent of the 72h emailed-link TTL. Runs on the same interval tick as the existing stuck-`running` sweeper.
- New settings in [src/config.py](src/config.py): `backend_public_url` (base URL for building emailed links against the backend, not the frontend), `approval_link_ttl_hours` (default 72), `approval_wait_timeout_hours` (default 168), `rate_limit_approval_email_per_minute` (default 20).
- Frontend: `frontend/components/composer/canvas/node-panels/user-approval.tsx` gained Approver email/CC input fields; new unauthenticated `frontend/app/(auth)/approval-result/page.tsx` renders a distinct approved/rejected/invalid confirmation state.
- `docs/designer-guide.md` `user-approval` node-reference section updated with the email-approval behavior, link TTL, and auto-expiry.
- `UserApprovalNodeData.attachment_path` (alias `attachmentPath`) in [src/engine/workflow.py](src/engine/workflow.py) lets a `user-approval` node attach a file to the approval email, substituted from workflow state (typically `{{lastOutput}}` from an upstream `file-write` node). `_build_attachment` in [src/engine/approval_email.py](src/engine/approval_email.py) reads and base64-encodes it, bounded to a configured root directory (new `approval_attachment_root` setting in [src/config.py](src/config.py), default `/tmp/composer-attachments`) and size cap (new `approval_attachment_max_bytes` setting, default 10 MiB) — anything outside those bounds, or any other read/encode error, is skipped with a warning and the email still sends without it. Frontend: `frontend/components/composer/canvas/node-panels/user-approval.tsx` gained the Attachment path input field.

#### Notes
- See ADR-0029 in [docs/decisions.md](docs/decisions.md) for the full design record, including why signed links were chosen over reply-parsing, why no login is required, why the sweeper timeout is independent of the link TTL, and confirmation that paused executions hold no live compute between pause and resume.

### Fixed — user-approval nodes misreported as failed in the run trace (2026-07-11)

A `user-approval` node pausing for a decision (via LangGraph's `interrupt()`) was showing up in the run trace as `failed`, with the raw `GraphInterrupt` exception dumped as the error text — even though the execution's overall status correctly ended up `waiting_approval` once `LangGraphExecutor.run` inspected the post-`ainvoke` state.

**Root cause:** `wrap_executor_with_events`'s `except Exception as exc:` block (`src/engine/events_wrapper.py`) is generic enough to catch `GraphInterrupt`, which is a `GraphBubbleUp` subclass — LangGraph's own control-flow signal for pausing a graph, not a real failure. The wrapper emitted a `node_failed` WebSocket event (with the exception's raw text as the error) before re-raising, so the per-node UI showed a crash for what was actually an intentional approval-gate pause. The installed LangGraph version (1.1.8) still catches `GraphInterrupt` correctly inside `ainvoke()` itself — this was a Composer-side wrapper bug, not a LangGraph regression.

**Fix:** added an `except GraphBubbleUp: raise` branch before the generic handler in `src/engine/events_wrapper.py`, so LangGraph's interrupt/resume signal (and the related `NodeInterrupt`/`ParentCommand` subclasses) propagate without emitting `node_failed`. The `approval_required` event (already emitted correctly by `LangGraphExecutor.run`) remains the sole signal for a pending approval.

New test: [tests/unit/engine/test_events_wrapper.py](tests/unit/engine/test_events_wrapper.py) (`test_wrapper_does_not_emit_node_failed_on_graph_interrupt`).

### Added — Jira node (2026-07-10)

A new `jira` node type: drag it onto the canvas, configure Jira Cloud domain/email/API token directly on the node, write a prompt, and the LLM picks which of 6 Jira REST API v3 tools to call (create/get/search/update/transition issue, add comment). Per-node, per-workflow credentials — no shared MCP registration or admin-managed key required. `JiraProvider` (`src/tools/providers/jira.py`) is an internal implementation detail of this node only — it is deliberately not registered in the tools catalog, so Jira does not also show up as a selectable tool on `agent`/`mcp` nodes.

A security review of the initial implementation (before this entry's fixes) found the API token was stored and returned in plaintext on every workflow read, and that the frontend's camelCase `apiToken` field silently failed to populate the backend model at all. Both are fixed here; see ADR-0028 for the full record.

#### Added
- `jira` node type — [src/engine/workflow.py](src/engine/workflow.py) (`JiraNodeData`/`JiraNode`), [src/executors/jira.py](src/executors/jira.py) (`JiraExecutor` — agentic loop over all 6 Jira tools), [src/tools/providers/jira.py](src/tools/providers/jira.py) (`JiraProvider` + 6 tool classes), [frontend/components/composer/canvas/node-panels/jira.tsx](frontend/components/composer/canvas/node-panels/jira.tsx), wired through the executor registry, graph builder, tools palette, property panel, node visuals, and catalog.
- Jira apiToken encryption-at-rest (`encrypt_jira_api_token`/`decrypt_jira_api_token`/`is_jira_api_token_encrypted` in [src/engine/workflow.py](src/engine/workflow.py), reusing the existing AES-256-GCM primitive in [src/security/encryption.py](src/security/encryption.py)) and redaction-on-read (`_to_workflow_read`/`_redact_jira_tokens` in [src/api/workflows.py](src/api/workflows.py)) — the token is never returned over HTTP in plaintext or ciphertext after the first save.
- `docs/designer-guide.md` `jira` node-reference section (now 20 node types); `docs/admin-guide.md` caveat distinguishing the Jira per-node token from the centrally-managed LLM-key masking convention.
- 10 new tests: [tests/unit/executors/test_jira_executor.py](tests/unit/executors/test_jira_executor.py) (5 — alias round-trip, decrypt-before-use, legacy-plaintext tolerance, LangSmith threading, encrypt/decrypt round-trip), [tests/unit/api/test_workflows_jira_tokens.py](tests/unit/api/test_workflows_jira_tokens.py) (5 — encrypt-on-create, redact-on-get/list, preserve-on-unchanged-marker, encrypt-on-new-value).

#### Fixed
- `JiraNodeData.api_token` had no `alias="apiToken"`, unlike every sibling field — the frontend's camelCase payload silently failed to populate it (Pydantic's `extra="allow"` absorbed the mismatch rather than raising), so every UI-created Jira node ran with no credentials at all.
- `JiraExecutor` now threads `langsmith_config=get_current_langsmith()` through its `build_chat_model` call, matching every other LLM-invoking executor (CLAUDE.md fix #6) — the initial implementation had silently omitted this.
- Stale module docstring in `src/tools/providers/jira.py` listed 5 tools, omitting `jira_update_issue`; pre-existing `reportAttributeAccessIssue` pyright errors on the `_domain`/`_email`/`_api_token` tool-instance assignments (untyped `tools: list[BaseTool]` local) suppressed/annotated to keep `pyright src tests` at 0 errors.
- `jira` was missing from `COMPOSER_NODE_TYPES` in [frontend/components/composer/canvas/workflow-canvas.tsx](frontend/components/composer/canvas/workflow-canvas.tsx), so React Flow fell back to its default node renderer instead of the app's standard chip + single-target/single-source handle layout — a dropped Jira node looked visually inconsistent with every other node type and had non-standard connection points.
- `JiraProvider` had been registered via `@register_tool_provider`, which also surfaced Jira as a selectable tool on `agent`/`mcp` nodes (in the shared tools catalog, [frontend/lib/api/catalog.ts](frontend/lib/api/catalog.ts)) alongside the standalone `jira` node — two ways to do the same thing, which was never the intended design. Removed the registration and the catalog entry; `JiraExecutor` still uses `JiraProvider`'s tool implementations directly (unaffected, since it instantiates the class rather than going through the registry).

#### Notes
- See ADR-0028 in [docs/decisions.md](docs/decisions.md) for the full design record, including why no backfill migration was written for pre-existing plaintext tokens (none existed — the feature was still in review).

### Added — Self-service "Forgot password?" flow (2026-07-10)

Additive to the admin-only reset shipped under ADR-0024: a standard email-link flow for users who forgot their password and have no admin handy, reusing the existing `password_change` JWT mechanism rather than inventing a second token concept.

#### Added
- **`POST /auth/forgot-password`** and **`POST /auth/reset-password`** in [src/api/auth_standalone.py](src/api/auth_standalone.py). `forgot-password` always returns `204` — whether the email matches a real, password-based account, an SSO-only account, or nothing at all — closing the account-enumeration vector; email-delivery failures are caught and logged, never surfaced. `reset-password` verifies the token directly via `verify_password_change_token` (deliberately not the dependency that also accepts normal access tokens, since this route must be reachable by a fully anonymous caller) and sets the new password without requiring the current one.
- **`send_password_reset_email`** helper in [src/integrations/email/resend.py](src/integrations/email/resend.py), reusing the existing `ResendEmailProvider` already wired up for the workflow email-node executor — no new email infrastructure.
- **`/forgot-password` and `/reset-password` pages** in `frontend/app/(auth)/`, a "Forgot password?" link on the login page, and matching client helpers `composerForgotPassword`/`composerResetPassword` in [frontend/lib/composer-api.ts](frontend/lib/composer-api.ts). [frontend/middleware.ts](frontend/middleware.ts) excludes both routes so they're reachable while logged out.
- Config: `resend_from_email`, `frontend_url` (used to build the absolute reset link in the email), `rate_limit_forgot_password_per_minute` (default 5/min/IP) in [src/config.py](src/config.py).
- 16 new tests: [tests/unit/api/test_auth_forgot_password.py](tests/unit/api/test_auth_forgot_password.py) (4), [tests/unit/api/test_auth_reset_password.py](tests/unit/api/test_auth_reset_password.py) (6), [tests/unit/integrations/test_resend_password_reset.py](tests/unit/integrations/test_resend_password_reset.py) (2), plus 4 frontend cases in [frontend/lib/auth.test.ts](frontend/lib/auth.test.ts).

#### Changed
- `jwt_password_change_ttl_seconds` bumped from 600 (10 min) to 1800 (30 min) — shared by both the admin-handoff case (interactive) and the new email case (user needs time to check their inbox).

#### Fixed
- `/auth/reset-password` now rejects a deactivated account, matching the `isActive` check already enforced everywhere else a standalone JWT authorizes an action — a stale reset link could otherwise reactivate a deactivated user.

#### Notes
- See ADR-0027 in [docs/decisions.md](docs/decisions.md) for the full design record.

### Added — Designer autosave (2026-07-10)

Root-caused a reported "reassigning a workflow's owner leaves only Start+End nodes" bug: the reassign endpoint never touched flow data — the Designer simply had no autosave, so an owner who edited and never clicked Save left the database row holding only its creation-time scaffold, which became visible the moment a different assignee opened it fresh.

#### Added
- **`useAutosave`** hook in [frontend/lib/use-autosave.ts](frontend/lib/use-autosave.ts) — a 3-second debounced autosave that reuses the existing manual-save mutation and `PUT /workflows/{id}` path (no new backend endpoint). A `saveNow()` variant backs the manual Save button so both paths share one status state machine (`idle → dirty → saving → saved/error`).
- Save-status indicator wired into the Designer canvas (`frontend/app/designer/[workflowId]/page.tsx`, [frontend/components/composer/canvas/save-controls.tsx](frontend/components/composer/canvas/save-controls.tsx)), plus a `beforeunload` guard that blocks tab-close while unsaved.

#### Fixed
- Autosave no longer flags a workflow dirty on initial canvas mount — fixed by comparing the *content* of each `onNodesChange`/`onEdgesChange` callback's incoming nodes/edges against a baseline captured once per mount, rather than an invocation counter (which broke under React 18 Strict Mode's mount-effect double-invoke).
- The page's inner component now remounts (`key={params.workflowId}`) on every workflow switch, since Next.js's App Router doesn't guarantee a remount on a dynamic-route param change alone — without it, the mount-vs-edit baseline leaked from the previously open workflow into the next.

#### Notes
- Last-write-wins remains the concurrency model; no multi-editor conflict resolution was introduced. See ADR-0026 in [docs/decisions.md](docs/decisions.md).

### Added — Workflow assignment & sharing (2026-07-09)

A workflow could previously only ever have one owner. Adds many-to-many sharing as a layer on top of the existing single-owner field, which keeps its existing meaning (who created it / who can transfer or delete it) unchanged.

#### Added
- **`WorkflowAssignment`** join table (`workflowId`, `userId`, `assignedById`, `assignedAt`; unique on `(workflowId, userId)`) — assignment grants full read+write access (open, edit, run); there is no view-vs-edit split.
- **Assignment CRUD** in [src/api/workflows.py](src/api/workflows.py) — grant/revoke, owner-or-admin only; delete and owner-transfer remain untouched by assignment.
- **`GET /users/search`** — lets non-admin workflow owners find people to share with (the existing `/admin/users` listing is admin-only); results limited to id/email/displayName.
- **`ManageAssigneesDialog`** wired into the admin and owner UI, with a "Shared" badge on assigned-not-owned workflows in the workflow list.
- `GET /workflows/{id}` / `PUT /workflows/{id}` authorization gained an assignee check (`_has_assignment`) alongside the existing owner/admin/`isPublic` checks; both `list_workflows` and `search_workflows` gained a matching `OR` clause so assigned-not-owned workflows are visible and findable everywhere in the UI.
- Integration test covering the full assignment cycle: [tests/integration/test_workflow_assignments.py](tests/integration/test_workflow_assignments.py).

#### Fixed (caught during code review)
- Double-grant now returns `409` instead of an uncaught 500; double-revoke checks `delete()`'s return value for `None` (Prisma Python's generated `delete()` already swallows `RecordNotFoundError` internally, so the original `except RecordNotFoundError` was dead code).
- `ManageAssigneesDialog` tracked one shared `isPending` flag across every row, letting a rapid second click hit the backend's 409/404 before the invalidated query's refetch removed the row — fixed with per-row pending-id tracking.
- The owner-settings "Manage access" section rendered for anyone who could load the page, including non-owner assignees who are correctly 403'd by the assignment-management endpoints — gated on `role === "admin" || workflow.userId === currentUserId`.

#### Notes
- Per-assignee permission levels (view-only, etc.) are explicitly out of scope for this pass. See ADR-0025 in [docs/decisions.md](docs/decisions.md).

### Security — rate-limit GET /users/search (2026-07-09)

`GET /users/search` (added for the account/workflow-sharing feature to let non-admin workflow owners find people to share with) was reachable by any authenticated member with no per-caller rate limit, letting a scripted caller reconstruct most of the user directory (email + displayName) via repeated queries.

#### Fixed
- [src/api/users.py](src/api/users.py) — `search_users` now calls `enforce()` with a new `rate_limit_users_search_per_minute` setting (default 30/min, keyed on the authenticated user id), matching the pattern used by other broad-principal endpoints (`executions`, `mcp_test`).
- [src/config.py](src/config.py) — added `rate_limit_users_search_per_minute: int = 30`.

### Security - enforce credential purpose, account status, and workflow execution scope (2026-06-02)

#### Fixed
- Standalone JWT-protected routes and WebSocket subscriptions now reject refresh tokens presented as bearer access tokens.
- `POST /executions` now applies the standard workflow authorization policy: members may execute their own or public workflows; admins may execute any workflow.
- Deactivated standalone users can no longer refresh sessions, exchange Azure SSO tokens, use existing JWTs, or invoke production workflows with API keys.
- Embedded-mode WebSocket authentication now uses the embedded JWT verifier instead of the standalone Composer secret.

### Improvement — Admin LLM-key saves take effect without a restart (2026-05-27)

The boot-time `key_sync` only ran during the FastAPI lifespan, so after an admin saved or deleted a key via the UI the new value didn't reach `settings.<provider>_api_key` until the next revision restart. Workflows on the same revision kept reporting "key missing" until ops forced a Cloud Run roll (the `_KEYS_REENTERED_AT` trick).

#### Added
- `PROVIDER_TO_SETTINGS_FIELD` is now exported from [src/security/key_sync.py](src/security/key_sync.py) so other modules can reuse the same provider→Settings-field mapping.
- 2 new tests in [tests/unit/api/test_admin_llm_keys.py](tests/unit/api/test_admin_llm_keys.py): PUT updates `settings.anthropic_api_key`; DELETE clears `settings.openai_api_key`.

#### Changed
- [src/api/admin_llm_keys.py](src/api/admin_llm_keys.py) — `upsert_llm_key` (PUT) now writes the plaintext into the cached `Settings` instance after persisting to Postgres. `delete_llm_key` (DELETE) clears the same field. Effect: key changes are visible to subsequent workflow executions on the same Cloud Run instance immediately, no restart needed.

#### Notes
- Multi-instance Cloud Run deployments (`--max-instances >1`) still see stale values on instances that didn't handle the PUT, until those instances' next boot. The boot-time sync continues to catch them on restart. For Composer's typical 1-instance steady-state this is a non-issue; documented for completeness.
- DELETE clears the in-memory field even if the same env var is set in .env / Secret Manager. On next boot the env-wins rule restores the env value — that's correct behaviour: deleting a DB row shouldn't permanently shadow operator-set env vars.

### Fix — Admin "Test connection" probes for Gamma and LangSmith (2026-05-27)

#### Changed
- [src/api/admin_llm_keys_test.py](src/api/admin_llm_keys_test.py) — `_test_gamma` was hitting `https://api.gamma.app/public/v1/generations?limit=1` (returns 404; that host+path doesn't exist). Repointed at the same base URL the executor uses (`https://public-api.gamma.app/v1.0`) and switched the probe to `GET /generations/composer-keytest-probe`. Gamma checks auth before the lookup, so a valid key returns 404 (treated as ok) and an invalid key returns 401. Verified live against the production Gamma API; no generation is created.
- `_test_langsmith` was hitting `GET /api/v1/runs?limit=1` which is now POST-only (returns 405). Switched to `GET /api/v1/sessions?limit=1` — lists tracing projects, accepts `x-api-key`, returns 200/401/403. Verified live.

### Fix — Admin-UI LLM keys now reach workflow runtime (2026-05-27)

Phase 9e made Postgres the source-of-truth for provider keys and added a
`composer keys sync --target vercel` deploy-time bridge — but Cloud Run
deployments had no equivalent.  An admin who saved a key via the UI
updated Postgres, but workflow execution still read `settings.<provider>_api_key`
(env vars only) at runtime; the env was frozen at deploy time, so
admin-UI edits never reached the workflow.  Symptom: workflows reported
"Google API Key is missing" while the admin UI showed the key present.

#### Added
- **`src/security/key_sync.py`** — `sync_llm_keys_from_db(db)` runs once
  during the FastAPI lifespan and decrypts every `llm_api_keys` row into
  the cached `Settings` instance.  Env-set values win (preserves the
  documented "DEV-ONLY override" semantic); DB fills in the blanks.
  Covers all ten providers tracked by Phase 9e (anthropic, openai,
  google, groq, langsmith, tavily, firecrawl, serper, browserless, gamma).
- **6 unit tests** in [tests/unit/security/test_key_sync.py](tests/unit/security/test_key_sync.py):
  empty-fields populate; env-set values preserved; unknown providers
  skipped with warning; decrypt failures skipped without crashing
  (the exact mode an ENCRYPTION_KEY rotation produces); DB query
  failures non-fatal; empty table is a no-op.

#### Changed
- [src/main.py](src/main.py) lifespan now calls `sync_llm_keys_from_db(db)` after
  Prisma connects, before the sweeper starts.  Boot-time cost is one
  `SELECT * FROM llm_api_keys` + N AES-256-GCM decrypts; negligible.
- [.env.example](.env.example) — documented the new lookup order and clarified that
  blank LLM-key env vars now mean "use the DB", not "broken config".

#### Notes
- A Cloud Run revision restart (which a redeploy already does) is the
  pickup signal for admin-UI key edits.  This is consistent with how
  every other env-var-style setting works on Cloud Run.
- An ENCRYPTION_KEY mismatch (e.g. after rotation without re-encryption)
  surfaces as a per-row decrypt warning at startup — the right operator
  signal — and the affected provider falls back to its env var (likely
  blank, surfacing the existing "key missing" error at workflow time).

### CI — Docker layer caching on the GCP deploy workflow (2026-05-27)

#### Changed
- [.github/workflows/deploy-gcp.yml](.github/workflows/deploy-gcp.yml) — backend and frontend image builds now use `docker/build-push-action@v6` with `setup-buildx-action@v3` and `type=gha` cache (scoped `backend` vs `frontend`, `mode=max` so intermediate layers are exported too — important because our backend Dockerfile is single-stage with several expensive layers: apt+Node 20 install, `uv sync --frozen`, `prisma generate`). First run after this change is still cold; subsequent pushes that don't touch dep manifests should rebuild in ~30–60s instead of ~3 min.

### Fix — Standalone+production CORS allowlist (2026-05-27)

The Phase 7a CORS branch only plumbed an allowed origin for embedded mode (`IEP_UI_ORIGIN`). Standalone + production fell through to `allow_origins=[]`, so the Cloud Run frontend's browser preflights to the Cloud Run backend were all rejected. The UI surfaced this as "Could not load MCP servers" (and the same blank-data state on every other admin page), because the React Query landed in `isError`.

#### Added
- **`COMPOSER_FRONTEND_ORIGINS` setting** in [src/config.py](src/config.py). Comma-separated list of exact origins (scheme+host+port) that the backend's CORS middleware should accept in standalone+production. Mirrors the embedded-mode `IEP_UI_ORIGIN` plumbing but supports multiple values so operators can list both the `*.run.app` URL and a custom domain.
- **Startup warning** in [src/main.py](src/main.py) when standalone+production boots with the env var empty — the silent symptom this incident produced should be visible at deploy time, not as a CORS error in the browser.
- **CORS unit tests** in [tests/unit/test_main.py](tests/unit/test_main.py) covering all four branches (standalone-dev `*`, standalone-prod allowlist, standalone-prod empty + warning, embedded uses `iep_ui_origin` and ignores the standalone var).

#### Changed
- [scripts/gcp-bootstrap.ps1](scripts/gcp-bootstrap.ps1) — new step 6 that runs after the frontend deploys, computes the allowlist (`*.run.app` URL + custom domain if set), and pushes it to the backend service via `gcloud run services update --update-env-vars`.
- [.github/workflows/deploy-gcp.yml](.github/workflows/deploy-gcp.yml) — new `update-backend-cors` job that depends on `deploy-frontend` and applies the same update. Honors the existing `COMPOSER_FRONTEND_URL` repository variable when a custom domain is in play.

#### Verified
- 16/16 affected unit tests green (5 new in `test_main.py`, 2 new in `test_config.py`).
- Ruff lint + format: clean.
- Pyright strict: 0 new errors on changed files.

### Reliability — Execution-status truth + MCP base64 hygiene (2026-05-04)

Closing the OAB-reported defect class documented in [docs/archive/incident-history/2026-04-30-execution-status-truth.md](docs/archive/incident-history/2026-04-30-execution-status-truth.md). Audit found Composer's primary persistence + completion-signal paths were already correct; the remaining gaps were the resilience layers around them.

#### Added
- **MCP base64 binary-blob sanitizer** ([src/mcp/sanitize.py](src/mcp/sanitize.py)). Strips Highspot's `Base64 Encoded Content` preamble (replaces with a metadata-only stub that names the item + tells the agent not to retry) and any generic >4KB fenced base64 run from MCP tool responses before they reach the agent's conversation history. Wired into `_render_content_blocks` so every text-channel response runs through it. Closes the second defect from the OAB incident write-up — a single Highspot xlsx fetch was burning ~20K tokens and OOMing the agent context after a few calls.
- **Stuck-execution sweeper** ([src/maintenance/execution_sweeper.py](src/maintenance/execution_sweeper.py)). Background task scheduled in the FastAPI lifespan that flips `running` rows older than `execution_stuck_after_seconds` (default 15 min) to `failed` with an explanatory error. Survives worker SIGKILLs, serverless function-timeout terminations, and any future bug that skips the executor's persist path. Configurable interval (`execution_sweeper_interval_seconds`, default 5 min); set to 0 to disable in environments that prefer an external cron.
- **Resilient detached-task wrapper** in `POST /api/run/{slug}` ([src/api/run.py](src/api/run.py) `_run_with_persistence`). The `asyncio.create_task(executor.run(...))` for async invocations now goes through a wrapper that catches uncaught crashes + cancellations, stamps `failed` on the row directly, and re-raises so the task carries the original exception. Belt-and-braces alongside the sweeper.
- **Config knobs**: `execution_stuck_after_seconds`, `execution_sweeper_interval_seconds` in [src/config.py](src/config.py).

#### Audited (no change required)
- `LangGraphExecutor._mark_failed` is `await`ed inside the executor's `try/except` before the task returns — Bug 1 of the OAB write-up never applied.
- The designer execution panel dispatches solely on `ev.type === "workflow_completed"` and cross-checks via a 2-second DB poll fallback — Bug 2 of the OAB write-up never applied.

#### Notes
- The truth doc is filed under `docs/archive/incident-history/` with a status header listing what landed in Composer for each recommendation.
- The new `_run_with_persistence` wrapper mirrors the resilience pattern OAB shipped in commit `2888e4d`; the sweeper is OAB's deferred recommendation D.

#### Verified
- 711/711 unit tests green (692 baseline + 19 new: 7 sanitizer + 8 sweeper + 4 wrapper).
- Pyright strict: 0 errors.
- ruff lint + format: clean.
- Frontend tsc: clean (no UI changes).

### Phase 10 — Polish: 5 more reference templates (2026-04-28)

#### Added
- **Template 13 — Meeting Transcript to Action Items.** Upload a transcript (PDF/DOCX/MD/TXT); first agent extracts action items as JSON (owner, task, due date, priority); second agent drafts a polished follow-up email referencing each item. Pairs with the new document-upload feature.
- **Template 14 — Customer Support Triage.** Pure-LLM (no external deps) classify-and-branch reference: classifier agent emits `{urgent, category, summary}` JSON; if-else branches on `urgent`; specialist agents draft urgent or standard replies. Runs on any fresh instance.
- **Template 15 — Gamma AI Presentation Generator.** Topic + audience in, Gamma-hosted slide URL out. Tavily-grounded research agent produces a slide outline; gamma-ai node renders the deck; final agent surfaces the URL. First template to exercise the gamma-ai node.
- **Template 16 — Code Review Assistant.** Paste a diff; review agent flags issues with severity tags + structured JSON; guardrails (PII) screens the OUTPUT for accidental secret/credential leaks; if-else delivers either the formatted review or a redacted warning. Combines agent JSON mode + guardrails on downstream content + branched delivery.
- **Template 17 — Lead Enrichment.** Company name in, CRM-shaped JSON profile out. Multi-source research (Tavily + Firecrawl) feeds into a structured-extraction agent producing a schema-validated record (industry, size, products, recent news, executives, competitors).

#### Changed
- Replaced unicode arrows (`→`) and em-dashes (`—`) in template names with ASCII equivalents — Windows cp1252 stdout couldn't encode them and broke the seed script's progress output. Names are now reliably round-trippable across platforms.

#### Notes
- All five templates use the existing node primitives + the recently shipped document-upload + transform `outputKey` features. No backend changes were needed to ship these.
- Template 14 is the only one that runs on a brand-new Composer instance with zero external API keys — a reliable smoke test for the classify-and-branch pattern.

#### Verified
- 683/683 unit tests still green (templates are data-only — no executor changes).
- Seed script: 12 templates updated, 4 created (templates 14-17). Template 13 already existed from a partial prior run; updated cleanly.
- ruff + format clean.

### Phase 10 — Polish: document upload + extraction (2026-04-28)

#### Added
- **`POST /uploads/extract-text` endpoint** — multipart upload of `.txt` / `.md` / `.markdown` / `.pdf` / `.docx`, returns extracted plain text. No persistence: file is read in-memory (10 MB cap), text is extracted in-flight, bytes are released. Per-user rate limit 20/min.
  - Plain text: UTF-8 with BOM tolerance, latin-1 fallback for non-UTF-8 input.
  - PDF: pypdf page-by-page extraction; pages with bad content streams are skipped rather than failing the upload.
  - DOCX: python-docx paragraph + table extraction; headers/footers/inline images intentionally skipped.
  - Routes on file extension (Chrome misreports content-type for some formats); content-type only a hint.
- **`document` start input type** in workflows. Run-input form renders a file picker for these inputs; on select it uploads to `/uploads/extract-text` and stashes the returned text as the input value. Engine sees a regular string variable — no special handling, downstream nodes reference `{{policy_doc}}` exactly the way they reference any other text input.
- **OAB compatibility shim**: start input variables saved as `type: "string"` (OAB's name for plain text) now alias to Composer's `text` type so workflows imported from OAB render correctly without re-saving every input declaration.

#### Changed
- **Template 12 (Document Ingestion)** updated to use the new `document` input type. Two-node pipeline now: Start (file picker) → Vector-DB upsert → Summary agent. Removes the previous Firecrawl-or-raw-text branching since uploaded files are the more useful path.

#### Notes
- Why no persistence: keeps Composer infrastructure-light (no S3, no Convex blobs). For workflows that need to keep the file around — re-running with the same input, audit, cross-execution reference — the designer can re-upload or wire up an HTTP node to fetch from their own storage.
- Why server-side text extraction (vs. client-side pdf.js): a ~500KB pdf.js bundle on every workflow run page felt heavy when pypdf already sits server-side and gives cleaner extraction. The trade-off is that uploads round-trip; for large PDFs the form shows an "Extracting…" indicator.
- The 10MB cap covers most policy docs / contracts / meeting transcripts. Larger inputs should chunk upstream — Template 12's ingestion path handles that naturally via vector-db's auto-chunking.

#### Verified
- 683/683 unit tests green (Phase-10-polish baseline 673 + 10 new uploads tests covering each format, BOM/latin-1 fallbacks, extension-priority dispatch, oversized rejection, empty-file rejection, and PDF dispatch via mock).
- Pyright 0 errors, ruff + format + frontend tsc all clean.
- Seed script: 11 templates updated, including Template 12 reworked to use the document input.

### Phase 10 — Polish: vector-db upsert + ingestion template (2026-04-28)

#### Added
- **vector-db node now supports `upsert` mode** alongside `query`. Schema gains `vectorDbOperation`, `vectorDbDocuments`, `vectorDbChunkSize`, `vectorDbChunkOverlap`. The `documents` field is a simpleeval expression that resolves to either:
  - a list of `{id?, text, metadata?}` dicts (pre-chunked, recommended for production where stable ids matter), OR
  - a list of strings (each becomes a chunk verbatim), OR
  - a single string (auto-chunked using char-window with overlap).
  The executor embeds each chunk via OpenAI then dispatches to the provider's upsert function.
- **Upsert implementations for all 5 providers**:
  - **Pinecone** — `POST /vectors/upsert`, body `{vectors: [{id, values, metadata}]}`. Stable sha1-derived ids so re-upserts of identical text overwrite cleanly.
  - **Qdrant** — `PUT /collections/{c}/points?wait=true`, points keyed by UUIDv5 derived from chunk text.
  - **Chroma** — `POST /api/v1/collections/{c}/upsert` with column-major arrays.
  - **Weaviate** — `POST /v1/batch/objects` with PascalCase class normalisation, UUIDv5 ids.
  - **Milvus** — `POST /v1/vector/insert` (Zilliz Cloud REST shape), 63-bit positive int ids hashed from chunk text.
- **Vector-db panel exposes upsert mode** — operation dropdown at the top toggles between query-only and upsert-only fields. Query mode shows the original prompt + top-k + score threshold; upsert mode shows the documents expression + chunk-size + chunk-overlap.
- **Template 12 — Document Ingestion (Vector DB Upsert).** Companion to template 11: scrape a URL with Firecrawl (or paste raw text), then upsert into a vector DB collection. The same collection can then be queried by template 11 — paired ingest + retrieve flows out of the box.

#### Notes
- Pre-chunked input wins on production runs because the designer controls chunk boundaries (which matter for retrieval quality). Auto-chunking is the "drop in any text and it works" default for prototyping.
- Char-window chunking is naive on purpose — token-aware or semantic chunking belongs upstream in a transform/agent. Designers wanting LangChain-style splitters pre-process and pass a list.
- The same `text_field` config that drives the query-side chunk extraction also names the metadata key under which upsert stores the chunk text. Keep them aligned across query + upsert nodes pointing at the same collection.

#### Verified
- 673/673 unit tests green (Phase-10-polish baseline 667 + 6 new tests covering pre-chunked list, raw-string auto-chunking, missing-documents validation, empty-collection rejection, unknown-operation rejection, dispatch across all 5 providers).
- Pyright 0 errors, ruff + format + frontend tsc all clean.
- Seed script: 11 templates updated, 1 created (Template 12).

### Phase 10 — Polish: My-workflows admin scope + RAG template (2026-04-28)

#### Added
- **Template 11 — RAG with Vector DB + Join Chunks.** Classic retrieval-augmented generation pipeline (Start → vector-db → transform extracts chunks → join-chunks stitches into context → agent answers). Demonstrates the vector-db + join-chunks pair and uses transform's `outputKey` to extract `vectorDbResults["results"]` into a flat `chunks` list in one node.

#### Fixed
- **`/designer` (My workflows) showed every workflow on the system for admins**, including userId=null templates. The list endpoint short-circuited the authz filter when `role == "admin"`, ignoring `mine=true`. Fixed: `mine=true` is an explicit "scope to me" request and now applies regardless of role; the admin-sees-all behaviour only kicks in when `mine` is unset (preserving the global feed for `/admin/workflows`-style use).
- **join-chunks reads `text` and `page_content` as fallbacks for `content`.** Vector-db results emit each chunk under `text`, LangChain Documents use `page_content`. Without the fallback, designers had to insert a data-transform between vector-db and join-chunks just to rename the field — but simpleeval's no-dict-literal policy made even that reshape impractical to write in a transform expression. Now vector-db results flow into join-chunks directly.

#### Verified
- 667/667 unit tests green (Phase-10-polish baseline 662 + 2 regression tests for the admin/`mine` interaction + 3 for the join-chunks `text`/`page_content` fallback).
- ruff + format + pyright + frontend tsc all clean.
- Seed script: 10 templates updated, 1 created (Template 11).

### Phase 10 — Polish: transform `outputKey` + while-loop template (2026-04-28)

#### Added
- **`outputKey` field on the transform node.** Optional named state variable to write the result to (in addition to `lastOutput`). Lets a single transform serve as both compute and persist, collapsing every "compute X and store as Y" pair from two nodes (transform → set-state) into one. Reserved names (`variables`, `lastOutput`, `node_results`, anything starting with `_`) and non-identifier strings are rejected at execute time with a clear error.
- **Transform panel rebuilt.** Old panel saved `inputVariable` + `expression`, neither of which the executor reads (it reads `transformScript`) — every transform saved through it ran with no script. New panel exposes `transformScript` + `outputKey` with inline reserved-name validation, examples for both Mustache and direct simpleeval syntax, and a live preview of how downstream nodes reference the named output.
- **Template 10 — While-loop with Accumulator.** A real iterating loop seeded as `template-10-while-loop-accumulator`: three set-state init nodes, while loop iterating `index < len(tickers)`, body picks `tickers[index]`, fetches Yahoo Finance, appends a record to `results` (one transform via `outputKey`), increments `index` (one transform via `outputKey`), loops back. Body fits in 4 nodes instead of the 8 it would have needed without `outputKey`. Reference for the loop + accumulator pattern that was previously impractical to template.

#### Notes
- Earlier session sketched three options for closing the loop ergonomics gap; we went with **(a)** — `outputKey` on transform — because it (1) puts the field where the designer's cursor already is when they need it, (2) generalises beyond loops to any pipeline where multiple transforms need named outputs without clobbering each other, (3) is consistent with how agent nodes already write to both `lastOutput` and a node-name alias, and (4) introduces zero new node types.

#### Verified
- 662/662 unit tests green (Phase-10-polish baseline 655 + 7 new transform/`outputKey` tests covering named-write, legacy-behaviour preservation, blank-string-treated-as-unset, reserved-name rejection, underscore-prefix rejection, identifier validation, end-to-end "write here, read in next transform").
- Pyright 0 errors, ruff + format clean, frontend tsc clean.
- Seed script: 9 templates updated, 1 created, idempotent re-run upserts cleanly.

### Phase 10 — Polish: templates + canvas branching + eval ergonomics (2026-04-28)

#### Added
- **Template workflows** seeded via `scripts/seed_templates.py`. Nine reference workflows (`isTemplate=true` + `isPublic=true` + `userId=null` so they appear on every user's gallery and clone into private working copies on use):
  - **01 Simple Agent** — Start → Agent → End (port of OAB #01).
  - **02 Web Research Agent** — Tavily-tooled agent (port of OAB #02; switched from inline MCP config to Composer's built-in `tavily.tavily_search`).
  - **03 Scrape and Summarise** — Firecrawl + chained agents (port of OAB #03; dropped Arcade Google-Doc step since Arcade requires per-user OAuth).
  - **04 Guardrails + Branching** — net-new; demonstrates guardrails pass-through + `{{<node>.passed}}` Mustache branching.
  - **05 Multi-Company Stock Analysis** — three Yahoo Finance HTTP fetches + comparative agent.
  - **06 Yahoo Finance Stock Report** — HTTP → Extract (JSON schema) → narrative agent.
  - **07 Amazon Product Research** — Firecrawl scrape → Extract → recommendation agent.
  - **08 Human-in-the-Loop Approval** — agent draft → user-approval gate → branched approve/reject paths.
  - **09 Zillow Property Finder** — Firecrawl + Extract + data-transform `filter` + recommendation.
  Seed script is idempotent (upsert by `externalSlug`); re-run after edits.
- **Templates gallery** at `/designer/templates` — lists every `isTemplate=true` workflow with category / difficulty / estimated-time badges. **"Use template"** button calls `instantiateTemplate(id)` which clones to the user's list (`isTemplate=false`, `isPublic=false`) and routes straight into the canvas. **"Preview the canvas (read-only)"** opens the original template untouched.
- **"Mark as template"** toggle on the workflow Settings page (independent of `isPublic`); flipping it invalidates the templates gallery cache so changes appear immediately.
- **"Templates" link** in the Designer home header next to "New workflow".
- **Branching node UI** — `if-else`, `while`, `user-approval` now render with **two labelled source handles** (`true`/`false`, `body`/`exit`, `approved`/`rejected`) coloured green/red/blue. The connection's `sourceHandle` flows through `fromReactFlow` as the `branch` field that backend validation requires. `toReactFlow` seeds `sourceHandle` from `branch` for back-compat with manually-edited JSON.

#### Fixed
- **`{{...}}` Mustache references in eval expressions.** simpleeval parses `{...}` as a set literal and rejects it. Designers reach for `{{check_guard.passed}}` because that's the syntax everywhere else in the canvas; the new `_expand_mustache_to_subscript` rewrites `{{a.b.c}}` → `a["b"]["c"]` before parse, and top-level variables are spread into the eval scope so `check_guard["passed"]` works directly. Both syntaxes accepted in if-else, while, transform, and data-transform conditions.
- **Guardrails pass-through.** Executor no longer overwrites `lastOutput` with its summary message — the upstream node's content keeps flowing. Verdict is exposed via `{{<node-alias>.passed}}` / `{{<node-alias>.violations}}` / `{{<node-alias>.message}}` for branching.
- **Guardrails panel rebuild.** Old panel saved keys (`classifierType`, `onFail`) the executor never read, so all four checks defaulted to disabled — every existing guardrails node was a no-op. New panel exposes the four `*Enabled` toggles, the `actionOnViolation` dropdown, an optional model override, and a doc block that **shows the live alias for THIS node** (e.g. `{{check_guard.passed}}` literally) with click-to-copy code chips so the placeholder-syntax copy-paste trap is closed.
- **`if-else` 422 on connect.** Root cause: the canvas had a single anonymous source handle on every node, so dragging an edge from an if-else left `sourceHandle = undefined` and `branch = null`, failing backend validation. Fixed by `BranchingNode` (above).

#### Notes
- **No true while-loop demo template** in this batch. Counter-based loops with accumulators need either a node-output-to-named-state primitive or a JS-style mutable transform; today's `set-state` (substitute-only) + `transform` (evaluate-to-`lastOutput`) split makes a 3-iteration loop ~10 nodes. To be discussed.
- **Templates default to Anthropic Haiku 4.5** — cheap and fast for demos. Designers swap models per node after cloning.
- **Templates with external dependencies** (Firecrawl, Tavily) document the required env vars in their description. Yahoo Finance uses the public `query1.finance.yahoo.com` chart endpoint — no key required.

#### Verified
- 655/655 unit tests green (Phase-10-polish baseline 648 + 7 new eval/Mustache tests).
- Pyright 0 errors, ruff + format clean, frontend tsc clean.
- End-to-end: published-workflow external invoke (`POST /api/run/my-test-workflow`) returns clean string output.
- Seed script: 9 templates created, idempotent re-run upserts cleanly.

### Phase 10 — Polish (2026-04-27)

#### Added
- **Per-model Verify** in Admin → LLM models. New `LlmModel.verificationStatus` / `verificationMessage` / `verifiedAt` columns + `POST /admin/llm-models/{id}/verify`. Probe mirrors the workflow's actual call (Anthropic `/v1/messages`, OpenAI/Groq `chat/completions`, Google `generateContent`) so the row stamps `ok` / `unavailable` correctly. `unavailable` auto-flips `enabled=false` so the model leaves the Designer dropdown.
- **Probe-on-fetch in `/llm-models/available`** — applies to every provider, not just Google. Uses the same `run_model_verify` dispatch as the per-row Verify button so the dropdown only lists models the key can actually invoke. Cached for 5 min via the existing TTL.
- **Runtime pre-flight in agent executor** — refuses fast with `ModelUnavailableError` if the configured model is stamped `unavailable`, instead of letting the request reach the provider and 404 inside a tenacity retry stack.
- **Published-endpoint indicator** on the designer canvas top bar + settings page (`PublishedEndpoint` component, `inline` and `block` variants). Shows the green badge, the `/api/run/{slug}` path, and a one-click clipboard copy of the full origin-prefixed URL.
- **Workflow import / export** (JSON + Markdown round-trip via fenced ```json``` block) on the New-Workflow dialog and the per-workflow card menu.
- **Canvas n8n-style chips** — `node-visuals.ts` maps every node type to icon + accent + label; `workflow-canvas.tsx` rebuilt around `NodeChip`; tools palette uses the same icons; brand-purple edges with magenta hover/selected.
- **Per-execution result download** as Markdown / PDF (browser print) via `ExecutionResult` component; trace collapsed by default.
- **Hierarchical sidebar** — top-level groups with sub-items per role audience.

#### Fixed
- **Agent panel ghost-selection.** When the stored model wasn't in the enabled list (e.g. admin disabled it), the native `<select>` silently showed its first option, so workflows saved a stale `model` value while the UI looked correct. Panel now injects an `(unavailable — pick a replacement)` synthetic option + red warning so designers see the real saved value.
- **Agent output content-block leak.** `AIMessage.content` returned a list of text blocks (Anthropic extended thinking / vision) which was previously stringified via `str()`, leaking `[{'type': 'text', 'text': ..., 'extras': {...}}]` into the workflow output. New `unwrap_message_content()` extracts only `text` blocks; both `_agentic_loop` and JSON-format `_format_output` use it.
- **Vector-DB panel** — was missing the **endpoint** (host URL) and **API key** fields entirely, and saved keys (`indexName`, `query`) that the executor never read. Rebuilt to match every field in `VectorDbNodeData` using the canonical `vectorDb*` aliases (endpoint, apiKey, collection, namespace, queryPrompt, topK, scoreThreshold, dimension, embeddingProvider, embeddingModel, outputVariable, textField, metadataFilter, includeMetadata, includeVector). Existing nodes need re-saving.
- **Gamma-AI panel** — added `format`, `textMode`, `numCards`, `textAmount`, `imageSource`, `language`; `exportAs` dropdown now includes the `web` option the schema accepts.
- **MCP tool calls with optional fields** — strip `None` values before passing to `tools_call` so providers like Firecrawl don't reject "expected string, received null" on optional params.
- **Pydantic `schema` field warnings** in dynamically-built MCP arg models (`protected_namespaces=()`).
- **Pre-existing pyright errors** (19) cleaned up in `admin_deployment_settings.py`, `admin_llm_keys.py`, `admin_users.py`, `auth_standalone.py` (added `passwordHash is None` check for SSO-only accounts), `executions.py`, `mcp_servers.py`, `workflows.py` (six call sites), `migration/writer.py`, `storage/checkpointer.py`.

#### Verified
- 648/648 unit tests green (Phase 10 baseline 642 + 6 new `unwrap_message_content` tests).
- Pyright 0 errors, ruff + format clean, frontend tsc clean.
- End-to-end: published-workflow external invoke (`POST /api/run/my-test-workflow`) returns clean string output.

### Phase 10 — Composer frontend + enterprise UX (2026-04-23)

#### Added
- [Phase 10 design spec](docs/archive/phase-history/specs/2026-04-22-phase-10-composer-frontend-design.md) + ADR-0023.
- **10a — Backend extensions.**
  - `Workflow.isProduction` + `Workflow.externalSlug` (globally unique); `User.passwordHash` nullable for SSO-provisioned users.
  - New `ApiKey` Prisma model (per-user, bcrypt-hashed, soft-delete via `revokedAt`); `POST/GET/DELETE /api-keys`.
  - `POST /api/run/{slug}` — external invoke with `Authorization: Bearer ck_<key>`; async default + opt-in sync (`timeoutSeconds` max 300); owner/admin/public authz.
  - `POST /auth/sso-exchange` — Azure AD JWT → Composer JWT handoff (auto-provisions User by email; JWKS cached 24h).
  - Rate limit `rate_limit_api_run_per_minute` (60/min per API key).
- **10b — Frontend scaffold.** Next.js 14 App Router + Tailwind CSS + shadcn/ui (base-nova style on `@base-ui/react`); NextAuth v5 with Azure AD + Credentials providers; OpenAPI TS client generated from Composer's schema; React Query; native WebSocket wrapper; role-aware layouts (`/designer`, `/runs`, `/admin`).
- **10c — End User UI.** List runnable workflows; workflow-details + input form driven by Start-node schema; live execution progress via WebSocket; result view; execution history; API key management with plaintext-once modal; approval dialog for `waiting_approval` state.
- **10d — Admin UI.** Backend additions: `/admin/users*` + `/admin/deployment-settings` + `DeploymentSetting` model + new admin-only `PATCH /mcp-servers/{id}/shared` endpoint. Frontend: dashboard, users list + role toggle, MCP shared toggle, built-in tool toggles, LLM keys CRUD (wraps Phase 9e), all-workflows override + reassign-owner dialog.
- **10e — Designer UI.** Workflow list + new/duplicate/delete; React Flow canvas with all 18 node types registered; unified Tools palette (shared MCPs + enabled built-ins); 18 node property panels (react-hook-form-driven); publish dialog with `slugify` pre-fill + URL preview; settings page.
- **10f — Polish + e2e.** Shared `ErrorBoundary` wrapping each role layout; `Skeleton` + `EmptyState` + error branches audited across all data pages; Tailwind config tightened (Inter via CSS variable, `--radius` token); Playwright end-to-end suite (auth / end-user / designer / admin / external-invoke — 11 tests across 5 files).
- **Integration tests.** `test_external_invoke.py` on real Neon — publish → API key → external invoke → revoke rejection.

#### Changed
- **Breaking:** `User.passwordHash` is nullable.
- **Breaking:** Phase 9's admin-via-SQL promotion supplanted by `POST /admin/users/{id}/role`.
- **Breaking:** `McpServer` now has `PATCH /mcp-servers/{id}/shared` as the admin-surgical path to flip `isShared`; the existing PUT requires a full `McpServerCreate` body.

#### Notes
- **WebSocket + Vercel:** Vercel Serverless Functions don't support long-lived WS connections; deploy the FastAPI backend as a separate container (Fly/Render/App Runner). Frontend remains on Vercel. See `docs/deployment/vercel-setup.md`.
- **shadcn style:** installed `base-nova` (built on `@base-ui/react`, not Radix); component API is largely the same but `Button asChild` is unsupported — use `Link` + `buttonVariants()` wherever a Link needs button styling.
- **Playwright suite:** ships with specs + Chromium installed; full run requires local uvicorn + next dev or a staging environment.
- **NextAuth v5 beta.31:** pinned since v5 hasn't gone stable; Azure AD provider uses the `issuer` param (not `tenantId`) in this beta.

#### Verified
- 642/642 unit tests green (baseline Phase 9: 600; +42 across API keys, publish + external invoke, SSO, admin endpoints).
- 5/5 frontend vitest tests (auth helper + workflow input form).
- 1/1 new Phase 10 integration test green against real Neon (`test_external_invoke.py`).
- Playwright: 11 tests parse across 5 specs (full run deferred to staging / CI).
- Pyright 0 errors, ruff + format + ESLint + Prettier + TypeScript all clean.

### Phase 9 — Cutover readiness (2026-04-22)

#### Added
- [Phase 9 design spec](docs/archive/phase-history/specs/2026-04-22-phase-9-cutover-readiness-design.md) + ADR-0022.
- **9b — Convex→Postgres migration.** `composer migrate --export-dir=<path>` script. New `original_owner_email` column on `Workflow`/`WorkflowExecution`/`McpServer`. `McpServer.user_id` made nullable for uniform "orphaned pending reconciliation" semantics. Email-based reconciliation CLI: `composer reconcile --email X`. Skips users/approvals/checkpoints/ephemeral tables per spec §4.2. In-flight OAB executions rewritten to status=failed with explanatory error. Json-typed fields wrapped by writer via `prisma.Json(...)`.
- **9f — Admin capabilities.** New deps `get_current_role` (returns user_id+role) and `ensure_admin`. Admin bypass on GET `/workflows/{id}`/`/executions/{id}`/list/search/events (SSE; later removed in 9a) and on PUT `/workflows/{id}`. DELETE stays strict owner-only. New admin-only ownership endpoints: `PATCH /workflows/{id}/owner`, `PATCH /mcp-servers/{id}/owner` — accept `{user_id}` or `{email}`.
- **9e — LLM keys in Postgres.** New `LlmApiKey` Prisma model (AES-256-GCM encrypted via existing `ENCRYPTION_KEY`). Admin CRUD: `GET/PUT/DELETE /admin/llm-keys[/{provider}]`. CLI: `composer keys {list|set|delete|sync}`. Vercel sync uploads decrypted values via Vercel API; `--prune` removes tracked env vars absent from Postgres. Runtime unchanged — workflow code still reads env vars at startup.
- **9a — WebSocket streaming.** `GET /executions/{id}/ws` (replaces SSE). Auth via `Sec-WebSocket-Protocol: bearer, <jwt>` subprotocol header. Event bus switched to DES-007 shapes (`workflow_started`, `node_started`, `node_completed`, `node_failed`, `workflow_completed`, `approval_required`) with camelCase JSON output + `tenantId` field (always null in standalone mode). Admin bypass on ownership. Close codes: 4401 unauth, 4403 forbidden, 4404 not found, 1000 normal.
- **9c — Deployment docs.** Five new docs under `docs/deployment/`: `vercel-setup`, `postgres-setup`, `llm-keys`, `admin-operations`, `monitoring`.
- **9d — `.env.example` finalize.** LLM-keys / LangSmith / agent-tool-key blocks marked DEV-ONLY (production reads from Postgres-synced Vercel env). New entries: `VERCEL_API_TOKEN`, `VERCEL_PROJECT_ID`, `OAB_MCP_OAUTH_ENCRYPTION_KEY`.
- **Integration tests.** `test_migration.py` (OAB→Composer cycle on real Neon); `test_events_ws_hardening.py` (two-user WS authz on real Neon).

#### Changed
- **Breaking:** SSE endpoint `GET /executions/{id}/events` DELETED. Clients use WebSocket `/executions/{id}/ws`.
- **Breaking:** event bus event-type vocabulary changed (DES-007 snake_case, split `status-change` into `workflow_started`/`workflow_completed`/`approval_required`, dropped `stream-chunk` and `approval-resumed`).
- **Breaking:** `McpServer.user_id` is now nullable (new Prisma migration). Existing rows with user_id set are unaffected.
- Removed orphaned `tests/integration/test_user_approval_events.py` (SSE-specific; doesn't translate to DES-007).

#### Notes
- User migration NOT performed (ADR-0022). OAB Clerk IDs are dropped; email is the cross-system identity.
- DES-007 event shape taken from Composer's own blueprint (§7.3); cross-check against IE source recommended before Phase 10 frontend locks its client.
- In-memory rate limiter from Phase 8 unchanged; multi-worker Redis-backed limiter still deferred.
- No SSRF protection yet (deferred).
- WS integration test uses Starlette's sync `TestClient` (httpx.AsyncClient doesn't do WebSocket; mixing fixture loops deadlocks Prisma).

#### Verified
- 600/600 unit tests green (+44 from Phase 8's 556: migration transforms 8, reconcile 2, admin 17, llm-keys CRUD 8, vercel client 4, keys CLI 5, WS unit 5, minus 6 deleted SSE tests, plus test-helper updates).
- 2/2 new integration tests green against real Neon (migration + reconciliation cycle; WebSocket two-user authz).
- Pyright 0 errors, ruff + format clean.

### Phase 8 — Security + hardening (2026-04-22)

#### Added
- [Phase 8 design spec](docs/archive/phase-history/specs/2026-04-21-phase-8-security-hardening-design.md) + ADR-0021.
- **Authz hardening:**
  - `GET /workflows/{id}` returns 404 for non-owner private workflows (info-leak tight; not 403).
  - `GET /workflows` list and `GET /workflows/search` filter to `OR(isPublic=True, userId=caller)`.
  - `GET /executions/{id}`, `POST /executions/{id}/resume`, `GET /executions/{id}/events` require `execution.userId == caller`; 404 for non-owner.
  - `GET /executions` list scopes to caller's own executions; removed the `?userId=...` query param.
- **Size caps:** `max_workflow_nodes=100`, `max_workflow_edges=200`, `max_execution_input_bytes=1_000_000`. Settings-tunable; enforced at write time. Violations → 422 (workflow) / 413 (execution input).
- **Rate limiting:** in-memory token-bucket `RateLimiter` + `TokenBucket` + `enforce()` helper in `src/security/rate_limit.py`. Per-key buckets (`user_id` authenticated / IP pre-auth). Applied to `POST /executions` (30/min/user), `POST /resume` (60/min/user), `POST /auth/login` (10/min/IP), `/auth/register` (5/min/IP), `/auth/refresh` (30/min/IP), `POST /mcp-servers/{id}/test-connection` (10/min/user). 429 with `Retry-After` header on breach.
- **Security regression tests:** parametric tests for Unicode/emoji/HTML/SQL-meta in workflow names, path-traversal IDs → 404.
- **Integration test:** real-Neon two-user scenario — public workflows cross-read, private rejection, update/delete authz, execution authz, list filters.

#### Changed
- **Breaking:** Phase 7b tests that assumed world-read on `GET /workflows/{id}` updated to set mock rows to `userId="dev"` (dev-mode caller) or `isPublic=True`.
- `GET /executions` list no longer accepts `?userId=...` — caller-scoped always.
- 404 (not 403) for private-read-by-non-owner — matches info-leak policy (ADR-0021).

#### Notes
- **In-memory rate limiter** — state resets on worker restart and is not shared across workers. Phase 9 replaces with a Redis-backed limiter for multi-worker scale-out.
- **No SSRF protection** on the `http` executor — internal-network URLs reachable. Deferred to Phase 9+.
- **No request-body ASGI-level size limit** — Phase 8 validates shape-after-parse; pathological payloads (>100 MB) could exhaust worker memory before validation. Tighten at ingress in Phase 9.

#### Verified
- 556/556 unit tests green (+27 from Phase 7b 529: Task 1/2/3/4 authz + size caps already counted above + 8 rate-limit unit tests + 8 hardening regression tests + a few updated/migrated test helpers).
- 1/1 new integration test green against real Neon (two-user authz boundaries).
- Pyright 0 errors, ruff + format clean.

### Phase 7b — Workflow CRUD (2026-04-21)

#### Added
- [Phase 7b design spec](docs/archive/phase-history/specs/2026-04-21-phase-7b-workflow-crud-design.md).
- `GET /workflows` — paginated list with filters (`isTemplate`, `isPublic`, `category`, `mine`). Envelope: `{total, items, limit, offset}`. Ordered by `updatedAt DESC`. Limit capped at 100.
- `GET /workflows/search?q=...` — name/description search via Prisma `contains` + case-insensitive. Empty `q` is 422.
- `GET /workflows/{id}` — fetch one; 404 if unknown.
- `PUT /workflows/{id}` — owner-only update. Full replacement (not PATCH). 403 if not owner; 404 if unknown; 422 if shape invalid.
- `DELETE /workflows/{id}` — owner-only hard-delete. Cascades to executions + checkpoints via existing Prisma `ON DELETE CASCADE`. 204 on success; 403/404 as above.
- `GET /executions` — paginated list with filters (`workflowId`, `userId`, `status`). Envelope same shape. Ordered by `startedAt DESC`.
- `WorkflowListResponse` + `ExecutionListResponse` Pydantic envelopes.
- Integration test: full CRUD cycle against real Neon.

#### Notes
- Phase 7 was scoped via spec into three sub-phases: **7b — Workflow CRUD (this phase)**, **7c — API auxiliaries** (deferred), **7d — Regression suite port from OAB** (deferred). Phase 7b closes the OAB API parity gap that blocks Phase 10 UI work.
- Route-ordering fix during implementation: `GET /workflows/search` registered BEFORE `GET /workflows/{id}` so FastAPI doesn't swallow `search` as a path parameter.
- No new ADR — conventional REST CRUD extensions.
- `mine=true` on `GET /workflows` uses the current user id (dev-mode fallback to `'dev'` per ADR-0015 in development).

#### Verified
- 529/529 unit tests green (+20 from Phase 6e 509: 15 workflow-CRUD + 5 executions-list).
- 1/1 integration test green against real Neon (full CRUD cycle — create → get → list → search → update → delete → 404).
- Pyright 0 errors, ruff + format clean.

### Phase 6e — Vector-DB (2026-04-21)

#### Added
- [Phase 6e design spec](docs/archive/phase-history/specs/2026-04-21-phase-6e-vector-db-design.md) + ADR-0020.
- `src/vectordb/` module — new top-level package.
  - `src/vectordb/embedding.py` — `embed_text_openai()` via the OpenAI embeddings API. `text-embedding-3-*` models accept `dimensions` param; older models omit it.
  - `src/vectordb/providers/` — one file per provider, each exporting `async def query(embedding, config) -> list[VectorDbResult]`. Shared `QueryConfig` + `VectorDbResult` frozen dataclasses in `base.py`.
  - **5 providers:** Pinecone (REST `/query`), Qdrant (REST `/collections/{name}/points/search`), Chroma (REST batch-indexed with `1 - distance` score), Weaviate (GraphQL with `_additional {id,distance,vector}` + `1 - distance` score), Milvus (REST `/v1/vector/search`; DSL-only filter — dict filters log warn + skip).
- `src/executors/vector_db.py` — orchestrator. Substitutes config fields, embeds via OpenAI, dispatches via `_PROVIDERS` dict map, applies score-threshold filter, optionally joins results with separator/prefix/suffix + `{{index}}` placeholder. Dual output: `variables[output_variable]` + `variables.lastOutput` (joined text if `joinResults=True`, else the full output dict).
- `src/engine/workflow.py` — `VectorDbNodeData` tightened with 18+ explicit fields matching OAB `types.ts` + executor reads. `VectorDbProvider` + `EmbeddingProvider` Literal aliases exported.
- Unit tests: ~30 across `tests/unit/vectordb/` + `tests/unit/executors/test_vector_db.py` (6 embedding + 4 Pydantic + 3-4 per provider × 5 + 14 executor with 5-way parametric dispatch).

#### Changed
- Non-OpenAI embedding providers (`cohere`/`jina`/`pinecone-inference`) raise `NotImplementedError` until a later phase.
- Stale "unshipped Phase 6 sentinel" tests removed — Phase 6 closes the executor catalog, all 18 node types now have registered executors: `test_unshipped_type_raises_with_phase_hint` (registry) and `test_build_graph_rejects_unshipped_executor_type` (graph_builder) deleted. `test_run_marks_failed_on_exception` (langgraph_executor) migrated to monkeypatch `build_graph` → raise, preserving the behavioral contract.

#### Notes
- **Phase 6 is now complete.** All 18 node types in OAB's catalog have Composer executors:
  - Phase 1: start, end
  - Phase 2: agent
  - Phase 3a/3b: mcp
  - Phase 4a/4b: http, transform, data-transform, extract, set-state, if-else, while
  - Phase 5a: user-approval
  - Phase 6a-e: note (skip), join-chunks, guardrails, gamma-ai, arcade, vector-db
- No automated integration tests for vector-db: each provider requires real credentials + a populated index. Unit tests via `pytest-httpx` pin the wire shapes for all 5 providers.
- Per-node API keys (`vectorDbApiKey`) use `{{...}}` substitution — users parameterize via state/env.
- `OPENAI_API_KEY` is the only global (for embeddings); per-provider keys live on the node.

#### Verified
- 509/509 unit tests green (+41 from Phase 6d 468 — net after 2 stale sentinel deletions).
- Pyright 0 errors, ruff + format clean.

### Phase 6d — Arcade (2026-04-21)

#### Added
- [Phase 6d design spec](docs/archive/phase-history/specs/2026-04-21-phase-6d-arcade-design.md) + ADR-0019.
- `src/executors/arcade.py` — `ArcadeExecutor` calls arcade.dev's tool-execution API via direct HTTP (no SDK). Two-step protocol: `POST /v1/tools/authorize` → `POST /v1/tools/execute`. If auth is pending, pauses via Phase 5a's `interrupt()` primitive; user completes OAuth externally and calls `POST /executions/{id}/resume` with `decision='approved'` (retry) or `'rejected'` (`ArcadeUserCanceledError`). Retry counter `_arcade_retries_<node_id>` caps resume attempts at `MAX_RETRIES=3` to prevent infinite loops on broken OAuth URLs.
- `src/engine/workflow.py` — `ArcadeNodeData` tightened: `tool` (required, alias `arcadeTool`), `input` (default `{}`, alias `arcadeInput`), `user_id` (default `'workflow-builder'`, alias `arcadeUserId`).
- `src/config.py` — `arcade_api_key` setting (env var `ARCADE_API_KEY`).
- SSE `approval-pending` event payload extended with optional `auth_url` / `auth_id` / `tool_name` fields (back-compat: existing user-approval consumers ignore unknown keys).
- Exception classes: `ArcadeNodeError` (config / network), `ArcadeAuthError` (auth failed or retry limit), `ArcadeUserCanceledError` (user rejected at resume).
- Unit tests: 14 via `pytest-httpx`; `interrupt()` mocked to raise `GraphInterrupt` in the pending-auth case. Covers happy path, all three output-extraction branches, auth-pending with state mutation assertion, auth-failed, resume approved/rejected paths, retry-limit exceeded, variable substitution, missing key, network error, 4xx, missing id, registry.

#### Changed
- `/executions/{id}/resume` now serves TWO interrupt sources: user-approval (Phase 5a) AND arcade-auth (Phase 6d). Same endpoint, same event type, same retry primitive. Executor owns the re-entry semantic (ADR-0019).

#### Notes
- Max runtime unbounded (depends on user completing OAuth). Retry counter caps the number of resume cycles.
- Variable substitution applies to each string value in `arcadeInput`; non-string values pass through unchanged.
- Output extraction: `result.output.value` first, then `result.output`, then the whole `result` dict — matches OAB's fallback chain.
- Retry counter persistence: executor mutates `state["variables"]["_arcade_retries_<node_id>"]` BEFORE calling `interrupt()`. LangGraph's Pregel captures pre-interrupt state in the checkpoint, so the counter survives through the pause.
- **Integration test is manual-only.** Arcade's OAuth flow can't be automated in CI. Smoke-test pattern documented in spec §7.2.

#### Verified
- 468/468 unit tests green (+18 from Phase 6c 450: 4 Pydantic tests + 14 executor tests).
- Pyright 0 errors, ruff + format clean.

### Phase 6c — Gamma-AI (2026-04-21)

#### Added
- [Phase 6c design spec](docs/archive/phase-history/specs/2026-04-21-phase-6c-gamma-ai-design.md).
- `src/executors/gamma_ai.py` — `GammaAiExecutor` calls the Gamma.app public API (`https://public-api.gamma.app/v1.0`) to generate a presentation/document/webpage. Two-step protocol: POST create → poll GET until `state=completed` or `failed`. OAB-compatible cadence: 60s initial wait, 10s interval, 4-min max. When `exportAs` in {pptx, pdf}, waits up to 60s more for the download URL. `lastOutput = downloadUrl || gammaUrl`. Module-level sleep constants (`INITIAL_WAIT_SECONDS`, `POLL_INTERVAL_SECONDS`, `MAX_POLL_SECONDS`, `EXPORT_WAIT_SECONDS`, `EXPORT_POLL_INTERVAL`) for test monkeypatching.
- `src/engine/workflow.py` — `GammaAiNodeData` tightened with 8 explicit fields matching OAB `types.ts:77-85` (`prompt`, `format`, `textMode`, `numCards`, `textAmount`, `imageSource`, `language`, `exportAs`). Enum literals pinned (`presentation`/`document`/`social`, `generate`/`condense`/`preserve`, `brief`/`medium`/`detailed`, `pptx`/`pdf`/`web`).
- `src/config.py` — `gamma_api_key` setting (env var `GAMMA_API_KEY`).
- Unit tests: 13 via `pytest-httpx`. Sleep monkeypatched so tests run fast.

#### Notes
- Phase 6 was split — the originally planned 6c (Gamma-AI + Arcade) is now **6c (Gamma-AI only)** + **6d (Arcade)** + **6e (Vector-DB)**. Arcade's auth-interrupt flow (reuse of Phase 5a's `/resume` pattern) warrants its own focused sub-phase.
- Max runtime ~6 minutes. Runs inline in the BackgroundTask (machine-bounded wait; no LangGraph interrupt needed).
- Transient 4xx/5xx during polling is retried; POST create failure or `state=failed` raises `GammaNodeError`. Polling timeout (5 min wall clock) returns last-known status without raising — OAB-compatible.
- **Integration smoke test skipped in Phase 6c.** `GAMMA_API_KEY` is present in `.env`, but a real generation takes ~5 minutes and consumes API credits; can be run manually when needed.

#### Verified
- 450/450 unit tests green (+17 from Phase 6b 433: 4 Pydantic tests + 13 executor tests).
- Pyright 0 errors, ruff + format clean.

### Phase 6b — Guardrails (2026-04-21)

#### Added
- [Phase 6b design spec](docs/archive/phase-history/specs/2026-04-21-phase-6b-guardrails-design.md) + ADR-0018.
- `src/executors/guardrails.py` — `GuardrailsExecutor` calls the Phase 2 LLM provider framework as a safety classifier. Four checks (PII, moderation, jailbreak, hallucination), any subset enabled per node. Concurrent via `asyncio.gather`. Prompts frozen in source (spec §5). Response parsing: `first_word.upper().startswith("YES")`; ambiguous non-YES treated as NO (anti-false-positive bias).
- `src/engine/workflow.py` — `GuardrailsNodeData` tightened with explicit fields matching OAB `types.ts:86-106` (`piiEnabled`, `moderationEnabled`, `jailbreakEnabled`, `hallucinationEnabled`, `actionOnViolation`, `model`).
- Output convention: `_guardrails_result = {passed, checks_run, violations, message}` (for `if-else` branching) + `lastOutput` = human-readable summary.
- `GuardrailsNodeError` wraps LLM failures; `GuardrailViolationError` fires when `action_on_violation='block'` + any violation → execution `failed`.
- Integration test against real Anthropic (Claude Haiku) + real Neon: PII detection path.

#### Changed
- `action_on_violation='block'` → `GuardrailViolationError` → execution status `failed`. `'warn'` → pass-through with violations populated.
- Sentinel tests migrate: `guardrails`/`Phase 6` → `vector-db`/`Phase 6` across three test files (`test_registry`, `test_graph_builder`, `test_langgraph_executor`). Same precedent as Phase 5a's `user-approval` → `guardrails` migration.

#### Notes
- OAB shipped a placeholder (`lib/workflow/executors/tools.ts:80` — 4-word hardcoded bad-word list with `TODO: Integrate with content moderation APIs`). Composer's executor supersedes it with real LLM-based classification while matching OAB's output shape.
- Model falls back to `DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"` when not set on the node (matches `src/executors/agent.py`'s pattern). Users can pin a cheap/fast model when guardrails run hot.
- Prompts are NOT user-configurable — guardrails is a safety feature, deterministic + auditable. Users wanting custom rules should compose `agent` + `if-else`.

#### Verified
- 433/433 unit tests green (+16 from Phase 6a 417: 4 Pydantic tests + 12 executor tests).
- 1/1 integration test green against real Anthropic + real Neon on first try (no fix-ups needed).
- Pyright 0 errors, ruff + format clean.

### Phase 6a — Note + Join-Chunks (2026-04-21)

#### Added
- [Phase 6a design spec](docs/archive/phase-history/specs/2026-04-21-phase-6a-note-join-chunks-design.md).
- `src/executors/join_chunks.py` — `JoinChunksExecutor` concatenates a list of chunks (strings, or dicts with `content`/`metadata`) with configurable separator/prefix/suffix; optionally appends `[metadata: {json}]` per chunk. Output to `lastOutput`. Errors: missing variable / non-list value → `JoinChunksNodeError`; empty list → empty string.
- `src/engine/workflow.py` — `JoinChunksNodeData` tightened with explicit fields (replaces Phase 1 `config: dict[str, Any]` placeholder). Matches OAB `types.ts:132-137` aliases (`joinChunksVariable`, `joinChunksSeparator`, `joinChunksPrefix`, `joinChunksSuffix`, `joinChunksIncludeMetadata`).
- Integration test against real Neon: `start → set-state(list) → join-chunks → end`, asserts final `lastOutput`.

#### Notes
- OAB did NOT ship an executor for `join-chunks` (the type was declared in `types.ts` but unwired in `langgraph.ts`). Composer implements the behavior per the declared field semantics.
- `note` is visual-only; already skipped at graph-build time since Phase 1. The existing regression test at `tests/unit/engine/test_graph_builder.py:166` locks this behavior (inspects the compiled graph to confirm note nodes are NOT registered as LangGraph nodes).
- Phase 6a is the "local executor" sub-phase; 6b (guardrails), 6c (gamma-ai + arcade), and 6d (vector-db) add external-API integrations next.

#### Verified
- 417/417 unit tests green (+16 from Phase 5b baseline of 401: 12 executor tests + 4 Pydantic model tests).
- 1/1 integration test green against real Neon.
- Pyright 0 errors, ruff + format clean.

### Phase 5b — SSE streaming (2026-04-21)

#### Added
- [Phase 5b design spec](docs/archive/phase-history/specs/2026-04-21-phase-5b-sse-streaming-design.md) + ADR-0017.
- `src/engine/events.py` — `ExecutionEventBus` (in-process asyncio fanout, bounded per-subscriber queue with drop-oldest overflow) + `ExecutionEvent` frozen dataclass with typed `EventType` literal.
- `src/engine/events_wrapper.py` — `wrap_executor_with_events` applied inside `graph_builder.build_graph`. Every executor emits `node-start` / `node-complete` automatically (no per-executor changes).
- `src/engine/context.py` — new ContextVars `current_execution_id`, `current_event_bus`. Set by `LangGraphExecutor._prepare_compiled`; read by the node wrapper.
- `src/engine/langgraph_executor.py` — emits `status-change(running/completed/failed/waiting_approval)` on every transition, `approval-pending` on pause; closes the event stream on every terminal path. `__init__` gains optional `event_bus` parameter (default None for back-compat).
- `src/api/events.py` — `GET /executions/{id}/events` Server-Sent Events endpoint. Snapshot-on-subscribe (first frame is `status-change` with current DB status), 15s keepalive via SSE comment, auto-unsubscribe on client disconnect, bounded per-subscriber queue.
- `src/api/executions.py` — `POST /resume` now emits `approval-resumed` with `{node_id, decision}` synchronously (before the BackgroundTask).
- `src/storage/db.py` — `event_bus` attached to `app.state` in `prisma_lifespan` alongside the checkpointer; new `get_event_bus(request)` dependency.
- Integration test: full approved-path SSE stream against real Neon; asserts `approval-resumed`, `status-change(completed)`, and post-resume `node-start`/`node-complete` events.

#### Changed
- `LangGraphExecutor.__init__` accepts optional `event_bus` parameter (defaults to None for back-compat with existing tests).
- All executors participate in event emission via the graph-build-time wrapper — no per-executor edits needed.

#### Fixed
- Integration-test race caught by real Neon: `approval-pending` emits before the SSE subscriber's HTTP connection can establish. Snapshot-on-subscribe (spec §9) does not replay past events, so asserting on `approval-pending` in the integration test was racy. Revised assertions rely only on events guaranteed after the subscriber connects: `approval-resumed`, `status-change(completed)`, and post-resume node events. Commit `a6a4a98`.

#### Verified
- 401/401 unit tests green (+19 from Phase 5a baseline of 382).
- 3/3 integration tests green against real Neon: SSE approved path + approved + rejected (Phase 5a regression).
- Pyright 0 errors, ruff + format clean.

#### Deliberate design choices (see ADR-0017)
- In-process asyncio bus, not Postgres `LISTEN/NOTIFY` or Redis. Zero new infra; multi-worker fanout is a Phase 9 concern.
- SSE over WebSocket — stateless, plays with standard HTTP middleware, no new dependencies.
- Snapshot-on-subscribe: first frame is always `status-change` with current DB status. No event replay; DB remains authoritative for history via `GET /executions/{id}`.
- Bounded per-subscriber queue (128) with drop-oldest overflow: slow subscribers never stall the emitter. Stream is advisory; authoritative state lives in Postgres.
- Five event types only. LLM token streaming is a Phase 10 UI concern.
- Any authenticated user can subscribe in 5b (same as `/resume`). RBAC is Phase 7b+.

### Phase 5a — User-approval + interrupt/resume (2026-04-21)

#### Added
- [Phase 5a design spec](docs/archive/phase-history/specs/2026-04-21-phase-5a-user-approval-design.md) + ADR-0016.
- Prisma `Approval` table + `ApprovalDecision` enum (per-decision audit row; cascade-deletes with execution; indexed by `executionId` and `approverUserId`).
- `src/executors/user_approval.py` — `UserApprovalExecutor` calls LangGraph's `interrupt({node_id, prompt})` (reads `data.approval_message` alias `approvalMessage` for the prompt template); records `_approval_<node_id>` in variables on resume; `UserApprovalNodeError` for invalid decisions.
- `src/engine/graph_builder.py` — extends Phase 4b's conditional-edges pass with `user-approval` routing (branches `{approved, rejected}`); `_route_user_approval` reads `_approval_<node_id>` from variables.
- `src/engine/langgraph_executor.py` — detects pause via `compiled.aget_state(config).next` after `ainvoke` (see Fixed below); merges `_pending_approval_node` / `_pending_approval_prompt` into `variables`; new `resume(execution_id, decision)` method calls `compiled.ainvoke(Command(resume=decision))`; chained pauses re-mark `waiting_approval`.
- `POST /executions/{id}/resume` — accepts `{decision: 'approved'|'rejected', note?: str}`; 404 / 409 / 422 / 500 error paths; writes Approval row; flips status to `running` before scheduling the `BackgroundTasks` resume.
- Integration tests: approved path + rejected path (real Neon); assert `Approval` row contents + final variable state.

#### Changed
- Status transitions: `running → waiting_approval → running → completed|failed`. No new terminal status — the rejected branch completes normally.
- Sentinel tests migrate: `user-approval`/`Phase 5` → `guardrails`/`Phase 6` across three test files (graph_builder, langgraph_executor, executors/test_registry).
- `LangGraphExecutor` refactored: `_prepare_compiled` / `_mark_completed` / `_mark_waiting_approval` / `_mark_failed` / `_load_execution` helpers factor the shared prep + DB-update logic between `run()` and `resume()`.

#### Fixed
- One bug caught by real-Neon integration testing: LangGraph's `interrupt()` in current versions does NOT propagate `GraphInterrupt` out of `ainvoke` — the Pregel runtime catches it internally, persists the checkpoint, and returns cleanly. Our original `except GraphInterrupt` was dead code; every execution would have completed immediately, skipping the pause. Fix: inspect `compiled.aget_state(config).next` after `ainvoke`; extract pending interrupt payload from `snapshot.tasks[*].interrupts[*].value`. Same check applied to `resume()` for chained pauses. Commit `b07d8de`.

#### Verified
- 382/382 unit tests green (+18 from Phase 7a baseline of 364).
- 2/2 Phase 5a integration tests green against real Neon (approved + rejected paths).
- Prior regression tests unaffected (sentinel tests migrated cleanly; Phase 4b conditional-edges untouched).

#### Deliberate design choices (see ADR-0016)
- Checkpoint + resume (LangGraph `interrupt()` + `aget_state` pause detection), not blocking request or re-run-from-scratch. Pins no API workers; preserves side-effect correctness.
- `Approval` as a dedicated Prisma table, not inline JSON on `WorkflowExecution.variables`. Enables audit trail + future multi-approver workflows.
- Status flip to `running` happens BEFORE the `BackgroundTask`, so polling clients never observe stale `waiting_approval` while the resume is in flight.
- Rejected branch is ordinary graph routing — no special `"rejected"` top-level status. The decision lives on the `Approval` table.
- Any authenticated user may resume in 5a (dev-mode fallback returns `'dev'` per ADR-0015). RBAC (only the workflow creator / tagged approvers can resume) is Phase 7b+.

### Phase 7a — Deployment-mode toggle + auth middleware (2026-04-21)

Brought forward from Phase 7 because user-approval (Phase 5) needs authenticated user context. Phase 5 (user-approval + SSE) is re-sequenced to run next.

#### Added
- [Phase 7a design spec](docs/archive/phase-history/specs/2026-04-21-phase-7a-deployment-mode-design.md) + ADR-0014 + ADR-0015.
- Prisma `User` table + `UserRole` enum. Populated only in standalone deployments; embedded deployments leave it empty (userId strings come from IEP-signed JWT `sub` claims).
- `src/config.py` — `deployment_mode`, `iep_jwt_issuer`, `iep_jwks_url`, `iep_shared_secret`, `iep_ui_origin`, `bcrypt_rounds`.
- `src/security/auth.py` — `AuthError` + `get_current_user_id` FastAPI dependency with mode-aware dispatch (HS256 w/ Composer's `JWT_SECRET` in standalone vs HS256 w/ `IEP_SHARED_SECRET` in embedded) + dev-mode fallback (ADR-0015).
- `src/security/passwords.py` — bcrypt hash + verify helpers (cost factor from `bcrypt_rounds`).
- `src/api/auth_standalone.py` — `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/disconnect` (registered only in standalone).
- `src/api/auth_common.py` — `/auth/me` (registered in both modes; standalone returns User row, embedded returns JWT claims).
- Deps: `bcrypt>=4.0.0`, `email-validator>=2.0.0` (for Pydantic `EmailStr`).
- 7 new integration tests: standalone register→login→me→refresh lifecycle, duplicate-email 409, missing-auth probe; embedded correct-issuer 200, wrong-issuer 401, missing-header 401, bad-signature 401 — all pass against real Neon.

#### Changed
- `src/main.py` — mode-aware router registration + CORS allowlist + startup validation. Embedded mode with missing `IEP_JWT_ISSUER` or both of `IEP_SHARED_SECRET`/`IEP_JWKS_URL` refuses to boot with a clear RuntimeError.
- `src/engine/state.py` — `WorkflowStateDict` gains `user_id: NotRequired[str]`.
- `src/engine/langgraph_executor.py` — propagates `execution.userId` into initial state before `compiled.ainvoke`.
- Every route handler and executor that hardcoded `user_id="dev"` now declares `Depends(get_current_user_id)` or reads `state["user_id"]`. Only remaining `"dev"` literal in `src/` is the single ADR-0015 fallback inside `src/security/auth.py`.
- Phase 5 (user-approval + SSE) is re-sequenced to run after 7a.

#### Fixed
- One integration-test bug caught by running against real Neon: `@composer.test` emails rejected by `email-validator` (reserved TLD per IANA). Changed to `@example.com` (RFC 2606 reserved-for-examples).

#### Verified
- 364/364 unit tests green (+48 from Phase 4b baseline of 316).
- 7/7 Phase 7a integration tests green against real Neon.
- 6/6 regression check: prior Phase 1/4a/4b integration tests (start→end, if-else, while, HTTP→Extract→Set-State) continue to pass via ADR-0015 dev-mode fallback.

#### Deliberate design choices (see ADR-0014 + ADR-0015)
- Deployment mode read from env var at startup; no live-switch. Auth semantics differ too fundamentally between modes for live-flipping to be safe.
- Dev-mode fallback (`user_id="dev"` when no Authorization + `environment=development`) keeps existing integration tests working without JWT refactor. Production deploys (`ENVIRONMENT=production`) get no fallback; startup logs prominent WARN when fallback is active.
- 7a ships HS256 shared-secret path for IEP JWT verification; JWKS/RS256 is Phase 7b.
- Onboarding UI (standalone-mode register/invite flows) activates when `deployment_mode=standalone` — documented for Phase 10.
- Login returns uniform 401 on unknown email OR wrong password (no enumeration side channel).

### Phase 4b — Control-flow executors (if-else, while) (2026-04-21)

**Phase 4 is now complete overall.** Combined with Phase 4a, Composer can express any OAB workflow topology except user-approval (Phase 5).

#### Added
- [Phase 4b design spec](docs/archive/phase-history/specs/2026-04-21-phase-4b-control-flow-design.md) + ADR-0013.
- `WorkflowEdge.branch: str | None` field for conditional-edge labels (ADR-0013).
- `src/executors/if_else.py` — `IfElseExecutor` records the evaluated branch decision; actual routing done by the builder's closure.
- `src/executors/while_loop.py` — iteration-bounded loop with `max_iterations` cap (default 100); raises `WhileMaxIterationsError` on overflow. Per-loop iteration counter lives in `state.variables._while_iterations`.
- `graph_builder._branch_mapping` helper: validates edge-branch sets against required branches (exact match — no missing, no extras, no duplicates, no unknown names).
- `graph_builder._route_if_else` / `_route_while`: router closures that re-evaluate the condition fresh on each traversal.
- `graph_builder.build_graph` emits `add_conditional_edges` for if-else / while.
- Integration tests: two if-else routing workflows (true + false branch) + one while countdown — all run against real Neon without an LLM.
- Regression port: 4 OAB if-else behavioural-contract tests.

#### Changed
- `src/engine/workflow.py`: `WorkflowEdge` gains `branch: str | None = None`; `WhileNodeData` tightened (`condition: str | None = None`, `max_iterations: int = Field(default=100, alias="maxIterations")`).
- `src/engine/graph_builder.py`: `validate_workflow_shape` validates branch labels against source type; `build_graph` emits conditional edges after normal edges.
- `src/executors/_eval.py`: expanded scope with safe coercion functions (`int`, `float`, `str`, `bool`, `len`) so workflow authors can convert between types when template substitution stringifies values.
- Obsolete Phase-4 sentinel test `test_build_graph_rejects_conditional_edge_source` removed — if-else now ships; the `user-approval` sentinel test covers the unshipped-executor path.

#### Fixed
- Two bugs caught by running integration suite against real Neon (same verify-before-exit pattern as Phases 1, 2, 3a, 3b, 4a):
    - `_eval.py` was not catching `KeyError` / `IndexError` / `TypeError` / `ValueError` from simpleeval's subscript evaluator; they bubbled up past the `except` tuple. Now wrapped as `EvalError` uniformly.
    - `{{lastOutput}}` substitution stringifies int `lastOutput` into `"2"` when used in `set-state.stateValue`. Workflows that chain `set-state → while(variables['n'] > 0)` saw `TypeError: '>' not supported between instances of 'str' and 'int'`. Fixed by adding `int`/`float`/`str`/`bool` coercion functions to simpleeval's scope — workflow authors can now write `int(variables['n']) > 0` defensively. The substitution semantics themselves are unchanged (still returns str for single-template strings per Phase 2).

#### Deliberate design choices (see ADR-0013 + spec)
- Routing by edge label, not runtime inference from node-data fields. Single source of truth.
- Router closures evaluate condition fresh on each traversal (slight redundancy vs. reading from node_results, but deterministic; OAB does the same).
- No `break` / `continue` keywords in while body — chain an inner `if-else` routing to the exit target for early termination.

### Phase 4a — Linear Executors (http, set-state, transform, data-transform, extract) (2026-04-21)

#### Added
- [Phase 4a design spec](docs/archive/phase-history/specs/2026-04-21-phase-4a-linear-executors-design.md) + ADR-0012.
- `src/variable_substitution.py substitute_in_value` — recursive `{{...}}` over dict/list/str (keys untouched, non-str passthrough).
- `src/executors/_eval.py` — `simpleeval` wrapper, the single eval primitive (ADR-0012). Scope: `variables`, `lastOutput`, `node_results`, per-call `extra_names`. No builtins, no imports, no dunder access.
- `src/executors/http.py` — HTTP request node (JSON/text response, dot-path extraction, template substitution on URL/headers/body, 60s timeout, non-2xx → `HttpNodeError`).
- `src/executors/set_state.py` — writes templated values into `state.variables[stateKey]` and aliases as `lastOutput`.
- `src/executors/transform.py` — single `simpleeval` expression over state.
- `src/executors/data_transform.py` — map / filter / reduce with per-item `simpleeval` evaluation; reduce additionally exposes `acc` in scope.
- `src/executors/extract.py` — LLM structured-output extraction reusing Phase 2's `structured_invoke` + Phase 3b's LangSmith threading.
- Integration test: `Start → HTTP → Extract → Set-State → End` against real Neon + real Anthropic + public jsonplaceholder endpoint.
- Regression test: 3 OAB HTTP-node behavioural contract tests (JSON parsing, non-2xx failure, URL templating).

#### Changed
- `src/engine/workflow.py`: `HttpNodeData` gains `responsePath`; `DataTransformNodeData` tightened from stub `config: dict` to proper fields (`operation`, `collection`, `expression`, `itemVar`, `initial`); `ExtractNodeData` gains `input_text` + `model`.
- `src/engine/graph_builder.py`: five new side-effect imports for the new executors.
- Sentinel tests move from `http`/`Phase 4` → `user-approval`/`Phase 5` across three files (shipped as part of the http executor commit).

#### Fixed
- Integration-suite bug caught by running against real Anthropic: LangChain's `convert_to_openai_function` (called by Anthropic's `with_structured_output`) requires a top-level `title` key in the JSON schema. Added `"title": "Todo"` to the Phase 4a integration test's extract schema. Not an executor bug — schema authoring pattern — but worth documenting so workflow authors know to include `title` on extract schemas.

#### Deliberate deviations from OAB
- `transform` / `data-transform` use `simpleeval` Python expression syntax, not OAB's JS `vm.runInNewContext`. No arrow functions, no template literals, no `?.`. `EvalError` names the failing expression + cause for mechanical translation.
- `http` does not follow OAB's retry/backoff config; Phase 9 (hardening) can introduce it.
- `extract` runs on Phase 2's `structured_invoke`; the LangSmith config threading (Phase 3b fix #6) applies automatically.

### Phase 3b — MCP OAuth + the six hard-won fixes (2026-04-21)

#### Added
- [Phase 3b design spec](docs/archive/phase-history/specs/2026-04-20-phase-3b-mcp-oauth-design.md) + ADR-0011.
- Prisma `McpOAuthToken` + `McpOAuthState` tables (additive migration; `McpServer` unchanged).
- `src/mcp/oauth.py` — PKCE + state + resource derivation, `build_authorize_url`, `exchange_code_for_tokens`, `refresh_token`, `get_valid_access_token` (with service-account fallback for shared servers).
- `src/mcp/client.py` gains an optional async `auth_header_factory` hook — OAuth tokens refresh-on-use.
- `src/mcp/base.py` `McpToolProvider` OAuth branch: constructs a factory that calls `get_valid_access_token` per outbound request. Tokens never transit the client.
- REST endpoints: `POST /mcp-servers/{id}/oauth/authorize`, `GET /oauth/callback`, `POST /mcp-servers/{id}/oauth/disconnect`.
- `src/engine/context.py` gains `LangSmithConfig` + ContextVar; `LangGraphExecutor.run` sets it from Settings before `compiled.ainvoke`. `build_chat_model` wraps the returned model via `with_config` when tracing is enabled.
- Integration test against real Highspot MCP (seeded access token path — verifies server-side token retrieval + real MCP protocol end-to-end).
- Regression port: OAB's Highspot OAuth lifecycle spec — two tests asserting `resource` param on token exchange + refresh (the OAB multi-day debugging invariant).

#### Changed
- `McpToolProvider.__init__` now accepts `db: Any | None = None` + `user_id: str | None = None` as kwargs. Static-auth callers may omit both.
- `MCPClient.__init__` accepts `auth_header_factory` alongside the existing `auth_header` dict. Factory wins when both provided.
- `src/llm/providers.py build_chat_model` accepts `langsmith_config: LangSmithConfig | None`. When provided (and `tracing_v2` is True), wraps the model via `with_config`. Back-compat: `None` returns the raw model.
- `src/tools/base.py BuildContext` gains `langsmith_config: LangSmithConfig | None = None` field.
- `src/mcp/resolver.py` propagates `db + user_id` into `McpToolProvider` so the OAuth path's async factory can look up tokens.
- Phase 3a's sentinel test `test_provider_oauth_auth_not_implemented_in_3a` removed (OAuth has shipped).

#### Fixed
- OAB's six hard-won MCP fixes are now all in place in Composer: RFC 8707 `resource` on all four OAuth flows (3b, asserted on 4 separate unit tests + 2 OAB regressions), manual tool calling (3a), `inputSchema` camelCase (3a), server-side token retrieval (3b — tokens never transit the client), `isShared` + service-account token fallback (3a permissions + 3b OAuth token fallback), LangSmith config threaded (3b).
- Three Highspot-integration-test bugs caught by running against real Highspot + real Neon (same verify-before-exit pattern as Phases 1, 2, 3a): Prisma `Json?` wrapping on `.update()` (beyond the Phase 3a fix on `.create()`); `str.rstrip('/mcp')` strips char-set not suffix (replaced with `str.removesuffix`); `get_valid_access_token` treats `expiresAt=None` as never-expires (the integration test was simplified to seed a direct access token per user direction — real refresh coverage lives in unit + regression).

#### Verified
- All unit tests green: `ruff check`, `ruff format --check`, `pyright src tests` (strict), `pytest -m "not integration"` → 251 passed, 0 failed.
- OAB Highspot OAuth regression: 2/2 passing (both `resource` assertions green).
- Real-Highspot MCP stack: direct probe confirmed Composer's OAuth + MCP pipeline reaches Highspot's authentication boundary cleanly (JSON-RPC 401 "Invalid or expired token" — the token provided during verification had expired; stack itself is functionally correct).

### Phase 3a — MCP Infrastructure (Static Auth) (2026-04-20)

#### Added
- [Phase 3a design spec](docs/archive/phase-history/specs/2026-04-20-phase-3a-mcp-infrastructure-design.md) + ADR-0010.
- Prisma `McpServer` table (full OAB-faithful schema — `oauthConfig` and `isShared` columns in place for Phase 3b).
- `src/security/encryption.py` — AES-256-GCM helpers (shared primitive for 3a static tokens + 3b OAuth tokens).
- `src/mcp/client.py` — HTTP JSON-RPC + SSE MCP client (per-instance rpc-id counter).
- `src/mcp/schema_adapter.py` — `inputSchema` three-way fallback + URL template substitution.
- `src/mcp/base.py` — `McpToolProvider(ToolProvider)` — runtime-constructed per server row (ADR-0010).
- `src/mcp/resolver.py` — per-node resolution + permission checks (owner + isShared).
- `src/engine/context.py` — per-execution db ContextVar, set by `LangGraphExecutor.run` before `compiled.ainvoke`.
- `src/executors/mcp.py` — the `mcp` node type executor (standalone, no LLM in loop).
- `src/api/mcp_servers.py` — POST / GET / POST test-connection / DELETE endpoints.
- Integration tests: Agent+DeepWiki, Agent+Firecrawl MCP, `mcp` node standalone.
- OAB regression: `mcp-lifecycle.spec.ts` port.

#### Changed
- `src/tools/registry.py` → `resolve_tools_for_node` now delegates `mcp_server_ids` to `src.mcp.resolver`. The Phase-2 NotImplementedError stub is gone.
- `src/tools/base.py` `BuildContext` gains `db: Any | None = None`. Callers with MCP tools must populate it.
- `src/engine/langgraph_executor.py` sets `src.engine.context.set_current_db` before `compiled.ainvoke` so the Agent executor can dispatch MCP resolution.
- Phase-1 "unshipped executor" sentinel moves from `mcp` (now shipped) to `http` (Phase 4).
- **Deliberate deviation from OAB**: encryption format is `base64(nonce || ciphertext || tag)`, simpler than OAB's `salt:iv:authTag:ciphertext`. We don't derive the key (we use the raw 32-byte key from `ENCRYPTION_KEY` directly), so no salt. Documented in `src/security/encryption.py`.

#### Fixed
- Three bugs caught by running the integration suite against real Neon + real DeepWiki + real Firecrawl MCPs (same verify-before-exit pattern as Phases 1 + 2):
  - `src/api/mcp_servers.py` was passing raw `dict | None` for `headers` and a raw `list` for `tools` into Prisma `Json?` columns. Prisma rejected with `MissingRequiredValueError: value is required but not set`. Fixed by wrapping non-None values with `prisma.Json(...)` and omitting the key entirely when None (same `Json` wrapping lesson as Phase 1's fix in [src/api/workflows.py](src/api/workflows.py)).
  - DeepWiki + Firecrawl have **deprecated the `/sse` transport** in favor of streamable HTTP at `/mcp`. DeepWiki's `/sse` now returns HTTP 410 "SSE transport is deprecated"; Firecrawl returns 404. The 4 integration/regression tests were updated to use `/mcp` URLs.
  - `AgentExecutor` + `McpExecutor` hardcoded `user_id=None` in their `BuildContext` / `resolve_single_mcp_tool` calls, but the API creates `McpServer` rows with `userId='dev'` (anonymous per ADR-0005). The resolver's permission check then denied access. Both executors now pass `user_id='dev'` to match; Phase 7 will wire real `user_id` from auth middleware.

### Phase 2 — Agent Executor + Tool Provider Framework (2026-04-20)

#### Added
- [Phase 2 design spec](docs/archive/phase-history/specs/2026-04-20-phase-2-agent-executor-design.md) + ADR-0006, ADR-0007, ADR-0008, ADR-0009.
- `src/variable_substitution.py` — OAB-parity `{{...}}` template engine with prototype-pollution guard.
- `src/llm/providers.py` — `build_chat_model` dispatch for Anthropic/OpenAI/Google/Groq via LangChain.
- `src/llm/structured_output.py` — shared `structured_invoke` primitive (Agent now, Extract in Phase 4).
- `src/tools/base.py` — Tool Provider Framework: `ToolProvider` ABC, `AuthRequirement` (NoAuth/ApiKeyAuth/OAuthAuth), `ToolDefinition`, `BuildContext`, `HealthStatus`.
- `src/tools/registry.py` — `@register_tool_provider` decorator, `get_provider` / `list_providers` / `resolve_tools_for_node`.
- Four standard tool providers: Tavily (search), Serper (Google search), Firecrawl (scrape), Browserless (headless-browser fetch).
- `src/mcp/__init__.py` — skeleton package for Phase 3's McpToolProvider.
- `src/executors/agent.py` — Agent executor with full agentic loop (MAX_ITERATIONS=10), tool binding via `chat_model.bind_tools()`, structured output routing.
- CI updated with provider secrets (Anthropic/OpenAI/Google/Groq/Tavily/Serper/Firecrawl/Browserless/LangChain) pulled from GitHub Actions secrets.
- Per-provider integration tests (real LLMs) + Agent+Tavily integration test + OAB simple-agent regression port.
- `pytest-httpx` added as a dev dep for HTTP-tool unit tests.
- `.env.example` expanded with LLM provider + tool key placeholders and Phase-owner annotations.

#### Changed
- Phase 2 pulls Phase 3's agentic-loop architecture forward (per ADR-0006) so Phase 3 MCP becomes tool-registration, not executor-rewrite.
- Phase 2 pulls Phase 6's first standard-tool provider (Tavily) and three more (Serper/Firecrawl/Browserless) forward; Phase 6 remaining providers (Gamma, Arcade) follow the same template.
- Phase 2 pulls Phase 4's structured-output primitive forward (per ADR-0008) so Extract reuses it.
- `src/executors/agent.py` `DEFAULT_MODEL`: `claude-haiku-4-5-20251001` (was stale `claude-3-5-haiku-latest` during Phase 2 development; caught by real-API integration testing).
- **Deliberate deviation from OAB**: JSON output uses `with_structured_output(method="json_mode")` with provider-native schema enforcement (or `json.loads` fallback for schema-less asks), not OAB's naive `JSON.parse`. Rationale: Phase 2 spec §6; ADR-0008.

#### Fixed
- 3 pre-existing tests used `"agent"` / `"Phase 2"` as the unshipped-executor sentinel; updated to `"mcp"` / `"Phase 3"` (MCP is now the next unshipped executor).
- Stale LLM model names (caught by running the integration suite against real keys): `claude-3-5-haiku-latest` 404s, replaced with `claude-haiku-4-5-20251001`; `gemini-2.0-flash` blocked for new users, replaced with `gemini-2.5-flash`.

### Phase 1 — Execution engine core (2026-04-20)

#### Added
- [Phase 1 design spec](docs/archive/phase-history/specs/2026-04-20-phase-1-execution-engine-design.md) — architecture for the execution engine, Prisma schema additions, Pydantic models for all 18 node types, checkpointer design, API surface, test plan, phase-exit checklist.
- [Decisions log](docs/decisions.md) established with ADR-0001 through ADR-0005:
  - ADR-0001: LangGraph checkpointer — Prisma owns schema, thin custom saver.
  - ADR-0002: Workflow JSON schema — full OAB fidelity (all 18 node types modeled in Phase 1).
  - ADR-0003: Documentation runs in lockstep with development (four-document system + enforcement).
  - ADR-0004: Local dev Postgres — Neon only (supersedes Phase 0's dual-path guidance).
  - ADR-0005: Phase 1 API surface (3 endpoints) + JWT primitives as library code only.
- Prisma schema: four tables — `Workflow`, `WorkflowExecution`, `LangGraphCheckpoint`, `LangGraphCheckpointWrite`.
- Pydantic envelope for all 18 OAB node types (`src/engine/workflow.py`), discriminated on `type`.
- `WorkflowStateDict` TypedDict with reducers (merge_dict, last_wins, operator.add) mirroring OAB's Annotation.Root.
- Executor protocol + registry (`src/executors/base.py`) with phase-aware NotImplementedError for unshipped types.
- Start + End executors (Phase 1 subset of OAB's 18 executor types).
- Graph builder (`src/engine/graph_builder.py`): shape validation, reachability BFS, LangGraph StateGraph compilation.
- `PrismaCheckpointSaver` (`src/storage/checkpointer.py`) implementing BaseCheckpointSaver's four async methods.
- `LangGraphExecutor` orchestrator (`src/engine/langgraph_executor.py`): creates WorkflowExecution rows, drives graphs, persists terminal state.
- REST API: `POST /workflows`, `POST /executions`, `GET /executions/{id}`.
- JWT primitives (`src/security/jwt.py`): access + refresh token helpers, not wired to routes (ADR-0005).
- Prisma client lifecycle wiring via FastAPI `lifespan`; `get_db` + `get_checkpointer` dependencies.
- CI updated with Postgres 15 service, Prisma migrate step, and split pytest (unit vs integration marker).
- Integration test (`tests/integration/test_start_to_end.py`) exercising the full engine path end-to-end.
- Regression test harness (`tests/regression/`) with one OAB-ported start→end smoke test.

#### Changed
- README points at `docs/design/` and `docs/archive/phase-history/specs/` for design/spec/ADR docs.
- README stack table: corrected the real-time row to SSE (Phase 5) → WebSocket (Phase 9), matching the phased plan.
- `.env.example`: Neon-only `DATABASE_URL` format; added `TEST_DATABASE_URL` for integration tests.
- **Deliberate deviation from OAB behavior:** For a workflow with only `start → end` and no intermediate node, Composer's `finalOutput` equals the parsed input. OAB's `finalOutput` for the same shape defaults to `""` (because OAB's Start doesn't write `variables.lastOutput`). Rationale in [Phase 1 spec §7.1](docs/archive/phase-history/specs/2026-04-20-phase-1-execution-engine-design.md#71-srcexecutorsstartpy). The ported regression test (`tests/regression/test_oab_start_end.py`) asserts the Composer behavior; OAB-equivalent assertions would need adaptation when more OAB tests are ported in Phase 7.

#### Fixed
- `POST /workflows` and `POST /executions` were passing raw Python dicts/lists into Prisma `Json` columns. Prisma rejected them at runtime with `DataError: nodes should be of type Json`. All Json-column writes now wrap values with `prisma.Json(...)`. Caught by running integration tests against the real Neon Postgres dev branch rather than mocks.
- `PrismaCheckpointSaver` was passing raw `bytes` into Prisma `Bytes` columns (`checkpoint`, `metadata`, `value`). The rust query engine's JSON serializer rejected them with `TypeError: Type <class 'bytes'> not serializable`. All writes now wrap with `prisma.Base64.encode(...)`; the read path already handled both `Base64` objects and raw bytes.
- `tests/conftest.py` held a session-autouse fixture that required `TEST_DATABASE_URL`, which started skipping unit tests too when the fixture moved to the top-level conftest. Replaced with a `pytest_collection_modifyitems` hook that skips only tests marked `@pytest.mark.integration`.
- Duplicated `_get_db` FastAPI dependency in both `src/api/workflows.py` and `src/api/executions.py` consolidated to a single `get_db` in `src/storage/db.py` (made `Request` a real runtime import so FastAPI's parameter detection works).
- Integration/regression poll helpers now use `asyncio.get_running_loop()` instead of the deprecated `get_event_loop()` (silences DeprecationWarning under Python 3.12).

#### Removed
- `docker-compose.yml` (per ADR-0004). Local dev is Neon-only; integration tests in CI use a GitHub Actions Postgres service container.
- README "Offline fallback: local Postgres" section.

### Phase 0 — Scaffolding (2026-04-20)

#### Added
- Initial repo scaffold: FastAPI skeleton, Prisma schema placeholder.
- `pyproject.toml` with full dependency list (FastAPI, LangGraph, LangChain providers, Prisma, pytest, ruff, pyright).
- `/health` endpoint returns service metadata.
- CI workflow (lint + typecheck + tests).
- `.env.example` template.
- MIT License.
- README with setup instructions.

> Historical note: Phase 0 originally shipped a `docker-compose.yml` for local Postgres fallback. ADR-0004 (Phase 1) supersedes that decision; Neon is the sole dev path going forward.
