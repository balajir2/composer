# Composer — Decisions Log (ADRs)

Append-only log of material engineering decisions. Every decision that isn't self-evident from the code gets an entry here so future-us (and future IE reviewers) can reconstruct the reasoning.

**Format per entry:**
- **Status** — Proposed / Accepted / Superseded-by-ADR-NNNN
- **Context** — the problem and what made it non-obvious
- **Decision** — the choice, stated crisply
- **Consequences** — what this obligates or forecloses
- **Implemented by** — commit hashes / PRs
- **Related** — other ADRs, specs, external docs

Entries are numbered sequentially and never renumbered. Superseding decisions get a new number and link back.

---

## ADR-0001: LangGraph checkpointer — Prisma owns schema, thin custom saver

**Status:** Accepted (2026-04-20)

**Context.** LangGraph Python persists execution state (for interrupts, resume, time-travel) through a `BaseCheckpointSaver` implementation. Two offs-the-shelf options existed — the official `langgraph-checkpoint-postgres` package (psycopg-based, manages its own tables), or writing a custom saver over our Prisma client. Three alternatives considered:

- **A.** Use `langgraph-checkpoint-postgres` as-is; Prisma owns everything else. Cleanest if you don't care about schema unification.
- **B.** Write a full custom Prisma-backed saver. Single ORM.
- **C.** Prisma owns the checkpoint tables in `schema.prisma`, but we implement a thin `BaseCheckpointSaver` subclass that reads/writes them via the Prisma client.

**Decision.** Option **C**. `schema.prisma` declares `LangGraphCheckpoint` and `LangGraphCheckpointWrite` with the shape required by `BaseCheckpointSaver` (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint bytes, metadata bytes, and a writes table keyed by task_id/idx/channel). A small `PrismaCheckpointSaver` class implements the four async methods (`aget_tuple`, `alist`, `aput`, `aput_writes`) using Prisma queries. The LangGraph default serializer (`JsonPlusSerializer`) produces the bytes.

**Consequences.**
- All migrations go through `prisma migrate dev` — single source of truth for schema, aligned with IE's expectation of Prisma-first data modeling.
- We are on the hook for keeping the saver current if LangGraph's checkpointer interface evolves. The interface is small (4 methods); the risk is bounded.
- We do not take a dependency on `langgraph-checkpoint-postgres` and thus don't carry its psycopg transitive.
- If Prisma's BYTEA handling or async driver proves problematic, the escape hatch is to switch to Option A mid-phase (the schema would migrate to LangGraph-managed tables; non-trivial but recoverable).

**Implemented by.** Phase 1 (see [phase-1 spec](../superpowers/specs/2026-04-20-phase-1-execution-engine-design.md)).

**Related.** ADR-0002, ADR-0004.

---

## ADR-0002: Workflow JSON schema — full OAB fidelity in Phase 1

**Status:** Accepted (2026-04-20)

**Context.** Composer stores workflows as JSON (persisted in the `Workflow.nodes` and `Workflow.edges` columns) and needs Pydantic models to validate them. How strict/complete those models are in Phase 1 is a scope question:

- **A.** Minimal: only parse `{nodes: [{id, type}], edges: [{source, target}]}` in Phase 1; extend per phase.
- **B.** Faithful to OAB: port all 18 node-type `data` models as Pydantic classes in Phase 1, even though executors for 16 of them don't exist yet.
- **C.** Hybrid: validate the envelope (id/type/position/edge shape) but leave `data` as `dict[str, Any]` per-type until the owning phase lands.

**Decision.** Option **B**. Phase 1 defines Pydantic models for all 18 node types (`start`, `end`, `note`, `agent`, `mcp`, `if-else`, `while`, `user-approval`, `transform`, `data-transform`, `set-state`, `extract`, `http`, `guardrails`, `vector-db`, `gamma-ai`, `arcade`, `join-chunks`), with the `type` field as a `Literal` discriminator per the OAB TypeScript types at `lib/workflow/types.ts`. A workflow with an unshipped executor validates successfully at CRUD time; attempting to *execute* it raises `NotImplementedError(f"Executor for node type {type!r} lands in Phase N")` when the graph builder tries to instantiate that executor.

