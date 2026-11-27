# Architecture

How Composer is built today. This is the document to read before adding a node type, an integration, or a new executor.

## Big picture

```
┌────────────────────────────────────────────────────────────────────────┐
│                       Browser (Next.js 15 frontend)                    │
│  Designer canvas (React Flow)  •  Runs page  •  Admin console          │
└──────────┬───────────────────────┬───────────────────────┬─────────────┘
           │ JWT (NextAuth)        │ WebSocket             │ JWT
           ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                 FastAPI backend (src/main.py routers)                   │
│   /workflows /executions /api/run/{slug} /uploads /admin/* /events …   │
└──────────┬───────────────┬───────────────┬───────────────┬──────────────┘
           │               │               │               │
           ▼               ▼               ▼               ▼
   ┌───────────────┐  ┌──────────┐  ┌─────────────┐  ┌────────────────┐
   │  LangGraph    │  │ Postgres │  │ MCP servers │  │  LLM providers │
   │  executor     │◄─┤ Postgres │  │ (HTTP/SSE)  │  │  (Anthropic /  │
   │  + executors  │  │          │  │             │  │   OpenAI / …)  │
   └───────────────┘  └──────────┘  └─────────────┘  └────────────────┘
```

- Frontend authenticates the user via NextAuth (Azure AD or username/password). Every API call carries a Composer JWT (HS256) the backend mints from the auth provider's claims.
- API routes are FastAPI `APIRouter`s. Each major surface (workflows, executions, MCP, admin, uploads, run) lives in its own module under [`src/api/`](../src/api/).
- LangGraph drives execution. The graph is built fresh per run from the workflow's `nodes`/`edges` JSON; node executors are registered via a small `register_executor` decorator in [`src/executors/base.py`](../src/executors/base.py).
- Postgres (SQLAlchemy 2 async on psycopg 3, migrated by Alembic; Prisma was removed in phase 6) is the single source of truth: workflows, executions, checkpoints, users, API keys, MCP servers + OAuth tokens, LLM API keys, deployment settings, LLM model catalog, approvals.

## Repository layout

