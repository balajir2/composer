# Deferred backlog — items requiring a design decision

Source: `docs/claude-improvement-backlog.md` (Codex-authored audit, 2026-07-11). During the P0/P1
remediation pass (2026-07-12), every item below was evaluated and deliberately **not**
implemented — each needs a product or architecture decision only the user can make, rather than
a decision an agent should make silently while burning through a punch list. This doc exists so
they can be picked up and closed one at a time, in whatever order makes sense.

Each entry: the problem, why it's a decision (not a bug fix), the specific question(s) to answer
before implementation starts, and a pointer to the full requirements/acceptance-criteria in the
source audit doc.

Status legend: 🔴 not started · 🟡 partially resolved · 🟢 resolved (see linked ADR).

---

## 🟡 P0-5 — Centralize and protect workflow credentials (disclosure gap PATCHED, model still deferred)

**Patched 2026-07-13:** the live plaintext-disclosure gap is closed. Vector-DB `apiKey`/
`embeddingApiKey`, HTTP node `httpHeaders` (secret-looking header values), and MCP
`headers`/`oauthConfig.clientSecret` now get the exact same encrypt-at-rest + redact-on-read
treatment Jira's `apiToken` already had (ADR-0028) — extended via generic, reusable helpers in
`src/security/encryption.py` (`encrypt_marked`/`decrypt_marked`/`redact_sensitive_headers`/
`encrypt_sensitive_headers`/`decrypt_sensitive_headers`) rather than a fifth copy-pasted
implementation. Full TDD, all new + existing tests green. No schema migration — same JSON-column
approach Jira already uses.

**What's still open — the original problem, now smaller in scope:**

**Problem:** No single canonical secrets model. Each credential-bearing field (Jira, vector-DB,
HTTP, MCP) has its own inline encrypt/redact pair rather than one shared `Credential`/
`Connection` abstraction — reuse, rotation, ownership, and "which workflows depend on this
credential" visibility (P2-2's prerequisites) still don't exist.

**Why it's still a decision:** A real `Credential`/`Connection` model is a genuine architecture
choice (new table? admin UI? migration path for the now-encrypted-but-still-inline values into
it?) — orthogonal to closing the disclosure gap, which didn't need it.

**Decisions needed (unchanged):**
1. `Credential` as a new first-class model, or keep the current per-field encrypted-inline
   pattern indefinitely (now proven across 4 field types, not just Jira)?
2. Migration strategy if a `Credential` model is built later — the now-encrypted inline values
   would need moving into it, not just re-encrypting.
3. Whether P2-2 (reusable connection management) actually requires this, or whether "encrypted
   inline, redacted on read" is sufficient indefinitely for a single-owner-per-credential model.

**Full spec:** `docs/claude-improvement-backlog.md` §P0-5 (requirements + acceptance criteria).

---

## 🟢 P1-2 — Move workflow execution to durable workers (RESOLVED)

**Resolved 2026-07-15 — see ADR-0033 in `docs/decisions.md`.**

