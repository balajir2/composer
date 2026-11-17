# Composer System Audit — Application-Level Fix List (2026-11-17)

**Audit date:** 2026-11-17
**Audited commit:** `ac584ab` (branch `main`)
**Scope:** backend application logic (auth, authorization, execution engine, durable-execution path, node executors, API surface), deploy configuration, and product-level missing functionality.
**Relationship to the earlier audit:** `docs/claude-system-audit-fix-list.md` (2026-11-02) covered infrastructure and supply chain (internal-route auth, dependencies, CI, containers, headers). Those items were remediated and are not repeated here. This list covers what that audit did not look at: how the application behaves.

**How this audit was done:** by reading the source. No tests were run and nothing was executed against a live database or GCP. Every item below cites the code it was read from. Items marked **Needs confirmation** depend on external platform behavior or runtime behavior that reading alone cannot settle; confirm those before changing code.

---

## How to use this list

1. Re-verify the cited evidence against the current branch before editing. Line numbers are from `ac584ab` and will drift.
2. Work on one numbered item at a time, one conceptual change per commit.
3. Every bug fix ships with a regression test that reproduces the real failure mode first (fails before the fix, passes after). Several of these bugs are invisible to mocked-DB unit tests; where an item says so, write the test as a Postgres-backed integration test.
4. Run the full gates before each commit: `ruff check`, `ruff format --check`, `pyright`, `pytest` for the backend; Prettier, `tsc`, Vitest and `next build` for the frontend.
5. Update `CHANGELOG.md` and `docs/decisions.md` in the same commit as any behavior change.
6. Items in Part B and items marked **Decision needed** change product behavior. Do not pick a design silently: stop and ask the repository owner.
7. Project rules in `CLAUDE.md` apply: the stack is locked, the OAB repository is read-only, and no cloud infrastructure is provisioned or deployed without an explicit go-ahead.

Priorities: **P0** is exploitable or corrupts data on the live deployment today. **P1** is a real bug or gap with a narrower trigger. **P2** is hardening and maintainability.

---

# Part A — Defects

## P0-1. Open self-registration on a deployment with shared LLM keys

**Evidence**

- `src/api/auth_standalone.py:118-154`: `POST /auth/register` creates an active account for any email and returns a token pair immediately. There is no email verification, invite, allow-list, admin approval, or setting to turn registration off. The only control is a per-IP rate limit.
- `src/main.py:170-171` mounts this router whenever `deployment_mode == "standalone"`, which is the production mode (`scripts/gcp-bootstrap.ps1:295`).
- LLM, Resend, Gamma, Tavily and similar keys are deployment-wide, and every workflow uses them.

**Impact:** anyone on the internet can register on the live instance and then:

- run agent workflows that spend the deployment's LLM and tool credits, bounded only by 30 executions per minute per user;
- send email to arbitrary recipients through the platform's Resend account using the email node (`src/executors/email.py:55-65`);
- list every active user's email address through `GET /users/search` (`src/api/users.py`);
- run any workflow marked public, including through `POST /api/run/{slug}` (`src/api/run.py:137`).

**Required fix**

- Add a registration policy with at least `open`, `invite-only` and `disabled`, stored as a deployment setting, defaulting to something other than `open` in production. **Decision needed:** which default, and whether email verification is required for `open`.
- Refuse production startup, or log loudly, when registration is `open`.
- Add per-user spend controls (see B-4) so that one account cannot drain shared keys.
- Restrict `GET /users/search` to something that cannot be used for harvesting. Exact-email match is one option.

**Done when:** an unauthenticated visitor cannot obtain a working account on a production deployment unless the operator opted in, and tests cover each policy value.

---

## P0-2. A cancelled execution is overwritten when its run finishes

**Evidence**

- `src/api/executions.py:546-553`: cancel sets `status='canceled'` with a guarded `update_many`. Its docstring states that the in-flight run is not pre-empted.
- `src/engine/langgraph_executor.py:196-205` (`_mark_completed`), `:229-235` (`_mark_waiting_approval`) and `:239-246` (`_mark_failed`) all write with an unconditional `update(where={"id": ...})`.
- `LangGraphExecutor.run()` catches every exception itself (`:312-320`), so the status-guarded crash handler in `src/api/internal.py:395-402` is almost never reached. The unguarded writes are the normal path.

**Impact:** a user cancels a running execution, the API reports `canceled`, the graph keeps running and its side effects still fire, and when it finishes the row becomes `completed`, `failed` or `waiting_approval`. The cancellation does not survive, and a cancelled execution can pause for approval and send an approval email.