```
composer/
├── src/
│   ├── main.py                  # FastAPI app + router wiring
│   ├── config.py                # Pydantic Settings (env vars)
│   ├── api/                     # REST + WebSocket endpoints
│   │   ├── workflows.py            CRUD + list + search + admin overrides
│   │   ├── executions.py           POST/GET, approval resume, history
│   │   ├── run.py                  POST /api/run/{slug} external invoke
│   │   ├── uploads.py              POST /uploads/extract-text
│   │   ├── api_keys.py             per-user API key management
│   │   ├── auth_*.py               standalone + common auth flows
│   │   ├── admin_*.py              users, LLM keys, models, MCP settings
│   │   ├── mcp_servers.py          MCP CRUD + OAuth callback
│   │   ├── llm_models*.py          live + DB-curated model catalog
│   │   ├── events_ws.py            /executions/{id}/ws — reconnect cursor + LISTEN wake-up
│   │   ├── approval_email.py       public GET /approvals/email/{token} — resolve a signed decision
│   │   ├── cloud_storage_oauth.py  Google Drive OAuth connect + Picker token issuance
│   │   └── internal.py             /internal/claim-and-run, /internal/sweep, /internal/poll-file-triggers (OIDC push auth)
│   ├── execution/
│   │   └── cloud_tasks.py          enqueue_execution_task() — Cloud Tasks push wrapper
│   ├── engine/
│   │   ├── langgraph_executor.py   build_graph() + run() + interrupts
│   │   ├── workflow.py             Pydantic node + edge schemas
│   │   ├── state.py                WorkflowStateDict, reducers
│   │   ├── events_wrapper.py       per-node event emission + alias spread
│   │   ├── events.py               ExecutionEvent / EventType definitions
│   │   ├── events_pg.py            PostgresEventStore — durable, sequence-numbered
│   │   ├── events_notify.py        Postgres LISTEN/NOTIFY wake-up signal
│   │   └── graph_builder.py        validation + conditional routing
│   ├── executors/                  # 22 Designer node-type implementations
│   │   ├── base.py                    register_executor + dispatch
│   │   ├── _eval.py                   simpleeval wrapper (Mustache aware)
│   │   ├── start.py / end.py / agent.py / http.py / …
│   │   ├── jira.py / confluence.py    per-node credentials, encrypted at rest (ADR-0028)
│   │   ├── file_write.py / download_pdf.py   write via a FileStorageProvider
│   │   └── vector_db.py               query + upsert dispatch
│   ├── llm/
│   │   ├── providers.py            Anthropic/OpenAI/Google/Groq dispatch
│   │   └── structured_output.py    JSON-mode invocation helpers
│   ├── mcp/
│   │   ├── client.py               streamable-HTTP MCP transport
│   │   ├── oauth.py                Authorization Code flow + PKCE + RFC 8707
│   │   └── base.py                 manual tools/list + binding to LangChain Tool
│   ├── tools/
│   │   ├── base.py                 BuildContext + provider framework
│   │   ├── registry.py             selectedTools resolution
│   │   └── providers/              tavily / firecrawl / serper / …
│   ├── vectordb/
│   │   ├── embedding.py            OpenAI text-embedding-3-small
│   │   └── providers/              pinecone / qdrant / chroma / weaviate / milvus
│   ├── storage_providers/
│   │   ├── base.py                 FileStorageProvider ABC, FileRef, HealthStatus
│   │   ├── local.py                LocalFilesystemProvider — polling, partial-write-safe
│   │   └── google_drive.py         GoogleDriveProvider — appProperties claim marker
│   ├── conversion/
│   │   ├── markdown_to_docx.py     markdown-it-py token-walker onto python-docx
│   │   ├── markdown_to_pdf.py      markdown-it-py → HTML → xhtml2pdf
│   │   ├── html_to_pdf.py          direct HTML → xhtml2pdf (download-pdf's html mode)
│   │   └── _pdf_security.py        shared deny-all link_callback (SSRF-safe PDF export)
│   ├── cli/
│   │   ├── watch.py                composer watch — polls a folder, drives POST /api/run/{slug}
│   │   └── main.py                 CLI entry point (watch, keys subcommands)
│   ├── security/
│   │   ├── auth.py                 dependency injection for user_id + role
│   │   ├── jwt.py                  Composer JWT mint + verify (access/refresh/password_change/approval_email)
│   │   ├── api_key_auth.py         per-user API key auth
│   │   ├── encryption.py           AES-256-GCM + encrypt_marked/decrypt_marked + header redaction
│   │   ├── rate_limit.py           in-memory token bucket (protocol shared with the below)
│   │   ├── rate_limit_pg.py        Postgres-backed atomic token bucket (cross-instance correct)
│   │   ├── passwords.py            bcrypt
│   │   └── sso_azure.py            Azure AD JWKS validation
│   ├── checkpoints/
│   │   ├── saver.py                official AsyncPostgresSaver pool + one-shot saver for the CLI
│   │   ├── schema.py               startup check of the saver's schema version (Alembic 0004)
│   │   ├── legacy.py               SQLAlchemy port of the old saver (copy tool, rollback; phase 7 deletes)
│   │   ├── copy.py, versions.py    two-way copy with channel-version conversion
│   │   ├── sync.py                 deploy guard (find_unsynced) and copy-on-resume (bring_forward)
│   │   └── runtime.py              registered checkpointer for the running process
│   ├── maintenance/
│   │   └── execution_sweeper.py    sweep_stuck_executions / sweep_expired_approvals /
│   │                                sweep_expired_leases / sweep_old_execution_events —
│   │                                invoked by POST /internal/sweep, not an in-process loop
│   ├── migration/                  OAB → Composer one-shot importer
│   ├── config_validation.py        validate_production_config() — FastAPI startup guard
│   └── integrations/               LangSmith config threading, Resend email, Google Drive OAuth
├── frontend/
│   ├── app/                        Next.js App Router
│   │   ├── designer/                  canvas + templates + settings
│   │   ├── runs/                      list / details / approval / API keys
│   │   ├── admin/                     users / LLM keys / models / MCP / tools
│   │   └── (auth)/                    login / register / sso-callback
│   ├── components/composer/        canvas, panels, role-nav, etc.
│   ├── lib/api/                    typed REST clients (OpenAPI-generated schema)
│   └── e2e/                        Playwright suite
├── alembic.ini                     Alembic config; revisions in src/db/migrations/versions/ (0001 creates the base schema)
├── tests/                          1,751 backend tests collected on 2026-11-20
├── scripts/seed_templates.py       Seeds the 21 reference templates
└── docs/                           ← you are here
```