**Outcome:** Implemented as designed. `POST /executions` now creates a `status='queued'` row and
enqueues a Google Cloud Tasks task (`src/execution/cloud_tasks.py`) instead of calling
`BackgroundTasks.add_task()`; Cloud Tasks delivers an OIDC-authenticated HTTP push to
`POST /internal/claim-and-run` (`src/api/internal.py`), which claims the row via
`SELECT ... FOR UPDATE SKIP LOCKED` (the pattern validated in ADR-0031) before running it — the
actual single-claim guarantee, since Cloud Tasks' at-least-once delivery alone doesn't provide one.
Approval resume and external-invoke route through the same mechanism. A lease/heartbeat on
`WorkflowExecution` plus a new `POST /internal/sweep` endpoint (Cloud Scheduler-triggered, not an
in-process loop — a mid-implementation redesign; see ADR-0033's 2026-07-14 addendum) recovers
executions whose worker died mid-run.

**Implemented via:** all 16 tasks of
`docs/superpowers/plans/2026-07-13-durable-execution-cloud-tasks.md`, 35 commits on
`feat/durable-execution-cloud-tasks` (forked from `main` at `9f1b131`). See ADR-0033's
"Implemented by" section for the full task-by-commit breakdown, including the real production bug
Task 15's integration testing caught and the fast-follow items logged along the way.

**Full spec:** `docs/claude-improvement-backlog.md` §P1-2.

---

## 🟢 P1-4 — Replace process-local events and rate limits (RESOLVED)

**Resolved 2026-07-15 — see ADR-0033 in `docs/decisions.md`** (same ADR as P1-2 — the audit's own
framing groups these as one shared-infra decision).

**Outcome:** Implemented as designed, no new infrastructure. Events: every node emission is
appended to a durable, sequence-numbered `execution_events` table (`src/engine/events_pg.py`'s
`PostgresEventStore`), with Postgres `LISTEN/NOTIFY` (`src/engine/events_notify.py`) as a
low-latency, non-durable wake-up pointer only; `GET /executions/{id}/ws`
(`src/api/events_ws.py`) replays from a reconnect cursor then subscribes live, closing the
missed-event/no-cross-instance-visibility gap the old in-process `ExecutionEventBus` had. Rate
limits: `src/security/rate_limit_pg.py` replaces the in-memory token bucket with the same
atomic-conditional-update pattern already used elsewhere (P1-1, P1-3, P1-6), correct across
Cloud Run's multiple instances.

**Implemented via:** same plan and commit range as P1-2 above (Tasks 3-8 cover this half).

**Full spec:** `docs/claude-improvement-backlog.md` §P1-4.

---

## 🔴 P2-1 — Add dry-run execution mode

**Problem:** No way to preview what a workflow *would* do — every run is live. A designer testing
a new Jira/email node has no option but to actually create the ticket / send the email.

**Why it's a decision:** The mechanical part (add a `live`/`dry_run` mode flag, branch each
side-effecting executor to return a structured preview instead of calling out) is straightforward
per-executor work. The unresolved design question is semantic: if a dry-run Jira node doesn't
really create an issue, what does a downstream `if-else` node that branches on "did Jira
succeed" see? A fabricated success? A distinct "not applicable in dry-run" branch outcome? Picking
wrong here means every dry-run of a workflow with conditional logic after a side-effecting node
produces misleading control-flow behavior — worse than not having the feature.

**Decisions needed:**
1. How does a dry-run preview's outcome interact with downstream conditional routing — fabricate
   a plausible success, force a dedicated "skipped, would have run" branch, or halt the dry-run at
   the first side-effecting node?
2. Is dry-run mode a per-execution setting (whole run is live or dry) or can it be scoped to a
   subset of nodes?
3. Does a dry-run execution get its own row/status so it can never be confused with a live one in
   the history list (the audit's own acceptance criterion), and does it count toward execution
   quotas/rate limits?

**Full spec:** `docs/claude-improvement-backlog.md` §P2-1.

---

## 🔴 P2-2 — Add reusable connection management

**Problem:** Every Jira/vector-DB/email/HTTP-auth credential is configured per-node, repeated
across every workflow that needs one. No reuse, no rotation-in-one-place, no visibility into which
workflows depend on a given credential before touching it.

**Why it's a decision:** This is a genuine product feature (a new admin/user "Connections" page)
with real UX surface — how connections are shared/scoped across users, what "health status" means
per provider, how rotation propagates to dependent workflows without silently breaking them. It
also directly depends on P0-5's credential model landing first — a Connections page needs
something to manage.

**Decisions needed:**
1. Sequencing: this should follow P0-5 (credential model), not precede it.
2. Sharing model — per-user only, or shared/team-scoped connections with authorization checks (the
   audit specifically asks for scoped sharing)?
3. Rotation UX — does updating a connection immediately affect all dependent workflows, or does it
   require an explicit "apply to workflows" step per the audit's "show dependent workflows before
   rotation" requirement?

**Full spec:** `docs/claude-improvement-backlog.md` §P2-2.

---

## 🔴 P2-3 — Split oversized modules along domain boundaries

**Problem:** A handful of files (`src/engine/workflow.py`, `src/api/workflows.py`,
`src/tools/providers/jira.py`, two large frontend canvas components) have grown into
multi-concern modules — models, validation, credentials, persistence, and execution logic mixed
together — raising maintenance risk without any functional issue.

**Why it's a decision (for now):** No design ambiguity here — this is a pure refactor with an
explicit "no behavior change" acceptance criterion. It's deferred for a *practical*, current
reason: `src/engine/workflow.py` and related engine files have been under continuous, active
concurrent editing throughout the P0/P1 remediation session (a parallel session has been shipping
file-storage/attachment features touching these exact files). A large structural refactor right
now is close to guaranteed merge-conflict churn for zero functional benefit. Revisit once the
concurrent work settles.

**Decisions needed:**
1. Timing — confirm the concurrent-session work on `workflow.py`/`langgraph_executor.py` has
   landed and stabilized before starting.
2. Module boundary lines — the audit suggests targets but the actual split points (e.g., does
   `workflow.py` become `workflow_models.py` + `workflow_validation.py`, or a `workflow/`
   package?) need a concrete plan before the first PR.

**Full spec:** `docs/claude-improvement-backlog.md` §P2-3.

---

## 🔴 P2-4 — Improve dependency and build reproducibility

**Problem:** No enforced guarantee that local/CI/production resolve the same dependency graph;
several frontend dependencies (Next.js 14, NextAuth beta, `reactflow` 11) are behind supported
versions; LangChain/LangGraph lower bounds aren't pinned as a tested compatibility set.

**Why it's a decision:** This is a policy question (how strict should pinning be, what's the
upgrade cadence) plus a sequencing question — the audit's own recommended order puts this behind
the Prisma persistence decision (P3-1) and the Next.js/React/NextAuth platform upgrade, both of
which are bigger, separately-scoped efforts.