**Required fix**

- Make all three executor writes conditional on the row still being `running`, using the same `update_many` pattern as the rest of the codebase. If zero rows are updated, do not emit a completion event and do not send an approval email.
- Check for cancellation between nodes so that a cancelled run stops firing side effects. The per-node wrapper in `src/engine/events_wrapper.py` is the natural place: re-read the row status before each node and abort the graph if it is no longer `running`.

**Done when:** an integration test cancels an execution mid-run and asserts that the final status is `canceled`, that no node after the cancel point ran, and that no approval email was sent.

---

## P0-3. Sweepers fail healthy executions and can overwrite finished ones

**Evidence**

- `src/maintenance/execution_sweeper.py:156-161`: `sweep_stuck_executions` selects `status='running'` rows whose `startedAt` is older than `EXECUTION_STUCK_AFTER_SECONDS`, which is 900 in production (`scripts/gcp-bootstrap.ps1:295`). `startedAt` is the row creation time (`prisma/schema.prisma:62`) and is never updated.
- Cloud Scheduler calls `/internal/sweep` every five minutes (`scripts/gcp-bootstrap.ps1:416`).
- `:166-175`, `:245-252` and `:316-325`: all three sweeps read rows with `find_many` and then write `failed` with an unconditional `update`.

**Impact**

1. **Every resumed approval is at risk.** An execution that waited more than 15 minutes for a human and is then resumed is `running` with an old `startedAt`. Any sweep that fires while it is running marks it `failed`. Because of P0-2 it is later overwritten to `completed`, so the status flaps, and a synchronous `/api/run` caller or a WebSocket client that sees the intermediate `failed` is told the run failed.
2. **Any run longer than 15 minutes** is marked failed mid-run, even though the lease allows 60 minutes (`src/config.py:336`).
3. **Lost update.** A row that completes between a sweep's read and its write is overwritten from `completed` to `failed` permanently. The same applies to an approval decided at the moment the approval-expiry sweep runs.

**Required fix**

- Base the stuck check on when the row last entered `running`, not on creation time. Options are a new `runningSince` column stamped at claim and at resume, or using `leaseExpiresAt` and limiting `sweep_stuck_executions` to rows with no lease.
- Align the stuck threshold with the lease length so the two cannot contradict each other.
- Convert every sweeper write to a conditional `update_many` that repeats the status and time predicate in the `WHERE` clause.

**Done when:** integration tests show that a resumed execution with an old `startedAt` is not swept, that a run within its lease is not swept, and that a row completing between the sweeper's read and write stays `completed`.

---

## P0-4. Lease recovery restarts the workflow from the beginning and repeats side effects

**Evidence**

- `src/maintenance/execution_sweeper.py:334-361`: an expired lease is re-enqueued with `kind="run"`, up to `execution_max_delivery_attempts` (5) times.
- `src/engine/langgraph_executor.py:154` and `:272`: `run()` builds a fresh `initial_state` and calls `compiled.ainvoke(state, config)` on the existing `thread_id`. Passing a new input to an existing thread starts the graph again from the Start node. It does not continue from the last checkpoint.
- No node has an idempotency key. The idempotency in `src/api/executions.py:172-177` only deduplicates the creating HTTP request.

**Impact:** if a worker dies partway through, the redelivered run sends the same emails, creates the same Jira issues and repeats the same HTTP mutations. A workflow that reliably outlives the request timeout does this five times before it is dead-lettered. The comment at `src/api/executions.py:164-167` says nothing re-runs executions, which was true before `sweep_expired_leases` was added and is not true now.

**Needs confirmation:** behavior of redelivering `kind="resume"` after the interrupt has already been consumed (`Command(resume=...)` against a thread that has moved on). Write the test before deciding on the fix.

**Required fix**

- On redelivery, continue from the checkpoint (`ainvoke(None, config)`) when one exists for the thread, and start fresh only when none exists.
- Because a node can still be interrupted after its side effect and before its checkpoint, give side-effecting nodes (email, Jira, Confluence, HTTP with a mutating method, Gamma, file-write) a deterministic idempotency key derived from execution id, node id and loop iteration, and pass it to providers that support one.
- Correct the stale comment.

**Done when:** an integration test kills a run after node N, triggers lease recovery, and asserts that nodes 1 to N did not execute a second time.

---

## P0-5. Cloud Tasks dispatch deadline is shorter than the execution model assumes