## Data access (the Prisma to SQLAlchemy migration, finished in phase 6)

Tables moved from Prisma to SQLAlchemy 2.x one group at a time (spec: `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`). Migrated tables are reached through repository modules in `src/db/repos/`: routes and services call named functions (for example in `users.py` or `api_keys.py`) instead of building queries. The caller owns the transaction, and repositories return plain records rather than ORM objects. Code outside a request (the CLI, key sync, API-key auth) gets sessions from `src/db/runtime.py`. Phases 1 to 6 are done: `deployment_settings`, `llm_models`, `llm_api_keys`, `users`, `api_keys`, `mcp_servers`, `mcp_oauth_tokens`, `mcp_oauth_states`, `cloud_storage_connections`, `workflows`, `workflow_assignments`, `workflow_executions`, `execution_events`, `rate_limit_buckets` and `approvals` are on SQLAlchemy. The LangGraph checkpoint tables are handled by the official saver (phase 5, below), and `tests/unit/db/test_prisma_import_boundary.py` keeps `src`, `tests`, `scripts` and `frontend/scripts` from importing Prisma or asyncpg (neither package is installed any more). Alembic is the only migration tool: `0001_baseline` creates the base schema on an empty database, and `alembic upgrade head` adopts a database that only Prisma built or refuses it (see [ADR-0037](decisions.md)).

**Short-session rule for code outside a request.** Executors, the MCP resolver and OAuth helpers, and the Drive token helpers take a `sessionmaker` parameter (executors get it from `get_registered_sessionmaker()` in `src/db/runtime.py`; routes use `require_sessionmaker(request)`, which answers 503 when no database is attached). Each DB step opens its own short `async with sessionmaker() as session:` block, commits, and closes it before the next step. No session is ever open across network I/O (MCP calls, OAuth token POSTs, Google refresh, Drive API, LLM calls), because an open session holds a pooled connection. Unit tests record session and network events to pin the ordering.

**Workflows.** Ownership and access checks on grant and revoke take a FOR NO KEY UPDATE row lock on the workflow (`workflows.get(for_update=True)`), so they serialize against an owner reassignment or delete but not against inserts of executions or assignments. The builder reads the workflow in one short session, runs its LLM loop with none open, and re-checks access under the lock in a fresh session when saving. External invoke, execution start, `poll_file_triggers` and the executor read workflows in short sessions closed before any network call, wait or graph run.

