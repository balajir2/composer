# Deferred backlog — items requiring a design decision

Source: `docs/claude-improvement-backlog.md` (Codex-authored audit, 2026-07-11). During the P0/P1
remediation pass (2026-07-12), every item below was evaluated and deliberately **not**
implemented — each needs a product or architecture decision only the user can make, rather than
a decision an agent should make silently while burning through a punch list. This doc exists so
they can be picked up and closed one at a time, in whatever order makes sense.

Each entry: the problem, why it's a decision (not a bug fix), the specific question(s) to answer
before implementation starts, and a pointer to the full requirements/acceptance-criteria in the
source audit doc.

Status legend: 🔴 not started.

---

## 🔴 P0-5 — Centralize and protect workflow credentials

**Problem:** Jira tokens get encryption/redaction. Vector-DB keys, embedding keys, HTTP
authorization headers, and MCP OAuth client secrets / shared-server headers do not — some are
returned in plain read APIs today (audit specifically calls out public-workflow reads as a
disclosure path).

**Why it's a decision:** A real fix means one canonical secrets model — encrypted `Credential`/
`Connection` records referenced from node JSON, not values embedded in it — applied consistently
across every provider (Jira, vector DB, HTTP, MCP, email). Encrypting one more field ad hoc
without that model gets redone the moment the real model lands, and the existing-workflow
migration path (old plaintext → new reference) needs to be decided up front, not bolted on after.

**Decisions needed:**
1. `Credential` as a new first-class model, or extend the existing per-node encrypted-field
   pattern used for Jira? (Former scales better across providers; latter is less migration.)
2. Migration strategy for existing workflows with inline plaintext credentials — auto-migrate on
   next save, one-time backfill script, or lazy-migrate on read?
3. Scope for v1 — everything in the audit's inventory, or Jira + vector DB + HTTP first, MCP
   headers/OAuth secrets in a follow-up?

**Full spec:** `docs/claude-improvement-backlog.md` §P0-5 (requirements + acceptance criteria).

---

## 🔴 P1-2 — Move workflow execution to durable workers

**Problem:** Executions run as request-bound background tasks / raw `asyncio.create_task`. A
Cloud Run instance kill, deploy, or CPU suspension can silently interrupt a run mid-node; the
stuck-execution sweeper can only mark it `failed`, never finish or resume the actual work.

**Why it's a decision:** The audit itself frames this as requiring an ADR first — Google Cloud
Tasks vs. Pub/Sub vs. a Postgres-backed queue vs. Redis-backed workers, each with different
operational cost, lease/heartbeat semantics, and LangGraph-checkpoint interaction. This is also a
stack-locked change per `CLAUDE.md` (execution model is part of the locked architecture) —
explicit approval required before even prototyping.

**Decisions needed:**
1. Which durable-queue backend — bias toward "smallest reliable option compatible with Cloud Run
   + LangGraph checkpoints" per the audit's own framing (a Postgres-backed queue reuses the
   existing DB and avoids a new managed dependency; Cloud Tasks avoids building queue semantics
   from scratch but adds a GCP-specific coupling).
2. Does this replace `LangGraphExecutor.run()`'s current in-process model entirely, or run
   alongside it for new executions while old-style ones drain out?
3. How does approval-resume (`POST /executions/{id}/resume`, the email-link flow) route through
   the same durable mechanism without a second, parallel resume path to maintain?

**Full spec:** `docs/claude-improvement-backlog.md` §P1-2 — write the ADR first, per the audit's
own "Required discovery" instruction.

---

## 🔴 P1-4 — Replace process-local events and rate limits

**Problem:** The execution event bus (WebSocket fan-out) and the rate-limit token buckets are
both in-process Python state. Correct only for a single backend instance — Cloud Run can and does
autoscale to N instances, at which point a client connected to instance A never sees events
emitted by instance B, and rate limits reset per-instance instead of being global.

**Why it's a decision:** Needs a shared backing store — Redis, Postgres `LISTEN/NOTIFY`, or GCP
Pub/Sub — each a new operational dependency (or a new usage pattern on the existing Postgres) with
different cost/complexity tradeoffs. Directly overlaps with P1-2's queue-backend choice; deciding
these together avoids picking two different shared-infra pieces where one would do.

**Decisions needed:**
1. Backing store choice — and whether it should be the *same* one chosen for P1-2's durable-worker
   queue (e.g., Postgres `LISTEN/NOTIFY` could plausibly serve both).
2. Event history/retention policy — how long a reconnecting client can look back to recover missed
   terminal events.
3. Whether this blocks or can ship independently of P1-2 (they're related but not strictly
   sequential).

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

## 🔴 P3-1 — Evaluate the long-term Python persistence stack

**Problem:** Prisma Client Python's upstream repository was archived (read-only) in April 2025.
Highest structural risk in the current backend stack.

**Why it's a decision:** Explicitly framed by the audit itself as "an ADR and proof-of-concept
task first — not authorization for a repository-wide ORM rewrite." `CLAUDE.md`'s stack table
locks Prisma Python as the chosen ORM; changing it requires the explicit user approval the
governing rules already call for. This decision also gates P1-2 (durable workers need real
transaction/row-locking/lease semantics from whichever ORM wins) and P2-4 (dependency-risk
resolution).

**Decisions needed:**
1. Commission the ADR + POC — compare Prisma Python (accept the archived-repo risk) vs.
   SQLAlchemy 2 + Alembic, specifically for: async support, transactions/row-locking/leases
   (needed by P1-2), migration tooling, and LangGraph-checkpoint integration.
2. If the POC favors migration: incremental (new tables on SQLAlchemy, existing ones stay on
   Prisma until touched) or a scoped big-bang cutover?

**Full spec:** `docs/claude-improvement-backlog.md` §P3-1.

---

## Suggested order

Dependencies between these argue for roughly this sequence, not strict P0→P3 priority order:

1. **P3-1** (persistence ADR) — gates P1-2's transaction/lease design; smallest to start (it's a
   research task, not implementation) and de-risks the rest.
2. **P0-5** (credential model) — gates P2-2; also closes the most concrete security gap
   (plaintext secrets in public workflow reads today).
3. **P1-2** + **P1-4** together — durable workers and shared events/rate-limits both need a
   shared-infra decision; picking one backing store for both avoids two separate new dependencies.
4. **P2-2** (connections UI) — once P0-5's credential model exists to manage.
5. **P2-1** (dry-run mode) — independent of the above; blocked only on the control-flow semantics
   decision.
6. **P2-3** (module splits) — whenever the concurrent-session file contention on
   `workflow.py`/`langgraph_executor.py` settles.
7. **P2-4** (dependency reproducibility) — ongoing hygiene; the frontend platform upgrade
   (Next.js 14 → supported LTS) is the one time-sensitive piece given Next.js 14 is past support.