**Evidence**

- `src/execution/cloud_tasks.py:168-182` creates the task with no `dispatch_deadline`.
- `src/config.py:325-336` states that an execution runs to completion inside one Cloud Tasks request, with the lease sized to Cloud Run's 3600-second request timeout.

**Needs confirmation against current Google Cloud documentation:** the default dispatch deadline for a Cloud Tasks HTTP target is 10 minutes and the maximum is 30 minutes. If that holds, Cloud Tasks abandons the request after 10 minutes and retries. The retry is harmless because the claim query sees an unexpired lease, but the original request has lost its caller, and the service runs with CPU throttled outside requests and `min-instances=0` (`scripts/gcp-bootstrap.ps1:270-283`). That is the scale-to-zero failure this subsystem was built to prevent, now applying to any execution longer than 10 minutes.

**Required fix**

- Set `dispatch_deadline` explicitly to the maximum allowed and size `execution_lease_seconds` and the stuck threshold to match it.
- Add an execution-level timeout in the executor (`asyncio.wait_for` around `ainvoke`) slightly below the dispatch deadline, so that a run ends in a recorded `failed` state instead of being cut off.
- **Decision needed:** workflows that must run longer than 30 minutes need a different model, for example checkpoint-and-continue across several task deliveries (which P0-4's resume-from-checkpoint makes possible) or a Cloud Run Job. Do not provision infrastructure without a go-ahead.

**Done when:** the dispatch deadline, lease, stuck threshold and executor timeout are derived from one setting, and a test asserts that they stay consistent.

---

## P0-6. Sessions cannot be revoked and reset tokens are replayable

**Evidence**

- `src/security/jwt.py`: tokens carry only `sub`, `iat`, `exp` and `type`. There is no token identifier and no per-user token version.
- `src/api/auth_standalone.py:248-251`: `/auth/disconnect` does nothing.
- `:254-285` and `:323-358`: changing or resetting a password does not invalidate existing access tokens (8 hours) or refresh tokens (30 days).
- `:196-245`: refresh issues a new pair but the old refresh token remains valid, and reuse is not detected.
- `:323-358`: a reset token is a stateless 30-minute JWT that can be used any number of times within its lifetime. The forgot-password email and the forced-change login response issue the same token type, and `/auth/reset-password` accepts either without the current password.

**Impact:** a stolen refresh token gives 30 days of access that the user cannot end by logging out or by changing their password. Only an administrator deactivating the account stops it. A reset link that leaks through a mail gateway or browser history can be used again after the user has already reset.

**Required fix**

- Add a `tokenVersion` integer to `User`, include it in access and refresh tokens, and compare it in the existing per-request user lookup (`src/security/auth.py:96-111`). Increment it on password change, password reset, administrator-forced reset, deactivation and logout.
- Make reset tokens single-use. Binding the token to the current password hash or to `tokenVersion` achieves this without a new table.
- Make `/auth/disconnect` increment the version, or add refresh-token rotation with reuse detection.
- Requires a Prisma migration; see P1-9 about how migrations reach production.

**Done when:** tests show that tokens issued before a password change, reset or logout are rejected, and that a reset token works exactly once.

---

## P1-1. Users with a workflow assignment cannot run it or watch it

**Evidence**

- `src/api/workflows.py:286-300`: the docstring says an assignment grants "open, edit, run".
- `src/api/executions.py:158`: `POST /executions` allows only admin, owner, or a public workflow. Assignments are not checked, so an assignee gets 404.
- `src/api/run.py:137` applies the same rule for API-key runs.
- `_has_assignment` is used only in `workflows.py` and `src/api/builder.py:173`.

**Required fix:** move the "can this user read or run this workflow" rule into one shared function and use it in `create_execution`, `run_external` and the workflow read paths. **Decision needed:** whether an assignee or the workflow owner may also see executions started by other people on that workflow. Today `list_executions`, `get_execution` and the WebSocket (`src/api/events_ws.py:100`) allow only the starter and administrators.

**Done when:** a test proves an assignee can start an execution on a private workflow and follow it to completion.

---

## P1-2. The approval gate does not check who approves

**Evidence**

- `src/api/executions.py:589-593`: in-app resume is allowed for the user who started the execution, or an administrator. Nobody else.
- `src/executors/user_approval.py`: `approverEmail` is used only to address the email.
- There is no endpoint or page listing approvals waiting for the current user. The frontend has approval pages only for the email link and inside the starter's own run view.

**Impact:** the person who requests something can approve their own request in the app, while the named approver, if they are a Composer user, gets 404 and can act only through the email link. As a control, the approval node does not hold.

**Required fix. Decision needed** on the model. A reasonable default: when the approver email matches a Composer user, that user and administrators may decide in the app; the starter may not decide their own request unless the node opts in; add `GET /approvals/pending` and an inbox page. Record the decider in the existing `Approval` row.

**Done when:** tests cover approver allowed, starter denied by default, and administrator allowed, and the inbox lists exactly the caller's pending approvals.

---

## P1-3. Executions can be stranded in `queued` or `running` when enqueueing fails

**Evidence**

- `src/api/executions.py:208-263` handles an enqueue failure by marking the row failed. Other call sites do not:
  - `src/api/run.py:194`: the row stays `queued` permanently, and a retry with the same idempotency key returns the stuck row.
  - `src/api/internal.py:705-710`: the file is moved to the error folder but the row stays `queued`.
  - `src/api/executions.py:641-679` and `src/api/approval_email.py` (confirm handler): the row has already been set to `running` with its lease cleared, and the approval has been recorded. If enqueueing fails, the decision is lost and the row waits for the stuck sweeper to fail it.
- No sweeper looks at `queued` rows (`execution_sweeper.py` module docstring).

**Required fix**

- Put "create the row, enqueue it, mark it failed if enqueueing fails" into one helper used by every caller.
- For resume, enqueue before committing the status change, or roll the status back on failure so the approver can try again.
- Add a sweep that re-enqueues or fails `queued` rows older than a few minutes.

**Done when:** tests inject an enqueue failure at each call site and assert that no row remains `queued` or `running` without an owner.

---

## P1-4. Editing or deleting a workflow changes executions already in flight

**Evidence**

- `src/engine/langgraph_executor.py:138-152`: the graph is built from the current `workflows` row every time an execution is claimed or resumed. Nothing records the definition the execution started with.
- `src/api/workflows.py:519-607`: `PUT` replaces nodes and edges in place with no version check. Two editors, or an editor and the AI builder, silently overwrite each other, and edits to a production workflow are live immediately.
- `:610-632`: deleting a workflow is allowed while it has active executions. The cascade removes execution rows but not `langgraph_checkpoints`, `langgraph_checkpoint_writes` or `execution_events`, which have no foreign key. Compare the explicit cleanup in `src/api/executions.py:483-486`.

**Impact:** an execution paused for approval resumes against a graph that may have been edited in the meantime, possibly with the paused node removed. Deleting a workflow leaves checkpoint data behind permanently, and nothing ever removes checkpoints of completed executions either.

**Required fix**

- Minimum: store the nodes and edges used on the execution row at start and build the graph from that copy. Reject `PUT` when a supplied `updatedAt` or revision does not match (HTTP 409). Refuse to delete a workflow that has active executions, and delete checkpoints and events when deleting.
- Add checkpoint retention for finished executions to `/internal/sweep`.
- The complete answer is workflow versioning; see B-1.

**Done when:** tests show that a resumed execution uses the definition it started with, that concurrent `PUT`s produce a 409, and that deleting a workflow leaves no checkpoint or event rows.

---

## P1-5. Outbound request protection covers only the HTTP node and can be bypassed by DNS rebinding

**Evidence**

- `validate_outbound_url` is called in exactly one place, `src/executors/http.py:88`.
- User-supplied URLs are also fetched by MCP server registration and testing (`src/api/mcp_servers.py`, `src/mcp/client.py`), the vector database providers (`src/vectordb/providers/*`), the Jira and Confluence nodes (user-supplied domain), and `src/executors/download_pdf.py`. None of them validate the target.
- `src/security/ssrf.py:117-124` resolves the hostname to validate it, and `httpx` then resolves it again to connect. A hostname that answers with a public address first and a private one second passes the check.
- `src/security/ssrf.py:45` calls the blocking `socket.getaddrinfo` inside async code.

**Impact:** with open registration (P0-1), any user can make the backend send requests to internal addresses through an MCP server URL or a vector database URL.

**Required fix**

- Apply the same policy to every outbound client that takes a user-supplied host. A shared `httpx` transport that validates the address actually connected to closes the rebinding gap and covers every caller at once.
- Resolve DNS without blocking the event loop.

**Done when:** each listed integration has a test that rejects a loopback or metadata target, and a rebinding test (first resolution public, second private) is blocked.

---

## P1-6. The file-write node writes anywhere on the server, and its output cannot be retrieved in the cloud

**Evidence**

- `src/executors/file_write.py:20-22`: `local` is the only registered provider.
- `src/storage_providers/local.py:56-60`: creates the destination directory and writes with no root restriction. The destination is substituted from workflow state. Only the filename is validated.
- `src/config.py:211`: the approval attachment root is `/tmp/composer-attachments`. On Cloud Run, `/tmp` is held in instance memory and discarded with the instance.

**Impact:** on the shared deployment, any user can write files to any path the process can write, including other users' output paths. Written files consume instance memory (1 GiB limit) and are never cleaned up. Apart from an approval-email attachment produced in the same request, a hosted user has no way to obtain a file they wrote. Google Drive can trigger a workflow but cannot receive its output.

**Required fix**

- Confine the local provider to a configured root with a per-user or per-execution subdirectory, and reject paths that resolve outside it.
- Add a size cap and cleanup.
- Add a Google Drive write provider, using the existing `CloudStorageConnection`, so hosted workflows have somewhere durable to write. This is new node capability and must stay generic.

**Done when:** tests reject a destination outside the root, and a hosted workflow can write a file the user can then open.

---

## P1-7. An editor can redirect a stored secret to a host they control

**Evidence**

- `src/api/workflows.py:181-283`: when a node's secret arrives as the redacted marker, the stored secret is kept, matched only by node id.
- The same request may change the fields that decide where the secret is sent: the Jira domain, the Confluence base URL, the HTTP node URL, the vector database URL.
- `PUT` is open to assignees (`:535-539`).

**Impact:** a user who has been given edit access, but who cannot read the owner's Jira token or Authorization header, can point the node at their own server, run the workflow, and receive the secret.

**Required fix:** when a node's destination field changes and the secret is still the redacted marker, require the secret to be entered again (HTTP 422 naming the field). The long-term fix is to hold credentials outside node data; see B-3.

**Done when:** a test changes a Jira node's domain while sending the redacted token and gets a 422.

---

## P1-8. Authentication configuration fails open and embedded mode is incomplete

**Evidence**

- `src/config.py:25`: `environment` is a free string defaulting to `"development"`. `src/security/auth.py:169-171` returns user `dev` for any request without a token when the value is `development`, and `src/main.py:112-113` allows every origin with credentials. A deployment where the variable is missing therefore runs with no authentication. Production validation runs only for the exact string `production` (`src/config_validation.py:39`); `prod` or `Production` skips every check.
- Embedded mode: `src/main.py:89-93` accepts `IEP_JWKS_URL` alone as valid configuration, but `src/security/auth.py:133-137` implements only the shared-secret path, so every request is rejected. Embedded mode also skips the active-user check (`auth.py:175-176`), does not validate audience or require `exp`, and has no user rows, so no caller can be an administrator.
- `src/api/auth_standalone.py:132` and `:171` store and look up emails without lower-casing, while SSO (`src/api/auth_common.py`) and owner assignment (`workflows.py:728`) lower-case. `A@x.com` and `a@x.com` can be two accounts, and a mixed-case registration cannot be found by owner assignment.
- `src/api/auth_common.py` (SSO exchange): an SSO login whose email matches an existing password account signs into that account, falls back to `preferred_username` when there is no `email` claim, and does not check `mustChangePassword`.

**Required fix**

- Make `environment` a `Literal["development", "staging", "production"]` and require an explicit second setting to enable the unauthenticated development fallback.
- Either implement JWKS verification for embedded mode or reject JWKS-only configuration at startup. Require `exp` and validate audience.
- Normalize emails to lower case on write and lookup, with a migration that detects existing case-collisions before applying.
- **Decision needed:** whether SSO may attach to an existing password account automatically.

**Done when:** an unknown `ENVIRONMENT` value refuses to start, the development fallback cannot be reached without the explicit flag, and email-case tests pass.

---

## P1-9. Database migrations are not part of deployment

**Evidence:** `prisma migrate deploy` appears only in CI workflows. Neither `.github/workflows/deploy-gcp.yml` nor the `Dockerfile` runs it.

**Impact:** a push to `main` deploys code that expects a new schema while production still has the old one, unless someone applies the migration by hand first. Several fixes in this list add columns.

**Required fix:** add a migration step to the deploy workflow that runs before the new revision receives traffic, and fails the deploy when it fails. A Cloud Run Job or a direct connection from the workflow both work. **Decision needed** before provisioning anything.

**Done when:** a deploy with a pending migration applies it, or stops, before the new revision serves requests.

---

## P1-10. The external run API has no way to return an asynchronous result

**Evidence**

- `src/api/run.py:196-202`: asynchronous mode returns an `executionId` and a `streamUrl`.
- `src/api/events_ws.py:40-56` and `GET /executions/{id}` accept only user JWTs. An API key (`ck_...`) is rejected by both.
- `src/api/run.py:179`: `streamUrl` is built by replacing `http` with `ws` in the request's base URL, which gives `ws://` when the scheme is not forwarded.
- Synchronous mode is capped at 300 seconds and holds the request open while polling the database every 250 ms.

**Impact:** an integration holding only an API key can start a workflow but cannot learn its outcome unless it finishes within the synchronous window.

**Required fix:** add `GET /api/run/executions/{id}` authenticated by API key and limited to executions that key's user started; build `streamUrl` from `BACKEND_PUBLIC_URL`; either accept API keys on the WebSocket or stop returning `streamUrl` to API-key callers. Completion webhooks are listed in B-6.

**Done when:** a test starts an asynchronous run with an API key and retrieves the final output with the same key.

---

## P2-1. Blocking work runs on the event loop

**Evidence:** bcrypt in `src/security/passwords.py:17,29` and `src/security/api_keys.py:33,39`; PDF and DOCX parsing in `src/api/uploads.py:98` and `src/storage_providers/text_extraction.py:23`; Markdown conversion in `src/executors/file_write.py:126-130` and `src/executors/download_pdf.py:142`; file reads and writes in `src/storage_providers/local.py`. `asyncio.to_thread` or an executor is used in only three places in `src/`.

**Impact:** the API, the WebSockets and every running execution share one event loop. A 10 MB PDF, or a burst of logins or API-key calls at about a quarter of a second of bcrypt each, stalls all of them. `POST /api/run` pays the bcrypt cost on every call, before the rate limiter runs.

**Required fix:** move these calls to `asyncio.to_thread`. Hash API keys with SHA-256 or HMAC instead of bcrypt: they are 32 random characters and gain nothing from a slow hash. Keep bcrypt verification as a fallback for existing keys and re-hash on first use.

## P2-2. One Postgres connection per WebSocket client

**Evidence:** `src/api/events_ws.py:154-155` opens a dedicated `asyncpg` connection per client and listens on one global channel, so every client wakes and queries on every event from any execution.

**Impact:** connection exhaustion on Neon with modest numbers of open run pages. **Needs confirmation:** if `DATABASE_URL` is a pooled (PgBouncer transaction-mode) endpoint, `LISTEN` does not deliver, and delivery silently falls back to the 15-second poll.

**Required fix:** one shared listener connection per process that fans out to in-process subscribers keyed by execution id, on a direct (unpooled) connection string.

## P2-3. No resilience or limits at the engine level

**Evidence:** no retry policy, per-node timeout or execution timeout in `src/engine/` (a search for `retry`, `wait_for` and `RetryPolicy` finds nothing functional). Agent loops allow up to 100 iterations (`src/executors/agent.py:33`) with no token budget.

**Required fix:** per-node timeout and optional retry with backoff for nodes marked safe to retry (depends on P0-4's idempotency keys), and a per-execution token and time budget.

## P2-4. Smaller items

- `src/security/api_key_auth.py:49`: lookup uses `find_first` on a non-unique nine-character prefix. Two keys with the same prefix lock one of them out. Iterate over matches or look up by a unique hash.
- API-key authentication does not check `mustChangePassword`, and neither does the WebSocket.
- `src/api/workflows.py:568-580`: any owner or assignee can set `isProduction`, `externalSlug` and `isPublic` through `PUT`, while `PATCH .../admin-flags` (`:664`) suggests these were meant to be administrator-controlled. **Decision needed** on which is intended.
- `GET /workflows` returns full node and edge JSON for up to 100 workflows per page. Add a summary projection for list views.
- `src/api/internal.py:558-762`: file-trigger polling processes every production workflow serially inside one request. It needs a per-run time budget before the number of workflows grows.
- Login rate limiting is per IP only. Add a per-account limit.
- Secret redaction in `src/api/workflows.py:103-178` is a per-node-type allow-list, so a new node type with a secret field leaks it until someone adds a redactor. Drive redaction from a marker on the Pydantic field instead.

---

# Part B — Major missing functionality

These are product gaps, not bugs. Each needs a design decision from the repository owner before implementation. They are ordered by how much they limit using Composer as a production workflow platform.

## Repository-owner clarification: centralized platform keys are intentional

Composer's centrally stored provider keys are an enterprise feature, not an accidental shortcut or a missing bring-your-own-key implementation. The Phase 9 design deliberately makes Postgres the source of truth for encrypted, administrator-managed LLM and platform-service keys. The application should retain that model.

Credential work must distinguish two different classes:

- **Platform credentials:** LLM, Resend, Gamma, Tavily and similar operator-funded services. These remain centrally managed by administrators and shared by authorized users of the deployment.
- **User or team connections:** Jira, Confluence, Google Drive, MCP OAuth, databases and customer-specific endpoints. These may benefit from connection records referenced by id, scoped ownership, rotation and usage tracking.

Central platform keys strengthen the need for controlled registration, authorization, usage metering, budgets and auditability. They do not imply that every user should have a separate provider key. Consequently, P0-1 is a deployment admission-control issue: it is P0 when a production instance is publicly reachable with open self-registration, but not merely because the deployment uses centralized keys.

| # | Gap | What exists today | What is missing |
|---|---|---|---|
| B-1 | **Workflow versioning and release** | A free-text `version` field; `PUT` overwrites in place | Immutable published versions, a draft separate from the published version, change history, rollback, and executions tied to a version. Fixes P1-4 properly. |
| B-2 | **Triggers** | Manual run, API run, Google Drive file polling | Scheduled (cron) runs and inbound webhooks. Both are standard for a workflow platform, and the Cloud Scheduler plumbing already exists. |
| B-3 | **User/team connection management** | Centrally administered platform keys are intentional; user-owned integration secrets are also pasted into individual nodes and encrypted in workflow JSON | Preserve centralized platform keys. Add a connection store only for user/team-owned external systems, referenced by id with scoped ownership, rotation and usage tracking. Already recorded as deferred items P0-5 and P2-2 in `docs/deferred-backlog.md`. Fixes P1-7 properly. |
| B-4 | **Usage metering, quotas and cost visibility** | Per-minute request limits | Token and cost capture per execution and per user, budgets and hard caps, and an administrator usage view. With shared LLM keys this is the main financial control, and it is absent. |
| B-5 | **Workflow and execution audit trail** | `Approval` rows, execution status/output and transient execution events exist, but do not form a complete immutable business audit | An append-only record covering workflow design/publish history and every execution: initiator, immutable workflow version/snapshot, node transitions, sanitized inputs/outputs, errors, approvals, retries, overrides, recovery actions and actor identity. Administrative security events such as logins, role changes, key changes, sharing and deletion also belong in the audit trail. |
| B-6 | **Human-assisted failure recovery** | A failed node fails the execution; checkpoints exist but there is no supported recovery operation | Let an authorized operator inspect a failed execution, correct explicitly permitted state/input, record a reason, and resume from the last safe checkpoint without replaying completed side effects. Include per-node error branches, safe retries, failure notifications and completion webhooks. Every override and recovery attempt must be audited. |
| B-7 | **Approvals as a feature** | Email link, plus in-app approval by the starter | Approver inbox, enforced approver identity, multiple approvers or quorum, delegation, reminders and escalation before the 168-hour expiry. Extends P1-2. |
| B-8 | **Teams and tenancy** | Two roles (admin, member) and per-user sharing | Organizations or workspaces, team-owned workflows, and roles finer than admin and member (viewer, runner, editor). Every user on a deployment currently shares one namespace, one user directory and one set of keys. |
| B-9 | **Application telemetry and operational visibility** | Application logs and optional LangSmith tracing | First-class OpenTelemetry-style traces, metrics and structured logs with correlation across request, execution and node ids. Capture queue depth, execution/node duration, failure and retry rates, checkpoint/recovery outcomes, provider latency, LLM tokens and cost, while excluding secrets and sensitive payloads by default. Add alerting, a database-aware readiness probe and an administrator view of stuck, failed and dead-lettered executions. |
| B-10 | **Composition** | Linear flow, if/else, while, join | Sub-workflows (calling one workflow from another), parallel map over a list, and a dry-run mode (already deferred as P2-1). |
| B-11 | **Account lifecycle** | Register, login, reset, administrator deactivation | Email verification, multi-factor authentication for password accounts, self-service account deletion with data export, and a retention policy for execution inputs and outputs, which can hold uploaded document text indefinitely. |

---

# Recommended order

1. **P0-1** registration policy. It removes the anonymous attacker from most other items.
2. **P0-2 and P0-3** together. Both are the same change (make status writes conditional) and both corrupt execution status today.
3. **P1-9** migration step in deploy, because P0-6 and others need schema changes.
4. **P0-6** session revocation.
5. **P0-5**, then **P0-4**. Confirm the Cloud Tasks limits first, since the answer shapes the resume design.
6. **P1-3**, **P1-1**, **P1-2**: execution and approval correctness.
7. **P1-5**, **P1-6**, **P1-7**, **P1-8**: security hardening.
8. **P1-4** and **P1-10**, then P2.
9. Design **B-9 telemetry**, **B-5 audit trail** and **B-6 human-assisted recovery** as one execution-operations initiative. Instrumentation and an immutable audit record should precede exposing recovery controls, because every override must be observable and attributable.
10. Continue the remaining Part B roadmap by owner decision. B-4, B-1 and B-2 give the most value next.

# Cross-cutting design requirements for telemetry, recovery and audit

These three capabilities are related but must not be collapsed into one data store:

1. **Telemetry is operational and aggregatable.** It answers whether the application is healthy and where time, errors and cost are accumulating. It may have sampled traces and finite retention.
2. **The audit trail is durable and append-only.** It answers who did what, to which workflow or execution, when, and why. Audit records must not depend on sampled telemetry and must have an explicit retention/export policy.
3. **Recovery is a privileged state transition.** It uses durable LangGraph checkpoints, but must select an explicitly identified safe checkpoint. It must never silently restart from the beginning or automatically repeat completed side effects.

Minimum recovery behavior:

- Only an administrator or a future role with explicit recovery permission may initiate it.
- The operator sees the failed node, error, last completed nodes, selected checkpoint and proposed state changes before confirming.
- The original failed execution remains immutable. A recovery attempt is represented as a new attempt linked to the original execution, or by an equally explicit attempt model that preserves the full history.
- The operator supplies a mandatory reason. Actor, timestamp, source execution, checkpoint, state patch and result are appended to the audit trail.
- State editing is allow-listed and schema validated; secrets are never displayed or copied into audit/telemetry payloads.
- Recovery proceeds from the last safe persisted boundary. Nodes with non-idempotent side effects require provider idempotency support or an explicit operator acknowledgement when safe continuation cannot be guaranteed.
- Concurrent recovery, cancel and ordinary resume operations are protected by conditional status transitions so only one wins.

Minimum telemetry behavior:

- Propagate correlation identifiers for HTTP request, workflow, execution, attempt and node.
- Emit execution/node counters and latency histograms, queue/lease gauges, provider-call metrics, recovery results, and LLM token/cost measurements where providers expose usage.
- Produce distributed traces around enqueue, claim, graph/node execution, checkpoint persistence, provider calls and WebSocket/event delivery.
- Use structured logs carrying the same identifiers, with centralized redaction and bounded field sizes.
- Define service-level alerts for queue age, failure rate, stuck/expired leases, recovery failures, provider errors and abnormal spend.
- Keep telemetry export vendor-neutral; LangSmith may remain an optional LLM-focused destination rather than the sole application observability system.

Minimum audit behavior:

- Record workflow create, edit, publish, rollback, sharing, ownership and deletion events, tied to an immutable workflow revision or definition hash.
- Record execution creation and trigger identity, the exact workflow snapshot/version, every node state transition, approval decision, cancellation, timeout, failure, retry and recovery action.
- Record security and administrative events including authentication, role and account-state changes, API-key lifecycle, centralized provider-key lifecycle and deployment-setting changes.
- Store sanitized metadata by default. Inputs and outputs may contain regulated or confidential data, so full payload capture must be configurable, encrypted, access-controlled and governed by retention policy.
- Make records append-only at the application permission layer, with pagination, filtering and export available to authorized administrators/auditors.
- Test that secrets, authorization headers and decrypted node credentials can never enter audit events, spans, metrics labels or structured logs.

# What this audit did not cover

- The frontend beyond its authentication wiring and page inventory. Component behavior, accessibility and client-side validation were not reviewed.
- The AI builder (`src/builder/`), the MCP OAuth flow, the migration tooling (`src/migration/`) and the individual vector database providers, beyond the outbound-request point in P1-5.
- Whether execution results (`nodeResults`, `variables`) can contain decrypted secrets. This was not traced and is worth a dedicated pass.
- Anything requiring execution: no tests were run, no dependency scan was repeated, and the deployed instance was not probed.