**Executions, events and rate limits (phase 4a).** `workflow_executions`, `execution_events` and `rate_limit_buckets` are reached through `src/db/repos/executions.py`, `events.py` and `rate_limits.py`. Every guarded status transition (cancel, mark failed or completed, mark waiting for approval, the sweeper's fail-if-unchanged functions) is one named repo function issuing ONE `UPDATE workflow_executions SET ... WHERE id = :id AND <guard> RETURNING id` that returns whether a row came back, so two writers can never both win and nothing reads then writes. **The claim** (`/internal/claim-and-run`) is a single statement, `UPDATE ... WHERE id = (SELECT id ... FOR UPDATE SKIP LOCKED) AND <eligibility> RETURNING`, under `SET LOCAL lock_timeout` and `statement_timeout` and an `asyncio.timeout`; it commits before any graph run starts, and the executor then holds no session across `ainvoke`. **Event sequence numbers** are allocated under a per-execution `pg_advisory_xact_lock` followed by `INSERT ... SELECT COALESCE(MAX(seq), 0) + 1`, committed at once, so parallel branches never collide and a retention sweep never makes later events reuse a sequence number; an event's row is committed before its NOTIFY is sent (LISTEN/NOTIFY runs on psycopg since phase 5). This lock is held for one INSERT and the commit and is the one documented exception to the non-blocking rule in `src/db/locks.py`. **The rate limiter** reads the bucket with `SELECT ... FOR UPDATE` in a short transaction, so concurrent checks of one bucket serialize and cannot over-admit. **Deleting executions** is one transaction: the guarded `DELETE ... RETURNING id, thread_id` and then the checkpoint rows and events of exactly those executions (checkpoint rows of all five checkpoint tables through `src/db/repos/checkpoints.py`).

**Approvals and resume (phase 4b).** `approvals` is reached through `src/db/repos/approvals.py`. Resuming a paused run (`POST /executions/{id}/resume` and the emailed-link confirm) is one transaction: `executions.resume_if_waiting` issues one guarded `UPDATE ... WHERE status = 'waiting_approval' RETURNING` that sets `running`, clears the lease and stamps `_resume_decision` in the same statement (the emailed link also requires the same pause node and `_pending_approval_since`, so a stale link cannot resolve a later pause), the `approvals` row is inserted in the same transaction, and the Cloud Task is enqueued only after the commit. After phase 5 nothing in `src` used Prisma; phase 6 removed the client, the `prisma/` schema directory, asyncpg and Node.js from the backend image.

**Checkpoints and notifications (phase 5).** Graphs checkpoint with the official `AsyncPostgresSaver` (`src/checkpoints/saver.py`) on its own `psycopg_pool.AsyncConnectionPool` (autocommit, `dict_row`, `prepare_threshold=None` for Neon's pooler; min 1, max 5, 10 s wait), opened in the app lifespan and registered for the process. It is separate from the SQLAlchemy pool because the saver needs autocommit and pipelined writes, and so a long run cannot starve request handlers; a connection is checked out per saver call, never held for the length of a run. The saver's tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) belong to Alembic revision `0004_langgraph_saver_tables`; `setup()` is never called and startup refuses to run if the recorded saver version differs at all from what the library expects (older or newer). The old Prisma tables stay until phase 7: `legacy.py` reads them for the copy tool (`composer checkpoints copy`) and rollback, `sync.py` provides the deploy guard and copy-on-resume, and a resume that finds no checkpoint fails the run with `checkpoint_missing` (or `checkpoint_sync_failed` if the copy-forward errors) instead of restarting it. Event wake-ups: NOTIFY goes through the SQLAlchemy pool; LISTEN uses `ExecutionEventListener` (`src/engine/notifications.py`), one lazy psycopg connection per process on the direct endpoint, opened when the first WebSocket watches and closed after `event_listener_idle_close_seconds`. See `docs/operations/production-deployment.md` for the cutover and rollback runbook.

**Documented exception: token refresh.** Refreshing an expired OAuth token (`refresh_token` in `src/mcp/oauth.py`, `_refresh_connection` in `src/integrations/google_drive/oauth.py`) holds ONE session open across the IdP refresh POST, because a transaction-scoped Postgres advisory lock (`src/db/locks.py`) must live as long as the POST so that two concurrent callers post a refresh token once. The lock is keyed on the token's natural key (`mcp_oauth_token:{server_id}:{user_id}`, `cloud_storage_connection:{user_id}:{provider}:{account_email}`) and the OAuth code exchange / re-grant takes the same lock, so a re-authorization cannot be overwritten by an in-flight refresh. Only the winner holds a connection: the lock is taken with the non-blocking `pg_try_advisory_xact_lock`, and a refused caller rolls back and closes its session, sleeps 100-400 ms (jittered), re-reads the row in a short session and returns it once it is fresh, else tries again until a 35 s deadline (longer than the 30 s bound on the POST itself), then raises the refresh error. The fast path (token still valid) keeps the short read session and takes no lock. The refresh POST is the only network call allowed inside an open session. The lock is transaction-scoped, never session-scoped, because Neon's transaction-mode pooler does not support session-level advisory locks.

**MCP custom headers.** A server's custom headers (`mcp_servers.headers`, sensitive values encrypted at rest) are sent on every MCP request, merged by `McpToolProvider` under the client's own headers and the auth header. Create validates them (RFC 7230 names, no CR/LF/NUL in values, no hop-by-hop or framing headers such as Host or Content-Length; surrounding whitespace is stripped) and errors name the header, never the value; rows saved before that are filtered at send time. A shared server sends its OWNER's custom headers for every user who can use it, mirroring the owner-token fallback (MCP fix #5).

## Execution model

A workflow is a JSON pair of `nodes[]` + `edges[]`. The executor:

1. **Loads the workflow row**, validates against `WorkflowSchema` (Pydantic discriminated union over node `type`).
2. **Compiles a LangGraph `StateGraph`** via [`graph_builder.build_graph`](../src/engine/graph_builder.py). Every node becomes a node in the StateGraph; every edge becomes either a plain edge or a conditional edge (for `if-else` / `while` / `user-approval`).
3. **Runs `compiled.ainvoke(initial_state, config={"configurable": {"thread_id": …}})`**. Each node receives the current state and returns a delta; LangGraph's reducers (`merge_dict` for variables, `add` for chat history) merge deltas back.
4. **Persists checkpoints** via the official `AsyncPostgresSaver` after every node — this is what makes interrupts (`user-approval`) and resumes work.
5. **Emits events** to `PostgresEventStore` (durable, sequence-numbered) plus a Postgres `NOTIFY` (low-latency wake-up). The WebSocket endpoint at `/executions/{id}/ws` replays from a reconnect cursor then subscribes live, streaming `node_started` / `node_completed` / `node_failed` / `execution_completed` events to the client. See "Durable execution, events, and rate limits" below for the full flow, including how `POST /executions` itself reaches step 1 (via Cloud Tasks, not a request-bound background task).

### State shape

```python
class WorkflowStateDict(TypedDict):
    variables:       Annotated[dict[str, Any], merge_dict]   # the eval / substitution scope
    chat_history:    Annotated[list[ChatMessage], add]
    current_node_id: Annotated[str, last_wins]
    node_results:    Annotated[dict[str, NodeExecutionResult], merge_dict]
    pending_auth:    Annotated[dict[str, Any] | None, last_wins]
    loop_results:    Annotated[list[Any], add]
    user_id:         NotRequired[str]
```

`variables` is the central dict — every executor reads from and writes to it. `lastOutput` and `input` are seeded by Start; the engine adds `_while_iterations` and `_guardrails_result` as it runs.

### Variable substitution + eval

Two engines, two scopes:

- **`{{path.to.field}}` substitution** ([`src/variable_substitution.py`](../src/variable_substitution.py)) — used everywhere prompts/URLs/values get rendered. Walks `state["variables"]` by dotted path; non-string values are JSON-serialised. Unresolved paths render as the literal `{{...}}` text (matches OAB).
- **simpleeval expressions** ([`src/executors/_eval.py`](../src/executors/_eval.py)) — used by `if-else.condition`, `while.condition`, `transform.transformScript`, `data-transform.collection` + `expression`. Sandboxed: no `eval`/`exec`/`import`/dunder access. Scope includes `variables`, `lastOutput`, `node_results`, plus every top-level `state.variables` key spread as a direct name. Expressions also accept `{{path}}` syntax — the wrapper rewrites `{{a.b}}` to `a["b"]` before parse so designers can use Mustache in eval contexts (the syntax everywhere else in the canvas).

### Conditional routing

`if-else`, `while`, and `user-approval` nodes have **two outgoing handles each**:

| Source type | Branches |
|---|---|
| `if-else` | `true` / `false` |
| `while` | `body` / `exit` |
| `user-approval` | `approved` / `rejected` |

The frontend's `BranchingNode` component renders two coloured handles per branching type; each connection's `sourceHandle` is the branch label (`true`, `body`, `approved`, etc.). The serialiser copies `sourceHandle` → `branch` on save. Backend validation rejects edges that leave a conditional source without a `branch` and rejects edges that have a `branch` set but originate from a non-conditional source.

### Events wrapper

Every executor is wrapped by [`events_wrapper`](../src/engine/events_wrapper.py) which:

1. Emits `node_started` before invoking.
2. Calls the executor.
3. Reads `result["node_results"][node.id].output`.
4. Aliases the output under both `state.variables[<sanitized_node_id>]` and `state.variables[<sanitized_node_name>]` (snake_case) so designers can reference `{{place_picker.city}}` in downstream prompts.
5. Emits `node_completed` (or `node_failed` on exception).

This auto-aliasing is why "give the node a Name and reference it as `{{name.field}}`" works without any explicit configuration.

## Durable execution, events, and rate limits (ADR-0033)

Cloud Run runs `--min-instances=0`, so anything that keeps working *after* an HTTP response has been sent — a `BackgroundTasks.add_task()`, a bare `asyncio.create_task()`, an in-process polling loop — is liable to be killed mid-flight by scale-to-zero. This section covers the three subsystems ADR-0033 moved off that model: execution itself, execution events, and rate limiting. All three now route through Postgres and/or a real inbound HTTP request instead of detached in-process work.

### Execution flow

```
POST /executions
     │  creates WorkflowExecution row, status='queued'
     ▼
enqueue_execution_task()          src/execution/cloud_tasks.py
     │  Cloud Tasks: OIDC-authenticated HTTP push
     ▼
POST /internal/claim-and-run      src/api/internal.py
     │  SELECT ... FOR UPDATE SKIP LOCKED  (claims the row; the actual
     │  single-claim guarantee — Cloud Tasks' at-least-once delivery
     │  alone does not provide one)
     │  status: queued → running
     ▼
LangGraphExecutor.run() / .resume()
     │  compiled.ainvoke(state, config={"configurable": {"thread_id": ...}})
     ▼
status: completed / failed / waiting_approval / canceled
```

Because Cloud Tasks delivery is a genuine inbound HTTP request, Cloud Run keeps the instance alive for the full duration of the execution — this is the actual fix for the scale-to-zero risk, not Cloud Tasks' retry/backoff/dead-letter behavior (which is a secondary benefit). `POST /executions/{id}/resume` (approval decisions, in-app and via email) and `POST /api/run/{slug}` (external invoke) enqueue the same kind of task rather than calling `BackgroundTasks.add_task()`.

**Attempt failure and retained state.** `WorkflowExecution.leaseOwner`/`leaseExpiresAt`/`deliveryAttempts` track which worker owns a `running` attempt. `POST /internal/sweep` is triggered by Cloud Scheduler and conditionally fails expired leases and lease-less orphaned attempts; it does **not** automatically replay processing or side effects. The same endpoint can apply opt-in approval expiration and event-retention cleanup. The LangGraph thread/checkpoints remain durable until retention or explicit deletion, but ordinary approval resume accepts only `waiting_approval`; a failed or canceled execution currently requires a separately designed, audited human-recovery mechanism rather than silently restarting.

This replaces the old model, which required Cloud Run's `--no-cpu-throttling` flag to keep an in-process `while True: ... await asyncio.sleep()` sweeper loop ticking between requests — the dominant cost driver before this ADR (see ADR-0033's 2026-07-14 addendum). A real inbound request needs no background CPU allocation, so the flag was removed.