**Decisions needed:**
1. Adopt the audit's recommended upgrade order as-is (lock deps → resolve Prisma risk → Next.js
   14→15 platform upgrade → `reactflow`→`@xyflow/react` migration → LangGraph/LangChain minor
   bump → routine patches), or reprioritize?
2. Frontend platform upgrade (Next.js 14 is past its support window) — separately scoped project
   or folded into this item?

**Full spec:** `docs/claude-improvement-backlog.md` §P2-4 (includes a full version-by-version
table and the audit's recommended upgrade order).

---

## 🟢 P3-1 — Evaluate the long-term Python persistence stack (RESOLVED)

**Resolved 2026-07-13 — see ADR-0031 in `docs/decisions.md`.**

**Outcome:** Stay on Prisma Python, don't migrate now. The archived-repo risk (confirmed via
`gh api`: archived, no commits since 2025-04-10) is real, but a POC
(`scripts/poc_persistence_row_lock.py`) proved Prisma Python's raw-SQL escape hatch
(`query_raw`/`execute_raw`/`db.tx()`) already supports real Postgres row-locking
(`FOR UPDATE SKIP LOCKED`) with correct concurrent-worker semantics — the exact pattern P1-2
needs, and the strongest technical argument for migrating turned out not to be a blocker. A full
migration (14 models, 30 files with DB call sites) isn't justified by risk that hasn't yet broken
anything. ADR-0031 records concrete trigger conditions for revisiting, and an incremental
migration path (new tables on SQLAlchemy first, starting with P1-2's worker-queue tables) if one
of those triggers.

This unblocks **P1-2** (durable workers) to proceed on Prisma directly, using the row-locking
pattern the POC validated, whenever P1-2 itself is picked up.

**Original problem/spec:** `docs/claude-improvement-backlog.md` §P3-1.

---

## Suggested order

Dependencies between these argue for roughly this sequence, not strict P0→P3 priority order:

1. ~~**P3-1** (persistence ADR)~~ — **done**, see ADR-0031. Decision: stay on Prisma; P1-2 can
   proceed directly using the validated row-locking pattern.
2. ~~**P0-5** (disclosure gap)~~ — **patched** 2026-07-13; the concrete security gap (plaintext
   secrets in public workflow/MCP reads) is closed. The `Credential`/`Connection` model decision
   (needed for P2-2's reuse/rotation UX) is still open — see the updated P0-5 entry above.
3. ~~**P1-2** + **P1-4**~~ — **done**, see ADR-0033. Not one shared backend after all, per the
   evidence: Cloud Tasks for the execution queue (purpose-built, solves the confirmed-active
   Cloud-Run-scale-to-zero risk directly), Postgres for events/rate limits (no new infra, reuses
   this session's already-proven patterns). One new service total; implemented on
   `feat/durable-execution-cloud-tasks`, not yet merged to `main`.
4. **P2-2** (connections UI) — needs P0-5's `Credential` model decision made first (still open).
5. **P2-1** (dry-run mode) — independent of the above; blocked only on the control-flow semantics
   decision.
6. **P2-3** (module splits) — whenever the concurrent-session file contention on
   `workflow.py`/`langgraph_executor.py` settles.
7. **P2-4** (dependency reproducibility) — ongoing hygiene; the frontend platform upgrade
   (Next.js 14 → supported LTS) is the one time-sensitive piece given Next.js 14 is past support.
