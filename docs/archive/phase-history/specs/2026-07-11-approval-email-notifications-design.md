# Approve-via-Email + Waiting-Approval Auto-Expiry — Design

**Status:** Approved (2026-07-11)
**Context:** Today, a `user-approval` node pauses a workflow (via LangGraph's `interrupt()`) and the only way to resolve it is for someone to open Composer, find the run, and click Approve/Reject in-app (`POST /executions/{id}/resume`, `frontend/components/composer/approve-dialog.tsx`). The user wants a lower-friction path: email the approver directly, let them decide with a single click, no login required. Separately, the user flagged that a `waiting_approval` execution currently has no upper time bound — it can sit forever, which is fine for compute (see "Why this doesn't cost compute" below) but not for indefinite Postgres/checkpoint growth.

## What already exists (do not re-derive)

- `src/executors/user_approval.py` — `UserApprovalExecutor.arun()` calls `langgraph.types.interrupt({"node_id": ..., "prompt": ...})`. On first pass this raises `GraphInterrupt`; on resume it returns the fed-back decision (`"approved"`/`"rejected"`).
- `src/engine/langgraph_executor.py` — `LangGraphExecutor.run()`/`.resume()` call `compiled.ainvoke(...)`, then inspect `compiled.aget_state(config).next` to detect a pause (LangGraph 1.1.8 catches `GraphInterrupt` internally inside `ainvoke`; it does not propagate as an exception under normal operation — see the `events_wrapper.py` fix from 2026-07-11, unrelated bug already resolved). On a fresh pause: `_mark_waiting_approval(execution_id, pending_info, existing_vars)` persists `status='waiting_approval'` plus `variables._pending_approval_node`, and emits an `approval_required` WebSocket event. `_extract_pending_info(snapshot)` currently returns exactly whatever dict was passed to `interrupt()` — today just `{node_id, prompt}`.
- `src/storage/checkpointer.py` — `PrismaCheckpointSaver`, a `BaseCheckpointSaver` backed by two Prisma tables (`LangGraphCheckpoint`, `LangGraphCheckpointWrite`), keyed by `thread_id`. This is the **entire** mechanism that makes a paused run resumable — no in-process state survives between `run()` returning and `resume()` being called later; the async task for a `waiting_approval` execution has already exited by the time a human sees the "waiting" status.
- `prisma/schema.prisma` `Approval` model — audit row per decision: `executionId`, `nodeId`, `approverUserId` (currently **required**, `String`, not nullable), `decision`, `note`, `createdAt`.
- `src/api/executions.py` `resume_execution` (`POST /executions/{id}/resume`) — requires `get_current_role` (JWT), checks `role != "admin" and execution.userId != user_id` (owner/admin only), requires `status == 'waiting_approval'`, creates the `Approval` row, flips status to `running`, schedules `executor.resume(execution_id, decision)` as a background task.
- `src/maintenance/execution_sweeper.py` — the existing stuck-execution sweeper. Its own docstring explicitly states: *"`waiting_approval` rows are excluded — they're paused on purpose and may sit for days waiting for a human reviewer."* This design adds a **second, much longer** timeout specifically for `waiting_approval`, distinct from the existing stuck-`running` timeout (which is on the order of minutes and unrelated).
- `src/security/jwt.py` — established pattern for a special-purpose, short-lived JWT type: `create_password_change_token`/`verify_password_change_token`. The new approval-email token follows this exact pattern (new type discriminator, not a new signing mechanism).
- `src/integrations/email/resend.py` — `ResendEmailProvider.send_email(payload)`, already used both by the workflow Email node and by the standalone password-reset email (non-workflow usage precedent already established).
- `src/config.py` — `frontend_url` already exists (added for password reset). No backend-facing public URL setting exists yet — needed here because the emailed link must resolve against the **backend** (it triggers `resume()` server-side), not the frontend SPA.

## Why this doesn't cost compute while waiting (confirmed, not assumed)

Verified by reading the actual flow end-to-end: the moment `LangGraphExecutor.run()` detects a pause, it calls `_mark_waiting_approval(...)`, emits `approval_required`, and **returns** — the async task ends completely. The full resumable state (every variable, every node's results so far, exactly which node to continue at) is serialized into `LangGraphCheckpoint`/`LangGraphCheckpointWrite` Postgres rows via `PrismaCheckpointSaver`. A `waiting_approval` row is exactly as cheap as any other idle database row, whether it waits ten seconds or ten days. `resume()` spins up a **new** task only when called, and LangGraph reconstructs state from Postgres via `thread_id`. The auto-expiry described below exists for **Postgres storage/row-growth hygiene and operational visibility**, not to relieve memory pressure — that pressure doesn't exist in the first place.

## Design

### A. Signed decision tokens

New JWT type in `src/security/jwt.py`, following the `password_change` pattern exactly:

```python
def create_approval_email_token(execution_id: str, node_id: str, decision: Literal["approved", "rejected"]) -> str
def verify_approval_email_token(token: str) -> ApprovalEmailClaims  # {execution_id, node_id, decision}
```

Claims: `execution_id`, `node_id`, `decision`, standard `exp`/`iat`. The **decision is baked into the token's signed payload**, not passed as a mutable query parameter — tampering with the URL invalidates the signature rather than flipping the outcome. TTL: new setting `approval_link_ttl_hours` (default `72`).

Two tokens are generated per pause (one per possible decision), producing two distinct URLs in the same email.

### B. Node config additions

`UserApprovalNodeData` (`src/engine/workflow.py`) gains:

```python
approver_email: str | None = Field(default=None, alias="approverEmail")
approver_cc: str | None = Field(default=None, alias="approverCc")
```

Both support `{{variable}}` substitution (same pattern as every other node's text fields). `UserApprovalExecutor.arun()` folds the substituted values directly into the existing `interrupt()` payload:

```python
interrupt({
    "node_id": self.node.id,
    "prompt": prompt,
    "approver_email": substitute(self.node.data.approver_email or "", state),
    "approver_cc": substitute(self.node.data.approver_cc or "", state),
})
```

This keeps the payload fully self-contained — no separate node/workflow lookup needed downstream to know who to email. Because this is pure substitution (no side effect), it's safe that LangGraph may recompute it on internal replay; only the **send** step (below) is side-effecting and is placed where it can't replay.

### C. Where the email actually gets sent (replay-safety)

LangGraph reruns a node's function from the top on resume; anything with a side effect placed *before* the `interrupt()` call inside `UserApprovalExecutor.arun()` would re-fire on every resume. The send must happen **outside** the executor, in the one-shot "just detected a fresh pause" branches that already exist in `LangGraphExecutor.run()` and `.resume()` (guarding `if snapshot.next:`) — each of those branches executes exactly once per real pause event, never on replay.

New module `src/engine/approval_email.py`:

```python
async def send_approval_email(execution_id: str, node_id: str, prompt: str, approver_email: str, approver_cc: str | None) -> None
```

- No-ops if `approver_email` is empty (email approval is opt-in per node).
- Builds the two signed tokens, builds the two links as `f"{settings.backend_public_url}/approvals/email/{token}"`.
- Renders a minimal HTML email: the prompt text (the same rendered BRD/approval message the in-app dialog shows) plus two clearly-styled Approve/Reject buttons.
- Sends via the existing `ResendEmailProvider`, using `settings.resend_from_email` as sender — reuses the exact integration already wired up for the password-reset and workflow-email-node use cases. Errors are logged, not raised (an email delivery failure must not fail the workflow pause itself — in-app approval remains available regardless).

Call sites: immediately after `_mark_waiting_approval(...)` in both `LangGraphExecutor.run()`'s pause branch and `.resume()`'s chained-pause branch (a second `user-approval` node reached after resuming from a first one) — both already only execute once per genuine pause.

### D. The public resolving endpoint

New router `src/api/approval_email.py`:

```
GET /approvals/email/{token}
```

- **No auth dependency** — the signed token itself is the credential, per the "anyone with the link" decision. This is deliberate: it must work for reviewers with no Composer account.
- Verify token (signature + expiry) → `execution_id`, `node_id`, `decision`.
- Load the execution; if `status != 'waiting_approval'` or `variables._pending_approval_node != node_id` → render a small "This link is no longer valid — the decision may already have been made, or the link has expired" HTML page. **This is also the single-use mechanism**: resuming flips `status` away from `waiting_approval` immediately, so a second click on either link (from the same email, or a stale one from a chained earlier pause) naturally lands here — no separate consumed-flag bookkeeping needed.
- Otherwise: create the `Approval` audit row (`approverEmail=<from token's associated email>`, `approverUserId=None`, `viaEmailLink=True`), flip execution status to `running`, schedule `executor.resume(execution_id, decision)` as a background task (same as the authenticated path) — then return an HTML confirmation page ("Thanks — your decision (Approved/Rejected) has been recorded."), **not** JSON, since this is a direct browser navigation from an email client, not an API call from the SPA.
- Rate-limited the same way other public/anonymous endpoints are (`auth_forgot_password` precedent) to blunt brute-force token guessing, though the token's entropy already makes guessing infeasible.

### E. Data model changes

`prisma/schema.prisma` `Approval` model:

```prisma
model Approval {
  id               String            @id @default(cuid())
  executionId      String            @map("execution_id")
  nodeId           String            @map("node_id")
  approverUserId   String?           @map("approver_user_id")   // now nullable
  approverEmail    String?           @map("approver_email")     // new
  viaEmailLink     Boolean           @default(false) @map("via_email_link")  // new
  decision         ApprovalDecision
  note             String?
  createdAt        DateTime          @default(now()) @map("created_at")

  execution        WorkflowExecution @relation(fields: [executionId], references: [id], onDelete: Cascade)

  @@map("approvals")
  @@index([executionId])
  @@index([approverUserId])
}
```

In-app decisions continue to populate `approverUserId` only (unchanged); email-link decisions populate `approverEmail` + `viaEmailLink=true` and leave `approverUserId` null.

### F. Waiting-approval auto-expiry (the new requirement)

Extends `src/maintenance/execution_sweeper.py` with a second, independent check — **not** a modification of the existing stuck-`running` logic, which stays as-is.

- `_mark_waiting_approval` additionally stamps `variables._pending_approval_since = <ISO timestamp>` at the moment a row enters `waiting_approval` (using `startedAt` would undercount workflows whose earlier nodes took real time before reaching the approval gate).
- New setting `approval_wait_timeout_hours` (default `168` = 7 days).
- New function `sweep_expired_approvals(db, *, timeout_hours, now=None) -> SweepResult` (mirrors `sweep_stuck_executions`'s shape/return type for consistency): queries `status='waiting_approval'` rows whose `variables._pending_approval_since` is older than the cutoff, marks them `failed` with a clear message (`"Execution expired waiting for approval on node {node_id} after {timeout_hours}h. Auto-failed by the approval-timeout sweeper."`), sets `completedAt`. Exceptions per-row are caught and logged, never re-raised (matches the existing sweeper's resilience contract — one bad row must not stop the sweep).
- `_sweeper_loop`/`start_sweeper` run both checks on the same interval tick (or a second independent loop — implementer's call at plan time; sharing one loop is simpler and there's no reason to desync them).

This is deliberately **independent** of the link's own shorter `approval_link_ttl_hours` (72h default): the link can expire while in-app approval still works fine; only this longer outer ceiling ends the execution itself.

### New settings summary (`src/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `backend_public_url` | `""` (must be set in prod) | Base URL for building the emailed approve/reject links — must resolve to the **backend**, not the frontend. |
| `approval_link_ttl_hours` | `72` | How long an individual emailed link stays clickable. |
| `approval_wait_timeout_hours` | `168` | How long a `waiting_approval` execution may sit before the sweeper auto-fails it. |

## Frontend

- `frontend/components/composer/canvas/node-panels/user-approval.tsx` gains two new fields: **Approver email** and **Approver CC (optional)** — same `Input` pattern as the Jira panel's email field, with `{{variable}}` hint text matching other substitution-aware fields.
- No other frontend change is required for the email-approval mechanism itself — the emailed link resolves entirely server-side and renders its own confirmation HTML, independent of the SPA.

## Non-goals (explicitly out of scope for this pass)

- Reminder/follow-up emails if a link isn't clicked before expiry.
- Reply-body parsing (explicitly rejected in favor of signed links — see prior discussion).
- Per-approver identity verification beyond "possession of the emailed link" (no login required, by design).