### Events

```
node executor              src/engine/events_wrapper.py
     │  node_started / node_completed / node_failed
     ▼
PostgresEventStore.append()          src/engine/events_pg.py
     │  durable, sequence-numbered row in `execution_events`
     ▼
pg_notify('composer_execution_events', '<execution_id>:<seq>')
     │  src/engine/events_notify.py — LISTEN/NOTIFY, not durable,
     │  8000-byte payload cap; carries only a pointer, never the event body
     ▼
GET /executions/{id}/ws              src/api/events_ws.py
     │  on connect: replay from a client-supplied `after_seq` cursor via
     │  PostgresEventStore, then subscribe live; a NOTIFY wakes the handler
     │  to re-query for anything after its last-seen seq
     ▼
WebSocket client
```

`LISTEN/NOTIFY` is a low-latency wake-up signal only — a notification is lost if no one is listening at emit time, so it never carries the event payload itself. The durable record and the reconnect-cursor/missed-event recovery both come from `PostgresEventStore`; a missed `NOTIFY` is harmless because the next notification or keepalive poll catches the WebSocket handler back up. This replaces the Phase 5b in-process `ExecutionEventBus` (`src/engine/events.py`), which had no cross-instance visibility and lost history for any client not connected at emit time.

### Rate limiting

`src/security/rate_limit_pg.py` implements the same token-bucket algorithm as the original in-memory `RateLimiter`, but state lives in the `rate_limit_buckets` table (`SELECT ... FOR UPDATE` in a short transaction that starts with a 2 s `lock_timeout`; a lock timeout is a deny, never a 500, and consumes no token) instead of a process-local dict. The in-memory version was only ever correct for a single instance; with `--max-instances=3`, multi-instance concurrency is a real, not theoretical, case.