**Consequences.**
- Workflow JSON contract is stable from Phase 1. Later phases only add executor code, not schema.
- Costs ~1 additional day in Phase 1 for the 16 data-model classes whose executors don't exist yet. Acceptable per the "no shortcuts, build as a product" preference.
- If OAB's type definitions are ambiguous for a given node type's `data` fields, the implementation reads `lib/workflow/types.ts` (and the executor's usage of those fields) as the authoritative specification.
- OAB node-type discriminator strings (`if-else`, `user-approval`, `gamma-ai`, etc.) are preserved verbatim in JSON; Python class names normalize to snake_case (`IfElseNodeData`, `UserApprovalNodeData`, `GammaAiNodeData`).

**Implemented by.** Phase 1.

**Related.** ADR-0001, ADR-0005.

---

## ADR-0003: Documentation runs in lockstep with development

**Status:** Accepted (2026-04-20)

**Context.** Composer is a ~13-week rebuild where dozens of non-obvious decisions will be made. If docs lag the code, the strategic value of being able to hand Composer to IE as a proposal-ready module evaporates — nobody, including future-us, will reconstruct *why* things are the way they are.

**Decision.** Four living documents, each with a defined scope and update cadence:

| Doc | Path | Cadence |
|---|---|---|
| Master design | [`docs/design/2026-04-20-composer-python-port-design.md`](./2026-04-20-composer-python-port-design.md) | Amended when a decision invalidates a section |
| Phase design specs | `docs/superpowers/specs/YYYY-MM-DD-phase-N-<topic>-design.md` | Written before a phase begins; re-committed mid-phase if scope shifts |
| Decisions log (ADRs) | `docs/design/decisions.md` (this file) | Append-only; new entry ships in the commit that implements it |
| Changelog | [`CHANGELOG.md`](../../CHANGELOG.md) | Updated in the same commit as the change |

**Enforcement rules.**
1. **Doc-with-code:** a commit that changes behavior must include the matching doc update. PRs without matching spec/decision/changelog updates are rejected.
2. **Phase-entry:** before writing code for a phase, its spec file exists and is committed.
3. **Phase-exit:** the "phase N complete" commit updates `CLAUDE.md`'s phase-status table.
4. **ADR anchors:** every ADR has a stable `## ADR-NNNN:` heading so code comments and other specs can link to it.

**Consequences.**
- More commits touching docs. Accepted cost.
- Future maintainers (Balaji in 6 months, IE reviewers, new engineers) can recover intent without reading the full git history.
- Spec drift is structurally prevented — not policed by memory.

**Implemented by.** This file (new); [CHANGELOG.md](../../CHANGELOG.md); [CLAUDE.md](../../CLAUDE.md) references.

**Related.** All future ADRs.

---

## ADR-0004: Local dev Postgres — Neon only

**Status:** Accepted (2026-04-20), supersedes the "Neon primary, Docker fallback" guidance shipped in Phase 0.

**Context.** The master design doc originally specified "Neon for dev, Docker fallback." Phase 0 shipped `docker-compose.yml` and dual-path `.env.example` / README. Revisited at Phase 1 entry.

**Decision.** **Neon only** for local development. No `docker-compose.yml` committed. `.env.example` documents the expected Neon URL format. CI uses a GitHub Actions Postgres service container for integration tests (not a checked-in compose file).

**Consequences.**
- `docker-compose.yml` is removed.
- `.env.example` simplified to Neon-shaped URL only.
- README's "Offline fallback" section removed; replaced with a one-line pointer to Neon's free tier.
- Integration tests (`@pytest.mark.integration`) require a `TEST_DATABASE_URL` env var set to a Neon dev branch or an equivalent Postgres. CI provides this via GitHub Actions `services: postgres`; developers running integration tests locally either point at a scratch Neon branch or skip the marker.
- If Neon is down during development, the user is unblocked because only *integration* tests require Postgres — unit tests (which are the bulk) run with fakes.

**Implemented by.** The docs commit that lands this ADR also removes `docker-compose.yml` and updates `.env.example` + `README.md`.

**Related.** ADR-0001 (checkpointer needs Postgres to exercise).

---

## ADR-0005: Phase 1 API surface + authentication

**Status:** Accepted (2026-04-20)

**Context.** Phase 1's design-doc deliverable is "POST a minimal workflow JSON, execute it, retrieve the execution record via API." Considered three scopes — minimal (3 endpoints), faithful workflow-CRUD subset (8 endpoints), or full OAB parity with stubs. Authentication scope (JWT middleware from day 1 vs deferred to Phase 7) was a secondary question.

Key context that resolved the tradeoff: **no one is using Composer today**. Progressive API surface per phase has no consumer. The full REST contract crystallizes naturally as Phases 2–6 add executors; pinning it in Phase 1 is a guess that will be wrong.

**Decision.**

- **API surface in Phase 1 = 3 endpoints:**
  - `POST /workflows` — create a workflow from JSON; returns created row
  - `POST /executions` — `{workflowId, input}` → creates execution row, schedules background task to run the workflow, returns execution row with `status: "running"`
  - `GET /executions/{execution_id}` — returns the execution row (status, output, error, node results)

  These are the minimum needed to exercise the Phase 1 engine end-to-end. Workflow CRUD (LIST/UPDATE/DELETE), approvals, OAuth, test-connection, upload, and streaming endpoints land in their owning phases (3, 5, 7).

- **Authentication in Phase 1 = no middleware; JWT primitives as library code only.**
  - `src/security/jwt.py` ships now with `create_access_token()`, `verify_access_token()`, typed payload models, and full unit-test coverage.
  - No FastAPI dependency wired into routes. All Phase 1 endpoints are anonymous; `userId` defaults to `"dev"`.
  - Phase 7 wires the primitives into a `Depends()` guard on all routes. At that point the primitives are tested and stable — Phase 7 just assembles them.

**Consequences.**
- Phase 1 stays within its design-doc deliverable; no scope creep from Phases 3/5/7.
- The "no shortcuts" principle is honored by building JWT primitives fully (not stubbing them) and by building all 18 node-type Pydantic models (ADR-0002).
- Anonymous API in Phase 1 is safe because Composer has no external users and runs only on local dev machines during the backend-rebuild window. Phase 7 restores the security posture before any external exposure.
- The 3 Phase 1 endpoints' response shapes are best-effort — they may be revised in Phase 7 as part of API-parity work. This is acceptable because no one depends on them in the interim.

**Implemented by.** Phase 1.

**Related.** ADR-0002, ADR-0003.