**Full design record:** ADR-0033 in [`decisions.md`](decisions.md); implementation plan: [`docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md`](superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md).

## File storage providers and Google Drive polling

`file-trigger`, `file-write`, and `download-pdf` all sit on the same pluggable `FileStorageProvider` ABC (`src/storage_providers/base.py`) rather than hard-coding a filesystem. Two implementations exist today:

- **`LocalFilesystemProvider`** — polling-based, partial-write-safe (a file must be stable across two consecutive polls before it's claimed). Driven by the `composer watch` CLI ([`src/cli/watch.py`](../src/cli/watch.py)), a long-lived local process that triggers a **production** workflow via the existing `POST /api/run/{slug}` external-invoke endpoint whenever a file lands — no dedicated trigger endpoint. `file-trigger` itself is visual-only (`graph_builder.py`'s `_VISUAL_ONLY_TYPES`), the same pattern as `note`: it's a canvas-visible configuration surface, not a step the execution engine advances through.
- **`GoogleDriveProvider`** — server-side, no local agent required. Per-user OAuth tokens live in `CloudStorageConnection` (mirrors `McpOAuthToken`'s encryption pattern; connect/callback/list routes in `src/api/cloud_storage_oauth.py`, see [`api-reference.md`](api-reference.md)). Its claim mechanism is a Drive `appProperties` marker rather than a move-to-folder — `move_file()` sets the marker first (permanent, never-reprocess guarantee) then, only if `driveProcessedFolderId`/`driveErrorFolderId` are configured on the node, additionally relocates the file there for user-visible feedback. `POST /internal/poll-file-triggers` (`src/api/internal.py`) — OIDC-authenticated, Cloud-Scheduler-triggered, a sibling to ADR-0033's `claim-and-run`/`sweep` — is the poll loop, running on a flat 5-minute cadence for all Drive triggers regardless of the node's own `pollIntervalSeconds` (that field stays meaningful for the local/CLI case only).

`download-pdf` reuses `GoogleDriveProvider` for its own Google Drive destination option, resolving the OAuth token the same server-side way via `get_valid_drive_access_token`.

DOCX/PDF rendering (`src/conversion/`) is pure-Python — `markdown-it-py` + `python-docx` for DOCX, `markdown-it-py` → HTML → `xhtml2pdf` for PDF — deliberately avoiding a pandoc system dependency. PDF export (both `markdown_to_pdf` and `download-pdf`'s direct `html_to_pdf` path) shares a deny-all `link_callback` (`src/conversion/_pdf_security.py`) that blocks all outbound resource resolution during rendering, closing the same class of SSRF risk the `http` node's `src/security/ssrf.py` guard closes for direct HTTP calls (IP-literal/DNS-resolution/cloud-metadata-hostname blocking).

**Full design record:** ADR-0030 in [`decisions.md`](decisions.md) (file storage framework); Google Drive design doc referenced from the CHANGELOG's 2026-07-16 entry.

## Auth model

Three layers stack:

1. **NextAuth (frontend)** — Azure AD or Credentials provider, mints a session JWT.
2. **Composer JWT (backend session)** — HS256, mirrors IE's `DES-004`. Used by all interactive UI calls.
3. **Per-user API keys (backend)** — bcrypt-hashed, stored in `api_keys`. Used by external invokes (`POST /api/run/{slug}`); the published workflow's owner controls who can call it.

Admin role is set on the `User.role` column; admin-only routes use `Depends(ensure_admin)`. Per-execution authz: workflow owners + admins can read/edit; public workflows are world-readable; private workflows return 404 (not 403) to non-owners to avoid leaking existence. Workflow assignment (`WorkflowAssignment`, ADR-0025) layers full read+write access on top of the single-owner field for non-owner users the owner or an admin explicitly shares with.

`validate_production_config()` ([`src/config_validation.py`](../src/config_validation.py)) runs at FastAPI startup and refuses to boot with an unsafe production configuration — e.g. a missing JWT secret or the dev-mode auth fallback left enabled — rather than silently running insecurely.

## Frontend

Next.js 15 App Router, three role-aware audiences:

- **`/designer/*`** — workflow authors. Canvas, templates gallery, settings, publish dialog.
- **`/runs/*`** — end users. Run list, run details, approval dialog, API key management.
- **`/admin/*`** — admins. Users, LLM keys, model catalog, MCP server toggles, deployment settings, all-workflows view.

A single `RoleNav` component reads the user's role and renders the appropriate hierarchical menu. Every panel's edits flow through React Hook Form + Zod for client-side validation; Save calls `PUT /workflows/{id}` with the full envelope.

The OpenAPI schema is generated from FastAPI at `/openapi.json` and consumed by `frontend/lib/api/generated/schema.ts` for end-to-end typing.

## Build with AI (`src/builder/`)

The builder runs a tool-using LLM loop over a draft created from the stored workflow, then persists the result. It does not operate directly on the browser's live React Flow state. Before every builder request, the designer page saves canvas edits that are dirty, already saving, or in an autosave error state; if that save fails, the request does not run. The client mirrors the persisted builder result back onto the canvas. This boundary prevents edits waiting for the autosave debounce from being overwritten by a turn based on an older stored graph.

The workflow-mutation tools in [`builder_tools.py`](../src/builder/builder_tools.py) are `create_node`, `update_node`, `remove_node`, `connect_nodes`, `disconnect_nodes`, and `set_start_inputs`, alongside the planning, credential, diagnosis, and patch-proposal tools. `connect_nodes` is idempotent for an existing `(source, target, branch)` connection. `disconnect_nodes(source, target, branch?)` removes matching edges without deleting either endpoint; omitting `branch` removes every edge between that source and target.

Two kinds of rules apply to what the builder produces:

- **Structural rules gate saves and runs.** `collect_workflow_violations` / `validate_workflow_shape` ([`src/engine/graph_builder.py`](../src/engine/graph_builder.py)) run on every save and before every run. Duplicate edges are not a violation: `dedupe_edges` collapses them (first edge per `(source, target, branch)` wins) in `POST`/`PUT /workflows`, in `build_graph` and on each builder turn ([`normalize.py`](../src/builder/normalize.py)), because rejecting them would make existing workflows unsaveable and unrunnable.
- **Content rules only steer the builder.** `collect_completeness_issues` ([`completeness.py`](../src/builder/completeness.py)) reports unfilled nodes (missing instructions or prompt, all-off guardrails, missing MCP server, model unset or not enabled, unresolved `{{name}}`). It never blocks a save or run.

**Repair loop** ([`builder_agent.py`](../src/builder/builder_agent.py), `run_builder_turn`): after a build turn and `normalize_orchestration`, `_turn_issues` combines structural violations (whole workflow) with content issues (only nodes created or changed this turn). If any exist, they are sent back as an "Automatic check" message and the tool loop runs again, up to `MAX_REPAIR_ROUNDS = 2`. Leftovers are emitted as a "Remaining issues: ..." chat message (first 5).

**Prompt inputs** ([`prompts.py`](../src/builder/prompts.py)): the per-node field guide (`build_field_guide` in [`node_catalog.py`](../src/builder/node_catalog.py), generated from the Pydantic node models, credential fields excluded) and the admin-enabled model list (`enabled_model_catalog` in [`model_policy.py`](../src/builder/model_policy.py), passed in by [`src/api/builder.py`](../src/api/builder.py)).

## Adding a new node type

1. **Schema** — add a `*NodeData` Pydantic class in [`src/engine/workflow.py`](../src/engine/workflow.py) and add the type to the discriminated union.
2. **Executor** — create `src/executors/<your_node>.py` with a class decorated `@register_executor("<your-type>")`. Implement `async def arun(self, state) -> dict[str, Any]` that returns a delta.
3. **Tests** — add `tests/unit/executors/test_<your_node>.py`. Cover happy path, validation failures, and any state interactions.
4. **Frontend panel** — `frontend/components/composer/canvas/node-panels/<your-node>.tsx`. Read/write the schema fields by their canonical aliases. Register in `PANEL_MAP` in `property-panel.tsx`.
5. **Canvas chip** — add an entry to `NODE_VISUALS` in [`frontend/components/composer/canvas/node-visuals.ts`](../frontend/components/composer/canvas/node-visuals.ts).
6. **Run the gates**: `uv run ruff check src tests && uv run ruff format src tests && uv run pyright src tests && uv run pytest`. Frontend: `cd frontend && tsc --noEmit -p tsconfig.json`.
7. **Document** — add a section to [`designer-guide.md`](designer-guide.md) explaining what the node does, when to reach for it, and the key fields.

If your node is conditional (multiple outgoing handles), also extend `CONDITIONAL_SOURCE_TYPES` in [`src/engine/graph_builder.py`](../src/engine/graph_builder.py), add a branch spec to `BRANCH_SPECS` in [`workflow-canvas.tsx`](../frontend/components/composer/canvas/workflow-canvas.tsx), and add the type to `CONDITIONAL_SOURCE_TYPES` in [`workflow-to-rf.ts`](../frontend/lib/workflow-to-rf.ts).
