# SQLAlchemy Migration Phase 6 — Removal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Prisma from Composer: the `prisma`, `asyncpg` and `asyncpg-stubs` packages, the `prisma/` directory (schema plus 20 migrations), every `prisma generate` / `prisma migrate` step in the Dockerfile and the workflows, Node.js from the image build, and the last Prisma-shaped test code. After this phase Alembic alone can build a fresh database, and no existing database (production, Neon branches, dev databases) changes or breaks.

**Architecture:** The Alembic revision chain and every revision id stay as they are. Only `0001_baseline` changes. Today it is empty, because `prisma migrate deploy` created the base schema. After this phase it contains that base schema's DDL: the 20 Prisma `migration.sql` files, copied verbatim in order (decision D2/D3). A database that already has an `alembic_version` row never runs 0001 again, so production (at `0004_langgraph_saver_tables`, or `0005_execution_attempts` once Codex merges) is untouched. A database that only Prisma built (no `alembic_version`, all 20 Prisma migrations recorded in `_prisma_migrations`) is adopted without running any DDL (D4). Before Prisma is removed, golden catalog snapshots of a database built the old way are committed. A schema-diff test then proves that a database built by Alembic alone matches them at revisions 0001 and 0004. Tests that still use Prisma or asyncpg move to the SQLAlchemy sessionmaker or to psycopg. The Prisma-only scripts are ported. Build, CI and deploy then drop Prisma and Node.js.

**Tech Stack:** SQLAlchemy 2.1 async + psycopg 3.3, Alembic 1.20, PostgreSQL 15, FastAPI, pytest + pytest-asyncio, pyright strict, ruff, uv 0.8.15, Docker BuildKit.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md). Relevant sections: §2.3 (dependencies removed at the end), §3.1 rule 5, §3.2 (Alembic baseline, the squashed base after phase 6), the §3.3 phase 6 and phase 7 rows, "Known schema divergence" and "Second divergence", §5.2 (overall done). Previous plan: [phase 5](2026-11-25-sqlalchemy-phase-5-checkpointer-and-notifications.md), whose "After phase 5: what phase 6 removes" section and Outcome section are this plan's input. ADRs: ADR-0035 (SQLAlchemy replaces Prisma) and ADR-0036 (official checkpointer) in `docs/decisions.md`. Parallel work: Codex's telemetry phase 5 ([handoff note](../notes/2026-11-25-codex-handoff-telemetry-phase-5.md); branch `feat/telemetry-phase-5-attempts`, revision `0005_execution_attempts`).

## Global Constraints

All Global Constraints from phases 1–5 apply unchanged:

- Repos are the only SQLAlchemy query code in `src`. Repos are imported as modules (`from src.db.repos import workflows as workflows_repo`), return frozen-dataclass records, and never commit; callers commit. Test helpers under `tests/` may use the ORM models directly, as the phase oracles already do.
- Writes take timestamps from `utcnow()` (`src/db/types.py`). `UTCDateTime` columns accept only timezone-aware datetimes.
- Short-session rule: never hold a SQLAlchemy session across a graph run, a saver call, an LLM call, network I/O, `asyncio.sleep` or a WebSocket wait.
- Fail closed. Never log secrets, connection strings or execution payloads. Log exception class names, ids and counts only.
- CI-parity pyright before every commit: `uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`. Also `uv run ruff check src tests` and `uv run ruff format --check src tests`.
- Commits are authored by Balaji Rajan `<balajirajan@gmail.com>` (verify with `git log -1 --format='%an <%ae>'`) and end with one `Co-Authored-By:` line naming the model that actually did the work, for example `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never touch `D:/GitHub/open-agent-builder` (frozen, read-only), and never write to Codex's worktree `D:/GitHub/composer/.worktrees/telemetry-phase-5-attempts`.

Additionally, for this phase:

- **Policy files change only in the task named for them, and only after Balaji approves the decision listed:**
  - `src/db/migrations/versions/0001_baseline.py`: Task 2 only (D2, D3, D4).
  - `pyproject.toml` and `uv.lock`: Task 6 only (D1, D5).
  - `prisma/**` is deleted in Task 6 only (D6). Until then it stays frozen, and `tests/unit/db/test_prisma_frozen.py` keeps passing.
  - `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/ci.yml`, `deploy-gcp.yml` and `live-provider-smoke.yml`: Task 7 only (D5).
  - `CLAUDE.md`: Task 8 only (D8).
  - `src/db/migrations/versions/0002`–`0004`: never edited. Only their docstrings mention Prisma, and they are historical.

  If a task hits an error it can only fix by editing a policy file outside its list, it stops and reports BLOCKED.
- **Production's database is not changed by this phase.** Production is already past 0001, so the first phase 6 deploy's `alembic upgrade head` is a no-op there (or applies only Codex's 0005, if that is pending). `_prisma_migrations` and the two old checkpoint tables stay until phase 7.
- **Revision ids never change.** `0001_baseline`, `0002_idempotency_per_user`, `0003_assignment_user_fks` and `0004_langgraph_saver_tables` keep their ids and `down_revision`s. `uv run alembic heads` must print exactly one head after every task.
- **Do not edit files on Codex's list** (see "Coordination with Codex") until Codex's PR has merged and this branch has been rebased onto it.
- **CI minutes are scarce:** about 30 remain this cycle, and one PR plus its deploy costs about 45. Every task is verified locally against a throwaway Postgres 15 container. The phase ships as ONE PR, opened only after the CI cycle resets, so CI runs once.
- Local database for all tasks: `docker run -d --name composer-p6-pg -e POSTGRES_PASSWORD=composer -e POSTGRES_USER=composer -e POSTGRES_DB=composer_test -p 55433:5432 postgres:15`, with `TEST_DATABASE_URL=postgresql://composer:composer@localhost:55433/composer_test`. The container's user is a superuser, as in CI, so the scratch-database tests can `CREATE DATABASE`.
- Tool paths: this machine's controller shell has no `uv`/`python` on `PATH`. Use the absolute `uv` path and `.venv/Scripts/python` (see the `project_tool_paths` memory). Commands below are written as `uv run …` for readability.

## Decisions for Balaji (each with a recommendation)

**Confirmed by Balaji on 2026-11-26:** all recommendations D1–D10 accepted (incl. the pyproject/uv.lock, Dockerfile, .dockerignore/.gitignore, ci.yml / deploy-gcp.yml / live-provider-smoke.yml, `prisma/` deletion and CLAUDE.md edits exactly as listed). D9: Codex's `0005_execution_attempts` merges first; phase 6 rebases after it.

**Status: confirmed (see above).** Each task names the decision it needs; the table below is kept as the record of what was decided.

| # | Topic | Recommendation |
|---|---|---|
| D1 | **Dependency removal.** Needs approval because it edits `pyproject.toml` and `uv.lock`. Lines that change in `pyproject.toml`: lines 18–19 (`# Database (Prisma Python)`, `"prisma>=0.15.0",`); lines 20–21 (the comment "SQLAlchemy replaces Prisma area by area; see …", reworded to "Database: SQLAlchemy 2 async + Alembic on psycopg 3 (ADR-0035)"); lines 29–30 (`# Postgres LISTEN/NOTIFY (P1-4)`, `"asyncpg>=0.32.0",`); line 85 (`"asyncpg-stubs>=0.31.0",` in the `dev` extra). In `uv.lock`, the `[[package]]` entries for `prisma` 0.15.0, `asyncpg` 0.32.0 and `asyncpg-stubs` 0.32.0, plus two transitive packages that only Prisma needs: `jinja2` and `tomlkit`. (`click` stays for `uvicorn`, `nodeenv` for `pyright`, `markupsafe` for `mako`/Alembic, and `python-dotenv` for its other dependents.) The `composer` package's dependency and metadata lists in `uv.lock` lose the same three names. | **Approve.** Run `uv remove prisma asyncpg` and `uv remove --optional dev asyncpg-stubs`, then confirm with `git diff --stat uv.lock` and `uv tree --frozen` that only those five packages left and no other version moved (Task 6 Step 3). `src` has imported neither package since phase 5 (`tests/unit/db/test_prisma_import_boundary.py` enforces this). |
| D2 | **The "squashed Alembic base" and existing databases.** Production's `alembic_version` is `0004_langgraph_saver_tables`. Neon branches and dev databases made between 2026-11-20 and 2026-11-25 can be at 0001, 0002 or 0003. Codex's `0005_execution_attempts` has `down_revision = "0004_langgraph_saver_tables"`. Options: **(A)** keep the chain and every id, and give `0001_baseline` the real base DDL. Fresh databases run 0001 to 0004 (to 0005) in order. Existing databases never re-run 0001, and Codex's 0005 composes in any merge order. **(B)** True squash: one revision with id `0004_langgraph_saver_tables` that creates everything, with 0001 to 0003 deleted. Any database at 0001, 0002 or 0003 then fails with "Can't locate revision". The up/down tests for 0002 and 0003 are lost, and 0004's downgrade would mean dropping the whole schema. **(C)** A new base id (for example `0000_squashed_base`) plus a manual `alembic stamp` on every existing database. That is a hand-run production operation, and every Neon branch needs it too. | **(A).** It meets the spec's intent (§3.2: a revision that creates the full current schema replaces the Prisma migrations for fresh databases), but the base is 0001 plus the existing chain rather than one collapsed file. That difference is recorded in the spec and the ADR (Task 8). With (A) no database needs a stamp, nothing in production changes, and the change is independent of Codex's merge order: Codex never touches `0001_baseline.py`, and 0005's `down_revision` stays valid. |
| D3 | **Where 0001's DDL comes from.** Options: (a) the 20 Prisma `migration.sql` files, copied verbatim and run in order. This is byte-for-byte what `prisma migrate deploy` ran on every existing database. (b) A `pg_dump --schema-only` of a database built the old way, cleaned by hand into one `CREATE` per table. This is tidier, but it means hand edits (`SET` lines, ownership, ordering), and every edit is a chance for a difference. (c) DDL generated from the SQLAlchemy models. The divergence notes forbid generating from `schema.prisma`, and the models are the current head, not 0001's state. Inlining the SQL in the revision file or keeping it in sibling `.sql` files are both possible; revisions should be frozen and self-contained. | **(a), inlined in `0001_baseline.py`** as a tuple of `(migration_name, sql)` pairs (about 400 lines of SQL). There is no import from `src`, so the revision stays frozen even if app code moves. The statements run one at a time through `exec_driver_sql`. They contain no `$$`, `%` or `::` (checked on 2026-11-26), and all of them are transactional, so they fit `env.py`'s single transaction. Proof of equivalence: the golden catalog snapshots (Task 1) taken from a database built by Prisma itself, and while `prisma/` still exists, a byte-equality test against the files (Task 2), which is then frozen by a fingerprint test (`tests/unit/db/test_baseline_frozen.py`, which replaces `test_prisma_frozen.py`). |
| D4 | **A database that only Prisma built** (no `alembic_version` row): an old Neon branch, an old dev database, or a disaster-recovery restore from before 2026-11-20. Running 0001's DDL there would fail with "relation already exists". | **Adopt, or refuse loudly.** 0001's `upgrade()` checks the public schema first. (1) No tables except `alembic_version`: run the DDL. (2) `_prisma_migrations` exists and lists exactly the 20 known migrations, each finished and not rolled back: run nothing. This is the same as today's empty 0001, so Alembic simply records it. (3) Anything else is refused with a `RuntimeError` naming the missing or unknown Prisma migrations, or "tables exist but no Prisma history": a partial Prisma history, unknown migrations, or tables without `_prisma_migrations`. The message tells the operator to finish that database from a pre-phase-6 checkout (`git checkout 58d2829 && uv run prisma migrate deploy`) or to restore it. Checksums are not compared, because Windows checkouts produced CRLF checksums on some dev databases. `downgrade()` of 0001 always raises: it would drop the whole schema. |
| D5 | **Build, CI and deploy edits.** Needs approval because these are policy files. Exact changes (Task 7): **`Dockerfile`**: header comment lines 1–16; builder `ENV` lines 30, 33–36 (`PRISMA_HIDE_UPDATE_MESSAGE`, `PRISMA_BINARY_CACHE_DIR` and their comment); the `RUN apt-get … nodesource … nodejs` block, lines 38–44; lines 50–53 (comment and `COPY prisma/schema.prisma prisma/`); lines 57–60 (`RUN uv run prisma generate` and its comment); runtime `ENV` lines 71–72 (`PRISMA_*`); and the comments at lines 91–94 and 97–98 that mention Prisma. The `SECURITY_REFRESH` block (lines 80–89) stays exactly as it is. **`.dockerignore`**: lines 57–59 deleted. **`.gitignore`**: lines 58–60 deleted. **`ci.yml`**: steps "Generate Prisma client" and "Apply migrations" deleted, at lines 64–68 (backend job) and 342–346 (E2E job); "Apply Alembic migrations" is renamed "Apply migrations (Alembic)". **`live-provider-smoke.yml`**: lines 80–84 deleted and the step renamed the same way. **`deploy-gcp.yml`**: line 100's comment (it says "single-stage" and lists "apt+node install … prisma generate"); the comment block at lines 129–141, reworded for Alembic; lines 162–163 (`uv run prisma migrate deploy`, `uv run prisma migrate status`) deleted; the guard comment at lines 164–170 (it says "Runs BEFORE the Alembic upgrade …"; this stays and is reworded only where it mentions Prisma). **`pyproject.toml`** tool sections: `"prisma/generated"` removed from ruff `extend-exclude` (line 104), pyright `exclude` (line 133) and coverage `omit` (line 177). | **Approve.** The deploy keeps its order: guard, then `alembic upgrade head`, then `alembic current`, then `gcloud run deploy`. `tests/unit/test_deploy_workflow.py` is updated to pin it, and the new `tests/unit/test_build_files.py` keeps Prisma and Node.js out of the build files. **Image size:** the runtime stage never had Node.js (it copies only `/app`), so the runtime image shrinks only by the Prisma engine cache, the generated client, `prisma`, `asyncpg`, `jinja2` and `tomlkit`. Expect tens of MB, not hundreds. The builder stage loses Node.js and the apt layer, which makes builds faster and drops a Node.js CVE surface from build caches. Both are measured (Task 1 Step 5, Task 7 Step 6). |
| D6 | **Delete `prisma/` and the tests pinned to it.** `prisma/schema.prisma`, `prisma/migrations/` (20 directories plus `migration_lock.toml`), `tests/unit/db/test_prisma_frozen.py` and `tests/unit/test_prisma_execution_schema.py`. | **Approve.** Their guarantees move: the DDL is in 0001 (fingerprint-frozen), the `running_since`/`failure_code` columns are covered by the golden catalogs and the parity test, and the history stays in git (last present at the phase 6 merge's parent). |
| D7 | **Scripts that still use Prisma.** `scripts/seed_templates.py` (documented in `docs/operations/production-deployment.md` §Phase 3 as the template seeder); `frontend/scripts/promote_admin.py` (run by the Playwright `frontend/e2e/admin.spec.ts`, so **E2E breaks** if it is not ported); `scripts/poc_persistence_row_lock.py` (the ADR-0031 Prisma row-lock proof of concept). | **Port the first two** to `one_shot_session` + repos. `seed_templates` gets an idempotency integration test. `promote_admin` is exercised by E2E and gets a small integration test. **Delete the POC**: it tests a Prisma capability that no longer exists, and SKIP LOCKED is now covered by the claim race tests. ADR-0031 gets a one-line note saying the script was removed in phase 6 and lives in git history. |
| D8 | **`CLAUDE.md` edits.** Stack table rows "ORM" (line 104) and "Postgres driver"; the Development commands block (lines 129–139: `prisma generate`, the frozen Prisma lines, "after `prisma migrate deploy` while both exist"); §4 gotchas 1–2 (Prisma-only). | **Approve:** ORM becomes "SQLAlchemy 2.x (async) + Alembic. Alembic is the only migration tool; `0001_baseline` creates the base schema on an empty database." The driver row becomes "psycopg 3". Dev commands lose every Prisma line. Gotchas 1–2 are replaced by one line: "`alembic downgrade base` is refused: 0001 is the base schema." The phase-status table rows that mention Prisma historically (lines 72, 73, 87) stay. |
| D9 | **Order relative to Codex's telemetry phase 5** (in flight: `0005_execution_attempts`, attempt history in the execution repos, sweepers, approvals, executor). | **Codex merges first; phase 6 rebases onto it.** Phase 6 needs CI only once, after the cycle resets, and Codex's PR is further along. D2(A) makes the Alembic side order-independent anyway. The remaining overlap is comment-only cleanups in four files Codex edits (see Coordination), which Task 5 Step 6 does only after the rebase. If phase 6 has to go first, Task 5 Step 6 is skipped and becomes a follow-up, and nothing else changes. |
| D10 | **Prisma-only query parameters in `DATABASE_URL`.** Production's secret may still carry Prisma parameters (`schema=public`, `connection_limit`, `pgbouncer`), which `src/db/url.py` strips. | **Keep the stripping** (and its tests) in this phase. Cleaning the secret is an ops change with its own rollback. Follow-up: drop the parameters from the secret, then remove the stripping in a later PR. |

---

## Every remaining Prisma / asyncpg use (main at `58d2829`)

Found with `grep -rnE "prisma|asyncpg" --include=*.py src tests scripts frontend/scripts`, plus `grep -rn "state\.db\b"` (excluding `db_sessionmaker`), the build and workflow files, and the docs.

**`src`: no imports (enforced since phase 5).** Comments and docstrings only:

| File:line | What | Task |
|---|---|---|
| `src/db/migrations/versions/0001_baseline.py` (whole file) | Empty baseline ("the schema Prisma's 20 migrations created") | 2 |
| `src/engine/context.py:1` | Docstring "Prisma client available to executors" (stale) | 5 |
| `src/api/test_cleanup.py:66,69` | Docstring cites `schema.prisma`. Flows into OpenAPI, so `frontend/lib/api/generated/schema.ts:1116,1119` is regenerated | 5 |
| `src/migration/transformers.py:1,37` | "Composer Prisma dict", "Prisma @id accepts any unique string". **The migration CLI has no Prisma runtime dependency** (`writer.py`, `runner.py` and `reconcile.py` use `one_shot_session` + repos since phase 2/3) | 5 |
| `src/db/*` (`engine.py:52,67`, `ids.py:3`, `types.py:23,59`, `url.py:1,5,23,50`, `models/base.py`, `models/enums.py:1`, `models/workflows.py:66`, `schema_filters.py:7`, repos `api_keys.py:75`, `executions.py:108`, `users.py:45`, `workflows.py:115`, `workflow_assignments.py:44`) | Historical "as Prisma did" notes that explain why a behaviour exists | **Kept** (they still help; see the spec, "comments that explain Prisma history where they no longer help") |
| `src/checkpoints/legacy.py` | Legacy saver docstring | Kept until phase 7 deletes the file |
| `src/api/executions.py:430`, `src/api/internal.py:241,573` | Prisma history comments | 5 Step 6, **only after Codex merges** (Codex edits these files) |

**`tests`:**

| File | Use | Task |
|---|---|---|
| `tests/conftest.py:69-85` | `NEEDS_LEGACY_PRISMA` shim (`from prisma import Prisma`, `app.state.db`) | 3 (shim removed after the 9 modules move) |
| `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_external_invoke.py`, `test_security_hardening.py`, `test_standalone_auth_lifecycle.py`, `test_workflow_assignments.py` | `NEEDS_LEGACY_PRISMA`; `db.approval.find_many`, `db.user.delete`, `db.workflow.delete`, `db.workflowexecution.*` | 3 |
| `tests/integration/test_durable_execution.py` (798 lines, 74 uses) | `from prisma import Base64, Json`; seeding, `db.tx()` + `update_many`, old-table checkpoint seeding | 4 |
| `tests/integration/test_migration.py` | `from prisma import Json`; `find_unique`, `create`, `count` | 4 |
| `tests/integration/test_mcp_highspot.py` | `from prisma import Json`; `db.mcpoauthtoken.create`, `db.mcpserver.update` | 4 |
| `tests/_rate_limit_reset.py`, `tests/_checkpoint_reset.py` | `import asyncpg` (bare connections) | 5 |
| `tests/integration/test_embedded_auth.py:70-71`, `tests/unit/api/test_admin_llm_keys.py:33-36`, `test_admin_llm_models_routes.py:~68-71`, `test_admin_tools.py:~57-60`, `test_events_ws.py:119`, `test_executions.py:163-169`, `test_executions_list.py:~59-62`, `test_executions_size_limit.py:~58-68`, `test_internal_fail_closed.py:35`, `test_internal_poll_file_triggers.py:66-67`, `test_llm_models_live.py:~30-33`, `test_llm_models_live_fetch.py:239`, `test_run_external.py:137`, `test_workflows.py:39`, `test_workflows_confluence_tokens.py:~81-84`, `test_workflows_crud.py:58-59,115,239-240,285-286,407,471-472,579,627,827`, `test_workflows_http_headers.py:~85-88`, `test_workflows_jira_tokens.py:~81-84`, `test_workflows_vector_db_keys.py:~84-87`, `tests/unit/security/test_hardening.py:37,61,125`, `tests/unit/api/test_mcp_servers.py:398` | Prisma-shaped mocks: `app.state.db = MagicMock()` / `db.user.find_unique = AsyncMock(...)` / `db.workflow.find_unique`. **Dead code:** no `src` code reads `app.state.db` since phase 5 | 5 |
| `tests/unit/db/test_prisma_frozen.py`, `tests/unit/test_prisma_execution_schema.py` | Read `prisma/` | 6 (deleted, D6) |
| `tests/unit/db/test_prisma_import_boundary.py` | Scans `src` only; packages still installed | 6 (scans `src`, `tests`, `scripts`, `frontend/scripts`; packages absent; no shim; no `app.state.db` writes) |
| `tests/unit/test_deploy_workflow.py:35` | Asserts the guard comes after `uv run prisma migrate deploy` | 7 |
| `tests/unit/engine/test_langgraph_executor.py:3,95,102,152`, `tests/unit/api/test_internal.py:231`, `tests/integration/sqla_phase4a/conftest.py:925`, `tests/unit/db/test_models_metadata.py` | Prisma history in comments and docstrings. **Codex edits all four** | 5 Step 6, after Codex merges (`test_models_metadata.py` test names stay: they describe the database's Prisma-made names) |
| `tests/integration/sqla_phase*/**` | Oracle docstrings mentioning Prisma/asyncpg history | Kept (oracles are not edited) |

**`scripts` and `frontend/scripts`:** `scripts/seed_templates.py:36,2999-3045` (Prisma `Json`, `find_unique`, `create`, `update`); `frontend/scripts/promote_admin.py` (Prisma `user.find_unique` / `update`); `scripts/poc_persistence_row_lock.py` (Prisma POC). All in Task 5 (D7).

**Build, CI, deploy:** `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/ci.yml`, `deploy-gcp.yml`, `live-provider-smoke.yml` (`telemetry-staging.yml` has none). See D5. Task 7.

**Docs mentioning Prisma** (Task 8): `README.md` (158, 159, 193, 217, 229, 297, 304; the Node.js 20+ prerequisite on line 144 stays but now says "for the frontend"), `CLAUDE.md` (D8), `docs/architecture.md` (30, 129, 135, 137, 145, 147), `docs/getting-started.md` (74–88), `docs/operations.md:15`, `docs/operations/production-deployment.md` (45–67, 184–190, 320–324), `gcp-cloud-run-setup.md` (91, 95, 253), `postgres-setup.md` (5, 52–77, 194), `disaster-recovery.md` (136–137), `incident-response.md` (158, 170), `scaling.md:74`, `docs/overview.md` (97, 104), `docs/saas/overview.md:60`, `docs/saas/privacy.md` (15, 22), `docs/telemetry/signal-catalog.md:369`, `alembic.ini:1`, `docs/decisions.md` (new ADR-0037; a note on ADR-0031 and ADR-0035), the spec, `CHANGELOG.md`. Historical documents (`docs/archive/**`, `docs/design/**`, older plans and specs, audit lists, backlogs) are **not** edited.

## Coordination with Codex (telemetry phase 5)

Codex's branch `feat/telemetry-phase-5-attempts` (read-only check on 2026-11-26: 8 commits on merge base `9d49dab`, revision renamed to `0005_execution_attempts` with `down_revision = "0004_langgraph_saver_tables"`, no new `prisma`/`asyncpg`/`app.state.db` uses) edits: `src/db/migrations/versions/0005_execution_attempts.py`, `src/db/models/{__init__,execution_attempts,executions}.py`, `src/db/repos/{execution_attempts,executions}.py`, `src/api/{approval_email,executions,internal}.py`, `src/engine/langgraph_executor.py`, sweepers, `tests/integration/db/test_alembic_0005_execution_attempts.py`, `tests/integration/db/repos/test_execution_attempt_*.py`, `tests/integration/execution_attempts/`, `tests/integration/sqla_phase4a/conftest.py`, `tests/integration/sqla_phase4b/{test_approval_email,test_resume_route}.py`, `tests/unit/api/{test_approval_email,test_executions_resume,test_internal}.py`, `tests/unit/db/test_models_metadata.py`, `tests/unit/engine/test_langgraph_executor.py`, `CHANGELOG.md` and its own docs.

**Sequencing (D9): Codex merges first.** Phase 6 Tasks 1–4 and 6–7 touch none of Codex's files and can be built at any time. Before opening the PR, this branch rebases onto main with Codex's merge. Then Task 5 Step 6 (comment cleanups in Codex's files), the head-parity run and the done greps run on the rebased tree. **Alembic:** with D2(A), 0005's `down_revision` stays valid in either order. A fresh database runs 0001 (real DDL) → 0002 → 0003 → 0004 → 0005. The golden tests pin revisions 0001 and 0004 explicitly, so 0005 does not move them, and the existing head parity test (`tests/integration/db/test_schema_parity.py`) covers 0005's models.

**Codex must avoid during phase 6** (until the phase 6 PR merges): `src/db/migrations/versions/0001_baseline.py`; `pyproject.toml`, `uv.lock`; `prisma/**`; `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/**`; `tests/conftest.py`, `tests/integration/conftest.py`, `tests/regression/conftest.py`, `tests/_rate_limit_reset.py`, `tests/_checkpoint_reset.py`; the 9 `NEEDS_LEGACY_PRISMA` modules (listed above); new files `tests/integration/_rows.py`, `tests/integration/db/schema_catalog.py`, `tests/integration/db/test_baseline_schema.py`, `tests/fixtures/schema/**`, `tests/unit/db/test_baseline_frozen.py`, `tests/unit/test_build_files.py`, `tests/integration/test_seed_templates.py`, `tests/integration/test_promote_admin.py`; `tests/unit/db/test_prisma_import_boundary.py`, `tests/unit/db/test_prisma_frozen.py`, `tests/unit/test_prisma_execution_schema.py`, `tests/unit/test_deploy_workflow.py`; the unit test files with `app.state.db` mocks (Task 5 list); `scripts/**`, `frontend/scripts/**`; `CLAUDE.md`. Codex must also **not add** any `prisma`/`asyncpg` import, `app.state.db` assignment or `NEEDS_LEGACY_PRISMA` marker, and must not rely on `_prisma_migrations` existing or on `prisma migrate deploy` in a test. After Task 6, the boundary test fails on any of these.

**Phase 6 avoids** every file on Codex's list above until the rebase. Task 1 Step 8 appends a short "Phase 6" section to the Codex handoff note with these two lists.

## File structure

| Path | Responsibility | Task |
|---|---|---|
| `tests/integration/db/schema_catalog.py` | `snapshot(conninfo, *, exclude=...) -> dict[str, Any]`: a normalized catalog of the public schema; `python -m` entry point writes JSON | 1 |
| `tests/fixtures/schema/golden_0001_baseline.json`, `golden_0004_langgraph_saver_tables.json` | Catalogs of a database built the old way (Prisma, then Alembic to 0004) | 1 |
| `src/db/migrations/versions/0001_baseline.py` | Base DDL (20 Prisma migrations verbatim), adoption check, refused downgrade | 2 |
| `tests/integration/db/test_baseline_schema.py` | Fresh database vs goldens; adoption; refusals; head parity on a scratch database | 2 |
| `tests/unit/db/test_baseline_frozen.py` | Names and fingerprint of 0001's DDL; byte-equality with `prisma/` while it exists | 2 (equality test removed in 6) |
| `tests/integration/_rows.py` | `Rows`: ORM-based seed/inspect helper for integration tests | 3 |
| `tests/integration/conftest.py` | `rows` fixture | 3 |
| `tests/conftest.py` | Shim removed | 3 |
| 9 integration modules | Prisma → `Rows` | 3, 4 |
| `tests/_rate_limit_reset.py`, `tests/_checkpoint_reset.py` | psycopg | 5 |
| ~25 unit test files | Dead `app.state.db` mocks removed | 5 |
| `scripts/seed_templates.py`, `frontend/scripts/promote_admin.py`; `scripts/poc_persistence_row_lock.py` (deleted) | Ported / removed | 5 |
| `tests/integration/test_seed_templates.py`, `tests/integration/test_promote_admin.py` | Script tests | 5 |
| `src/engine/context.py`, `src/api/test_cleanup.py`, `src/migration/transformers.py`, `frontend/lib/api/generated/schema.ts` | Stale Prisma wording | 5 |
| `pyproject.toml`, `uv.lock`, `prisma/` (deleted), `tests/unit/db/test_prisma_frozen.py` and `tests/unit/test_prisma_execution_schema.py` (deleted), `tests/unit/db/test_prisma_import_boundary.py` | Packages and schema out; boundary widened | 6 |
| `Dockerfile`, `.dockerignore`, `.gitignore`, `.github/workflows/{ci,deploy-gcp,live-provider-smoke}.yml`, `tests/unit/test_deploy_workflow.py`, `tests/unit/test_build_files.py` | Build and pipeline | 7 |
| Docs (list above), `CLAUDE.md`, `docs/decisions.md` ADR-0037, spec, CHANGELOG, this plan's Outcome | Record | 8 |

---

### Task 1: Verification baseline and golden schema (Prisma still present)

Nothing in `src` changes. This task records what "unchanged" means while Prisma can still build a database: test counts, image sizes, and the catalog of a database built the old way.

**Files:**
- Create: `tests/integration/db/schema_catalog.py`
- Create: `tests/fixtures/schema/golden_0001_baseline.json`, `tests/fixtures/schema/golden_0004_langgraph_saver_tables.json`
- Modify: `docs/superpowers/notes/2026-11-25-codex-handoff-telemetry-phase-5.md` (append the "Phase 6" section)

**Interfaces:**
- Produces: `snapshot(conninfo: str, *, exclude: frozenset[str] = IGNORED) -> dict[str, Any]` and `IGNORED = frozenset({"alembic_version", "_prisma_migrations"})` in `tests.integration.db.schema_catalog`. Keys of the result: `"tables"` (sorted names), `"columns"` (table → ordered list of `[name, type, not_null, default]`), `"constraints"` (sorted `[table, name, contype, definition]`), `"indexes"` (sorted `[table, name, indexdef]`), `"enums"` (name → ordered labels), `"sequences"`, `"functions"`, `"rows"` (`{"checkpoint_migrations": [0..9]}` when that table exists). Golden files are this dict, as `json.dumps(…, indent=2, sort_keys=True)` plus a trailing newline.

- [ ] **Step 1: Start the throwaway database and build the test database the old way**

```bash
docker run -d --name composer-p6-pg -e POSTGRES_PASSWORD=composer -e POSTGRES_USER=composer \
  -e POSTGRES_DB=composer_test -p 55433:5432 postgres:15
export TEST_DATABASE_URL=postgresql://composer:composer@localhost:55433/composer_test
DATABASE_URL=$TEST_DATABASE_URL uv run prisma generate
DATABASE_URL=$TEST_DATABASE_URL uv run prisma migrate deploy
DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head
DATABASE_URL=$TEST_DATABASE_URL uv run alembic heads   # exactly one head
```

- [ ] **Step 2: Run everything CI runs, locally, on today's code, and record the counts**

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests
uv run pytest -m "not integration"
uv run pytest -m "integration and not live"
```

Expected (phase 5 Outcome numbers, give or take PR #55): unit about 2323 passed; integration about 869 passed and 1 skipped (Jira). Write the exact counts into this plan's Outcome section (Task 8) as "before". If anything fails on clean main, stop and report: phase 6 starts from green.

- [ ] **Step 3: Write the catalog snapshot module**

```python
# tests/integration/db/schema_catalog.py
"""A normalized catalog of the public schema, for comparing two databases' schemas.

Used to prove that a database built by Alembic alone matches one built the old way (Prisma's
20 migrations, then Alembic). Captures what pg_dump would show that matters to the app:
tables, columns in order (type, nullability, default), constraints, indexes, enum labels,
sequences, functions, and the rows of checkpoint_migrations. Ownership and privileges are
left out (they depend on the connecting role).

    uv run python -m tests.integration.db.schema_catalog <postgres url> <out.json>
"""

import json
import sys
from pathlib import Path
from typing import Any

import psycopg

IGNORED = frozenset({"alembic_version", "_prisma_migrations"})

_TABLES = """
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') ORDER BY 1
"""
_COLUMNS = """
SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
       pg_get_expr(d.adbin, d.adrelid)
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""
_CONSTRAINTS = """
SELECT cl.relname, co.conname, co.contype::text, pg_get_constraintdef(co.oid)
FROM pg_constraint co JOIN pg_class cl ON cl.oid = co.conrelid
WHERE co.connamespace = 'public'::regnamespace ORDER BY 1, 2
"""
_INDEXES = "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2"
_ENUMS = """
SELECT t.typname, array_agg(e.enumlabel::text ORDER BY e.enumsortorder)
FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
WHERE t.typnamespace = 'public'::regnamespace GROUP BY t.typname ORDER BY 1
"""
_SEQUENCES = "SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public' ORDER BY 1"
_FUNCTIONS = "SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY 1"


def snapshot(conninfo: str, *, exclude: frozenset[str] = IGNORED) -> dict[str, Any]:
    with psycopg.connect(conninfo, autocommit=True) as conn:
        tables = [r[0] for r in conn.execute(_TABLES) if r[0] not in exclude]
        columns: dict[str, list[list[Any]]] = {t: [] for t in tables}
        for table, name, type_, not_null, default in conn.execute(_COLUMNS):
            if table in columns:
                columns[table].append([name, type_, not_null, default])
        constraints = [list(r) for r in conn.execute(_CONSTRAINTS) if r[0] not in exclude]
        indexes = [list(r) for r in conn.execute(_INDEXES) if r[0] not in exclude]
        enums = {name: list(labels) for name, labels in conn.execute(_ENUMS)}
        sequences = [r[0] for r in conn.execute(_SEQUENCES)]
        functions = [r[0] for r in conn.execute(_FUNCTIONS)]
        rows: dict[str, list[Any]] = {}
        if "checkpoint_migrations" in tables:
            rows["checkpoint_migrations"] = [
                r[0] for r in conn.execute("SELECT v FROM checkpoint_migrations ORDER BY v")
            ]
    return {
        "tables": tables,
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "enums": enums,
        "sequences": sequences,
        "functions": functions,
        "rows": rows,
    }


def dump(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    conninfo, out = argv
    Path(out).write_text(dump(snapshot(conninfo)), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Capture the goldens from a database built by Prisma**

```bash
docker exec composer-p6-pg createdb -U composer golden_old
export GOLD=postgresql://composer:composer@localhost:55433/golden_old
mkdir -p tests/fixtures/schema
DATABASE_URL=$GOLD uv run prisma migrate deploy
DATABASE_URL=$GOLD uv run prisma migrate status            # "Database schema is up to date!"
uv run python -m tests.integration.db.schema_catalog "$GOLD" tests/fixtures/schema/golden_0001_baseline.json
DATABASE_URL=$GOLD uv run alembic upgrade 0004_langgraph_saver_tables
uv run python -m tests.integration.db.schema_catalog "$GOLD" tests/fixtures/schema/golden_0004_langgraph_saver_tables.json
docker exec composer-p6-pg psql -U composer -d golden_old -c '\d "_prisma_migrations"'   # record for Task 2's adoption fixture
docker exec composer-p6-pg dropdb -U composer golden_old
```

Sanity checks before committing: `golden_0001` has 17 tables (one per `model` in `prisma/schema.prisma`; `_prisma_migrations` and `alembic_version` are excluded), the enums `UserRole` (`admin`, `member`) and `ApprovalDecision` (`approved`, `rejected`), the index `workflow_executions_workflow_id_idempotency_key_key` (Prisma's, before 0002), and `workflow_assignments.assigned_by_id` NOT NULL (before 0003). `golden_0004` has 21 tables, the index `workflow_executions_workflow_id_user_id_idempotency_key_key`, the foreign keys `workflow_assignments_user_id_fkey` and `workflow_assignments_assigned_by_id_fkey`, and `rows.checkpoint_migrations == [0, …, 9]`. Check also that `_prisma_migrations`' columns match the DDL used in Task 2 Step 1 (`id varchar(36)` PK, `checksum varchar(64)`, `finished_at timestamptz`, `migration_name varchar(255)`, `logs text`, `rolled_back_at timestamptz`, `started_at timestamptz` default `now()`, `applied_steps_count integer` default 0). If it differs, use what `\d` printed.

- [ ] **Step 5: Record the image sizes on today's code**

```bash
docker build -t composer-backend:p6-before .
docker build --target builder -t composer-backend:p6-before-builder .
docker image inspect -f '{{.Size}}' composer-backend:p6-before composer-backend:p6-before-builder
docker history --no-trunc --format '{{.Size}}\t{{.CreatedBy}}' composer-backend:p6-before | head -20
docker run --rm --entrypoint sh composer-backend:p6-before -c \
  'du -sh /app/.venv /app/.prisma-cache 2>/dev/null; command -v node || echo no-node; python -c "import prisma, asyncpg; print(\"prisma+asyncpg present\")"'
```

Write the two sizes (bytes and MB), the `.prisma-cache` size and the `node` result into the Outcome section.

- [ ] **Step 6: Run the catalog module's own checks**

`uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`, `uv run ruff check src tests`, `uv run ruff format --check src tests`. Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add tests/integration/db/schema_catalog.py tests/fixtures/schema/
git commit -m "test(db): golden catalogs of a database built by Prisma, before Prisma goes

Phase 6 makes Alembic build fresh databases alone. These snapshots, taken from a
database that prisma migrate deploy built, are the oracle the new baseline must match
once Prisma can no longer build one.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Coordination note.** Append a section "Phase 6 (Prisma removal), 2026-11-26" to `docs/superpowers/notes/2026-11-25-codex-handoff-telemetry-phase-5.md`, containing the two file lists from "Coordination with Codex", the sequencing (Codex first; 0005's `down_revision` unchanged under D2(A)) and the "must not add" rules. Commit it as `docs(notes): phase 6 coordination for Codex`.

---

### Task 2: `0001_baseline` builds the base schema (needs D2, D3, D4)

**Files:**
- Modify: `src/db/migrations/versions/0001_baseline.py`
- Create: `tests/integration/db/test_baseline_schema.py`
- Create: `tests/unit/db/test_baseline_frozen.py`

**Interfaces:**
- Consumes: `snapshot`, `dump` and `IGNORED` from Task 1; `include_name` from `src.db.schema_filters`; `Base` from `src.db.models`.
- Produces: in `0001_baseline.py`, the module constants `PRISMA_MIGRATIONS: tuple[tuple[str, str], ...]` (20 `(name, sql)` pairs in order) and `PRISMA_MIGRATION_NAMES: tuple[str, ...]`, plus the functions `_statements(sql: str) -> list[str]` and `_base_state(bind: Connection) -> Literal["create", "adopt"]` (raises `RuntimeError` otherwise). Tests load the module with `importlib.util.spec_from_file_location`, because its name starts with a digit.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/db/test_baseline_schema.py
"""Alembic alone builds the same schema the old way built (Prisma's 20 migrations, then
Alembic). Each test runs in its own scratch database (CREATE DATABASE needs a role allowed to
create databases: CI's Postgres service and the local container are superusers)."""

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import psycopg
import pytest
from sqlalchemy.engine import make_url

from src.db.url import to_libpq_url
from tests.integration.db.schema_catalog import snapshot

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "schema"
BASELINE = REPO_ROOT / "src" / "db" / "migrations" / "versions" / "0001_baseline.py"

PRISMA_MIGRATIONS_TABLE = """
CREATE TABLE "_prisma_migrations" (
    "id" VARCHAR(36) PRIMARY KEY NOT NULL,
    "checksum" VARCHAR(64) NOT NULL,
    "finished_at" TIMESTAMPTZ,
    "migration_name" VARCHAR(255) NOT NULL,
    "logs" TEXT,
    "rolled_back_at" TIMESTAMPTZ,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT now(),
    "applied_steps_count" INTEGER NOT NULL DEFAULT 0
)
"""


def _baseline_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_0001", BASELINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _admin_url() -> str:
    return to_libpq_url(os.environ["TEST_DATABASE_URL"])


@pytest.fixture
def scratch_db() -> Iterator[str]:
    name = f"composer_scratch_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = make_url(_admin_url()).set(database=name).render_as_string(hide_password=False)
    try:
        yield url
    finally:
        with psycopg.connect(_admin_url(), autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        timeout=300,
    )


def _ok(url: str, *args: str) -> None:
    done = _alembic(url, *args)
    assert done.returncode == 0, done.stdout + done.stderr


def _golden(name: str) -> object:
    return json.loads((GOLDEN / f"golden_{name}.json").read_text(encoding="utf-8"))


def _prisma_built(url: str, names: tuple[str, ...]) -> None:
    """What prisma migrate deploy left behind: the DDL plus its history table."""
    mod = _baseline_module()
    with psycopg.connect(url, autocommit=True) as conn:
        for _, sql in mod.PRISMA_MIGRATIONS:
            for stmt in mod._statements(sql):
                conn.execute(stmt)
        conn.execute(PRISMA_MIGRATIONS_TABLE)
        for n in names:
            conn.execute(
                "INSERT INTO _prisma_migrations (id, checksum, finished_at, migration_name,"
                " applied_steps_count) VALUES (%s, %s, now(), %s, 1)",
                (str(uuid.uuid4()), "0" * 64, n),
            )


def test_fresh_database_at_0001_matches_what_prisma_built(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0001_baseline")
    assert snapshot(scratch_db) == _golden("0001_baseline")


def test_fresh_database_at_0004_matches_the_old_way(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0004_langgraph_saver_tables")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_fresh_database_at_head_matches_the_models(scratch_db: str) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine

    from src.db.models import Base
    from src.db.schema_filters import include_name

    _ok(scratch_db, "upgrade", "head")
    engine = create_engine(make_url(scratch_db).set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(
                conn,
                opts={"include_name": include_name, "compare_type": True,
                      "compare_server_default": True},
            )
            assert compare_metadata(ctx, Base.metadata) == []
    finally:
        engine.dispose()


def test_a_database_prisma_built_is_adopted_without_ddl(scratch_db: str) -> None:
    _prisma_built(scratch_db, _baseline_module().PRISMA_MIGRATION_NAMES)
    _ok(scratch_db, "upgrade", "0004_langgraph_saver_tables")
    assert snapshot(scratch_db) == _golden("0004_langgraph_saver_tables")


def test_a_partial_prisma_history_is_refused(scratch_db: str) -> None:
    names = _baseline_module().PRISMA_MIGRATION_NAMES
    _prisma_built(scratch_db, names[:-1])
    done = _alembic(scratch_db, "upgrade", "head")
    assert done.returncode != 0
    assert "missing Prisma migrations" in done.stderr
    assert names[-1] in done.stderr


def test_tables_without_prisma_history_are_refused(scratch_db: str) -> None:
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        conn.execute("CREATE TABLE workflows (id text PRIMARY KEY)")
    done = _alembic(scratch_db, "upgrade", "head")
    assert done.returncode != 0
    assert "no Prisma history" in done.stderr


def test_downgrade_below_the_baseline_is_refused(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0001_baseline")
    done = _alembic(scratch_db, "downgrade", "base")
    assert done.returncode != 0
    assert "0001 is the base schema" in done.stderr
    with psycopg.connect(scratch_db, autocommit=True) as conn:
        assert conn.execute("SELECT to_regclass('public.workflows')").fetchone() == ("workflows",)
```

Pyright strict: wrap the `mod.*` accesses in `cast(...)` (for example `cast("tuple[tuple[str, str], ...]", mod.PRISMA_MIGRATIONS)`) where pyright reports `Unknown`.

- [ ] **Step 2: Run them and see them fail**

`uv run pytest tests/integration/db/test_baseline_schema.py -v`. Expected: the 0001/0004/head tests FAIL (the fresh database lacks tables: today's 0001 is empty), `_prisma_built` errors with `AttributeError: PRISMA_MIGRATIONS`, and the downgrade test fails (base downgrade succeeds today).

- [ ] **Step 3: Write `0001_baseline.py`**

Build the SQL constants mechanically, never by hand-typing. Use a throwaway script in the scratchpad that, for each `prisma/migrations/<name>/migration.sql` in sorted order, emits `(<name>, """<file text with CRLF→LF, trailing whitespace on the file stripped>"""),`. Paste its output into the tuple. Then:

```python
"""Base schema: the 20 Prisma migrations that built every pre-Alembic database, verbatim.

Until phase 6 of the SQLAlchemy migration, `prisma migrate deploy` created these tables and
this revision was empty. Prisma is gone now, so this revision creates them on an empty
database, with the exact SQL Prisma ran (oldest first). The tables are therefore identical to
every existing database's; tests/integration/db/test_baseline_schema.py compares a fresh build
with catalogs of a database Prisma built (tests/fixtures/schema/). Later changes, including
the two deliberate divergences from prisma/schema.prisma (0002, 0003), stay in their own
revisions.

Existing databases are never re-run: one with an alembic_version row is already past this
revision. A database that only Prisma built (no alembic_version, all 20 migrations recorded in
_prisma_migrations) is adopted: nothing runs and Alembic records this revision, as the old
empty baseline did. Anything else (a partial Prisma history, unknown Prisma migrations, or
tables without a Prisma history) is refused rather than guessed at.

Downgrading below this revision is refused: it would drop every table.

The SQL is frozen by tests/unit/db/test_baseline_frozen.py. Never edit it; write a new
revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-20 (DDL added 2026-11-26, SQLAlchemy migration phase 6)
"""

from typing import Literal

from alembic import op
from sqlalchemy.engine import Connection

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

PRISMA_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "20260420081000_phase_1_execution_engine",
        """-- CreateTable
CREATE TABLE "workflows" (
...verbatim, generated by the script above...
""",
    ),
    # ... 19 more, ending with "20261117000000_execution_attempt_timing"
)

PRISMA_MIGRATION_NAMES: tuple[str, ...] = tuple(name for name, _ in PRISMA_MIGRATIONS)


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. The Prisma SQL has no functions, DO blocks or
    quoted semicolons, so splitting on ';' is exact. Comment-only chunks are skipped."""
    out: list[str] = []
    for chunk in sql.split(";"):
        code = [ln for ln in chunk.splitlines() if ln.strip() and not ln.strip().startswith("--")]
        if code:
            out.append(chunk.strip())
    return out


def _base_state(bind: Connection) -> Literal["create", "adopt"]:
    tables = {
        r[0]
        for r in bind.exec_driver_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
    } - {"alembic_version"}
    if not tables:
        return "create"
    if "_prisma_migrations" not in tables:
        raise RuntimeError(
            "0001_baseline: the public schema has tables but no Prisma history "
            "(_prisma_migrations); refusing to adopt a schema of unknown shape. Restore the "
            "database or build it from an empty one."
        )
    applied = {
        r[0]
        for r in bind.exec_driver_sql(
            "SELECT migration_name FROM _prisma_migrations "
            "WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL"
        )
    }
    missing = [n for n in PRISMA_MIGRATION_NAMES if n not in applied]
    unknown = sorted(applied - set(PRISMA_MIGRATION_NAMES))
    if missing or unknown:
        raise RuntimeError(
            "0001_baseline: this database was built by Prisma but is not at its final "
            f"migration; missing Prisma migrations: {', '.join(missing) or 'none'}; unknown: "
            f"{', '.join(unknown) or 'none'}. Finish it from a pre-phase-6 checkout "
            "(git checkout 58d2829 && uv run prisma migrate deploy) or restore it."
        )
    return "adopt"


def upgrade() -> None:
    bind = op.get_bind()
    if _base_state(bind) == "adopt":
        return
    for _, sql in PRISMA_MIGRATIONS:
        for statement in _statements(sql):
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    raise RuntimeError(
        "0001 is the base schema; downgrading below it would drop every table. "
        "Restore from a backup instead."
    )
```

Note: `exec_driver_sql` with no parameters sends the text unchanged. That is safe because the Prisma SQL contains no `%`. Task 2 Step 5's frozen test asserts this, so that a future revision author does not copy the pattern blindly.

- [ ] **Step 4: Run the integration tests until they pass**

`uv run pytest tests/integration/db/test_baseline_schema.py -v`. Expected: 7 passed. If a golden comparison fails, print the diff with `difflib.unified_diff(dump(golden).splitlines(), dump(actual).splitlines())`. The fix is always in how the SQL was copied (a missing migration or a whitespace-only edit, for example). The golden is never edited.

- [ ] **Step 5: Write the frozen test (and the temporary byte-equality test)**

```python
# tests/unit/db/test_baseline_frozen.py
"""0001_baseline's DDL is frozen: it is the SQL every pre-Alembic database ran.

If the fingerprint test fails, you edited 0001's SQL. Revert it and write a new Alembic
revision instead.
"""

import hashlib
import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[3]
BASELINE = REPO_ROOT / "src" / "db" / "migrations" / "versions" / "0001_baseline.py"
PRISMA_DIR = REPO_ROOT / "prisma" / "migrations"
FROZEN_FINGERPRINT = "<sha256 computed in Step 6>"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("baseline_0001", BASELINE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migrations() -> tuple[tuple[str, str], ...]:
    return cast("tuple[tuple[str, str], ...]", _module().PRISMA_MIGRATIONS)


def _fingerprint(migrations: tuple[tuple[str, str], ...]) -> str:
    digest = hashlib.sha256()
    for name, sql in migrations:
        digest.update(name.encode() + b"\0" + sql.encode() + b"\0")
    return digest.hexdigest()


def test_baseline_sql_is_unchanged() -> None:
    assert _fingerprint(_migrations()) == FROZEN_FINGERPRINT, (
        "0001_baseline's SQL is frozen: revert your change and write a new Alembic revision"
    )


def test_twenty_prisma_migrations_in_order() -> None:
    names = [n for n, _ in _migrations()]
    assert len(names) == 20
    assert names == sorted(names)
    assert names[0] == "20260420081000_phase_1_execution_engine"
    assert names[-1] == "20261117000000_execution_attempt_timing"


def test_sql_is_safe_to_send_unparameterised_and_in_one_transaction() -> None:
    """exec_driver_sql sends the text as-is (no '%' placeholders, no dollar quoting), and
    env.py runs every revision in one transaction (no CONCURRENTLY, no ALTER TYPE ADD VALUE,
    no explicit transaction control)."""
    for name, sql in _migrations():
        upper = sql.upper()
        assert "%" not in sql and "$$" not in sql, name
        for keyword in ("CONCURRENTLY", "ADD VALUE", "BEGIN;", "COMMIT;"):
            assert keyword not in upper, (name, keyword)


def test_statement_split_skips_comment_only_chunks() -> None:
    split = cast("Callable[[str], list[str]]", _module()._statements)
    assert split("-- CreateTable\nCREATE TABLE a (x int);\n\n-- tail\n") == [
        "-- CreateTable\nCREATE TABLE a (x int)"
    ]


# Removed in Task 6 together with prisma/: proves the copy is byte-for-byte while both exist.
def test_baseline_sql_equals_the_prisma_migration_files() -> None:
    files = sorted(p for p in PRISMA_DIR.iterdir() if (p / "migration.sql").is_file())
    expected = tuple(
        (p.name, (p / "migration.sql").read_bytes().replace(b"\r\n", b"\n").decode().rstrip() + "\n")
        for p in files
    )
    assert _migrations() == expected
```

Make the copy rule in Step 3's script match this test exactly: LF line endings, trailing whitespace stripped, and one final newline.

- [ ] **Step 6: Compute and pin the fingerprint, then run the unit tests**

`uv run python -c "import tests.unit.db.test_baseline_frozen as t; print(t._fingerprint(t._migrations()))"`. Paste the output into `FROZEN_FINGERPRINT`, then run `uv run pytest tests/unit/db/test_baseline_frozen.py tests/unit/db/test_migration_revisions.py tests/unit/db/test_prisma_frozen.py -v`. Expected: all pass (`prisma/` is untouched).

- [ ] **Step 7: Regression check on the old path.** Rebuild the test database the old way (`dropdb`/`createdb composer_test`, `prisma migrate deploy`, `alembic upgrade head`). This proves that a Prisma-built database is adopted on a real Prisma history and not just on the test fixture. Then run `uv run pytest -m "integration and not live" tests/integration/db`. Expected: all pass, including the 0002/0003/0004 up/down tests (they downgrade to 0001, never below).

- [ ] **Step 8: Opus review gate (required).** Dispatch a code-reviewer subagent on **Opus** (this is the squashed-base decision: wrong DDL or a wrong adoption rule corrupts fresh databases or breaks restores). Give it this task, D2–D4, the diff, and the two goldens. Ask it to check: the SQL equals the files; the adoption rules cover a restore, a Neon branch from before 2026-11-20, and a half-migrated dev database; the refusals are loud and actionable; nothing can run DDL on a non-empty schema; and Codex's 0005 still chains. Fix its findings, and add a regression test for each bug it finds (feedback memory: reviewer-found bugs ship with a reproducing test).

- [ ] **Step 9: Commit**

```bash
git add src/db/migrations/versions/0001_baseline.py tests/integration/db/test_baseline_schema.py tests/unit/db/test_baseline_frozen.py
git commit -m "feat(db): the Alembic baseline creates the base schema itself

Prisma is about to go, so a fresh database must be buildable by Alembic alone. The baseline
now carries the exact SQL Prisma ran, so fresh and existing databases match, and no
revision id changes, so production and Codex's 0005 are unaffected. Prisma-only databases
are adopted only when their history is complete.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Shared row helper; six small `NEEDS_LEGACY_PRISMA` modules; shim removed

**Files:**
- Create: `tests/integration/_rows.py`
- Modify: `tests/integration/conftest.py` (add the `rows` fixture)
- Modify: `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_external_invoke.py`, `test_security_hardening.py`, `test_standalone_auth_lifecycle.py`, `test_workflow_assignments.py`
- Modify: `tests/conftest.py` (remove the shim once Task 4's three modules are also moved; see Step 6)

**Interfaces:**
- Produces: `class Rows` in `tests/integration/_rows.py`, plus the fixture `rows(app: FastAPI) -> Rows` in `tests/integration/conftest.py`:
  - `async add[T](self, row: T) -> T` (commit, refresh, expunge; attributes stay readable because the sessionmaker has `expire_on_commit=False`)
  - `async get[T](self, model: type[T], pk: Any) -> T | None`
  - `async where[T](self, model: type[T], *criteria: ColumnElement[bool]) -> list[T]`
  - `async count(self, model: type[Any], *criteria: ColumnElement[bool]) -> int`
  - `async set(self, model: type[Any], *criteria: ColumnElement[bool], **values: Any) -> int` (rows updated)
  - `async remove(self, model: type[Any], *criteria: ColumnElement[bool]) -> int` (rows deleted)
  - `async drop_threads(self, thread_ids: list[str]) -> None` (all five checkpoint tables, through `checkpoints_repo.delete_threads`)
  - `sessionmaker` property, for tests that need a raw transaction (Task 4's cancel race).

**Translation table** (Prisma → `Rows`; ORM attributes are snake_case):

| Prisma (camelCase) | SQLAlchemy |
|---|---|
| `db.workflow.create(data={"userId": u, "name": n, "nodes": Json(x), "edges": Json(y)})` | `await rows.add(Workflow(user_id=u, name=n, nodes=x, edges=y))` (no `Json` wrapper) |
| `db.workflowexecution.create(data={"workflowId": w, "status": s, "threadId": t, "nodeResults": Json({}), "variables": Json({}), "runningSince": d, "leaseOwner": o, "leaseExpiresAt": e})` | `await rows.add(WorkflowExecution(workflow_id=w, status=s, thread_id=t, node_results={}, variables={}, running_since=d, lease_owner=o, lease_expires_at=e))` (datetimes must be aware) |
| `db.workflowexecution.find_unique(where={"id": i})` | `await rows.get(WorkflowExecution, i)` |
| `db.approval.find_many(where={"executionId": i})` | `await rows.where(Approval, Approval.execution_id == i)` |
| `.decision == "approved"`, `.nodeId` | `.decision == ApprovalDecision.APPROVED`, `.node_id` |
| `db.executionevent.find_many(where={"executionId": i})` | `await rows.where(ExecutionEvent, ExecutionEvent.execution_id == i)` (`.type`, `.payload` unchanged) |
| `db.workflowexecution.count(where={"id": i})` | `await rows.count(WorkflowExecution, WorkflowExecution.id == i)` |
| `db.workflowexecution.update(where={"id": i}, data={...})` | `await rows.set(WorkflowExecution, WorkflowExecution.id == i, **snake_case_values)` |
| `db.user.delete(where={"id": u})`, `db.workflow.delete(...)`, `db.workflowexecution.delete(...)` | `await rows.remove(User, User.id == u)` etc. |
| `db.executionevent.delete_many(where={"executionId": i})` | `await rows.remove(ExecutionEvent, ExecutionEvent.execution_id == i)` |
| `db.langgraphcheckpoint(write).delete_many(where={"threadId": t})` | `await rows.drop_threads([t])` |

- [ ] **Step 1: Write `_rows.py`**

```python
# tests/integration/_rows.py
"""Seed and inspect rows in integration tests that drive the real app.

Replaces the test-only Prisma client that tests/conftest.py gave NEEDS_LEGACY_PRISMA modules
until phase 6. Uses the app's own sessionmaker, so rows are committed before the code under
test reads them. ORM objects come back detached with their columns loaded; relationships are
lazy="raise", so read only columns.
"""

from typing import Any, TypeVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from src.db.repos import checkpoints as checkpoints_repo

T = TypeVar("T")


class Rows:
    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sm

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        return self._sm

    async def add(self, row: T) -> T:
        async with self._sm() as s:
            s.add(row)
            await s.commit()
            await s.refresh(row)
            s.expunge(row)
            return row

    async def get(self, model: type[T], pk: Any) -> T | None:
        async with self._sm() as s:
            row = await s.get(model, pk)
            if row is not None:
                s.expunge(row)
            return row

    async def where(self, model: type[T], *criteria: ColumnElement[bool]) -> list[T]:
        async with self._sm() as s:
            found = list((await s.scalars(select(model).where(*criteria))).all())
            for row in found:
                s.expunge(row)
            return found

    async def count(self, model: type[Any], *criteria: ColumnElement[bool]) -> int:
        async with self._sm() as s:
            return (await s.scalar(select(func.count()).select_from(model).where(*criteria))) or 0

    async def set(self, model: type[Any], *criteria: ColumnElement[bool], **values: Any) -> int:
        async with self._sm() as s:
            result = await s.execute(update(model).where(*criteria).values(**values))
            await s.commit()
            return result.rowcount  # pyright: ignore[reportAttributeAccessIssue]

    async def remove(self, model: type[Any], *criteria: ColumnElement[bool]) -> int:
        async with self._sm() as s:
            result = await s.execute(delete(model).where(*criteria))
            await s.commit()
            return result.rowcount  # pyright: ignore[reportAttributeAccessIssue]

    async def drop_threads(self, thread_ids: list[str]) -> None:
        async with self._sm() as s:
            await checkpoints_repo.delete_threads(s, thread_ids)
            await s.commit()
```

Check the actual signature of `checkpoints_repo.delete_threads` in `src/db/repos/checkpoints.py` and match it. If pyright accepts `CursorResult.rowcount` without the ignore (cast `execute`'s result to `CursorResult[Any]`), prefer that.

Add the fixture to `tests/integration/conftest.py`:

```python
@pytest.fixture
def rows(app: FastAPI) -> Rows:
    return Rows(app.state.db_sessionmaker)
```

- [ ] **Step 2: Move one module and run it (the worked example)**

In `tests/integration/test_user_approval_approved.py`: delete the `NEEDS_LEGACY_PRISMA` block and `db: Any = app.state.db`; change the signature to `(client: AsyncClient, rows: Rows)`; and replace the last block with:

```python
    approvals = await rows.where(Approval, Approval.execution_id == execution_id)
    assert len(approvals) == 1
    assert approvals[0].decision == ApprovalDecision.APPROVED
    assert approvals[0].note == "looks good"
    assert approvals[0].node_id == "ua"
```

Imports: `from src.db.models import Approval, ApprovalDecision` and `from tests.integration._rows import Rows`. Run `uv run pytest tests/integration/test_user_approval_approved.py -v`. Expected: PASS. Then make the same change in `test_user_approval_rejected.py` (`ApprovalDecision.REJECTED`).

- [ ] **Step 3: Move the other four modules** using the translation table: `test_external_invoke.py` (cleanup deletes of user/workflow), `test_security_hardening.py`, `test_standalone_auth_lifecycle.py` (its "delete the User row via Prisma directly" cleanup becomes `rows.remove(User, User.id == user_id)`), and `test_workflow_assignments.py`. Every assertion keeps its meaning; only attribute names change (`userId` → `user_id`, and so on). If an assertion compared a Prisma `Json` value, compare the plain Python value.

- [ ] **Step 4: Run the six modules**

`uv run pytest tests/integration/test_user_approval_approved.py tests/integration/test_user_approval_rejected.py tests/integration/test_external_invoke.py tests/integration/test_security_hardening.py tests/integration/test_standalone_auth_lifecycle.py tests/integration/test_workflow_assignments.py -v`. Expected: the same pass count as in Task 1's run for these files.

- [ ] **Step 5: Commit** `test: move six integration modules off the test-only Prisma client` (with the why: the Prisma package leaves in this phase; these tests now use the app's own sessionmaker).

- [ ] **Step 6: Remove the shim — this step runs after Task 4.** Once `grep -rn NEEDS_LEGACY_PRISMA tests` prints only `tests/conftest.py`, delete `tests/conftest.py:69-85`'s `if getattr(request.module, "NEEDS_LEGACY_PRISMA", False): …` branch, leaving:

```python
    async with _app.router.lifespan_context(_app):
        yield _app
```

Remove the now-unused `request` parameter if nothing else uses it. Run `uv run pytest -m "integration and not live"`; the expected count equals Task 1's. Commit `test: drop the NEEDS_LEGACY_PRISMA shim`.

---

### Task 4: The three Prisma-heavy modules

**Files:**
- Modify: `tests/integration/test_durable_execution.py`, `tests/integration/test_migration.py`, `tests/integration/test_mcp_highspot.py`

**Interfaces:**
- Consumes: `Rows` / the `rows` fixture (Task 3); models `Workflow`, `WorkflowExecution`, `ExecutionEvent`, `LangGraphCheckpoint`, `LangGraphCheckpointWrite`, `McpOAuthToken`, `McpServer` from `src.db.models`; `executions_repo.cancel_if_active` (the real guarded cancel) from `src.db.repos.executions`.

- [ ] **Step 1: `test_durable_execution.py` — seeding and inspection.** Remove `from prisma import Base64, Json`, the `NEEDS_LEGACY_PRISMA` block, and every `db: Any = app.state.db`. Add `rows: Rows` to each test signature. Translate every `db.<model>.<op>` with the Task 3 table. Rewrite the module docstring's lines 12–17: "Uses the shared `app`/`client` fixtures (real lifespan, real sessionmaker, checkpointer and event bus on `app.state`) and seeds rows through `tests/integration/_rows.py`."

- [ ] **Step 2: The cancel race (lines 140–157).** Today's `_cancel_running` opens `db.tx()` and runs `update_many(where={"id", "status": "running"})`. The replacement must keep a real guarded `UPDATE … WHERE status = 'running'` in its own transaction, racing the executor:

```python
        async def _cancel_running() -> int:
            await executor_ready.wait()
            start_updates.set()
            if first_writer == "executor":
                await first_committed.wait()
            async with rows.sessionmaker() as s:
                updated = await rows_set_running_to_canceled(s, execution.id)
                await s.commit()
            if first_writer == "cancel":
                first_committed.set()
            return updated
```

Define `rows_set_running_to_canceled` at module level as one statement, `update(WorkflowExecution).where(WorkflowExecution.id == execution_id, WorkflowExecution.status == "running").values(status="canceled", completed_at=utcnow(), running_since=None, lease_owner=None, lease_expires_at=None)`, returning `rowcount`. It mirrors the old `update_many` byte for byte. Do **not** swap in `executions_repo.cancel_if_active`: it also accepts `waiting_approval`, which changes what this race test proves. That path is covered by `test_public_cancel_accepts_an_already_committed_approval_pause`.

- [ ] **Step 3: `test_cancel_retains_checkpoint_and_pending_writes` (lines 435–500).** Seed the old tables with `rows.add(LangGraphCheckpoint(thread_id=…, checkpoint_ns="", checkpoint_id=…, checkpoint=b"retained-checkpoint", metadata_=b"retained-metadata"))` and `rows.add(LangGraphCheckpointWrite(…, value=b"retained-write"))`. Prisma's `Base64.encode(b"x")` stored the raw bytes `b"x"`, so the bytes are identical. Assert with `rows.where(...)`. **Add** the same retention check for the official saver, where real runs write now: before the cancel, `await app.state.checkpointer.aput({"configurable": {"thread_id": tid, "checkpoint_ns": ""}}, empty_checkpoint(), {}, {})` (`from langgraph.checkpoint.base import empty_checkpoint`); after the cancel, `assert await app.state.checkpointer.aget_tuple({"configurable": {"thread_id": tid}}) is not None`. Cleanup: `rows.drop_threads([tid])`.

- [ ] **Step 4: Run it**

`uv run pytest tests/integration/test_durable_execution.py -v`. Expected: Task 1's count for this file plus one more assertion block (no new test function). Run it twice in a row: the race tests are parametrised, so check for flakiness.

- [ ] **Step 5: `test_migration.py`.** Remove `from prisma import Json` and the shim. Lines 98–171: `find_unique` → `rows.get`, `create(data={… Json(...)})` → `rows.add(WorkflowExecution(...))`, `count` → `rows.count`. Lines 157 and 177 already use `app.state.db_sessionmaker` and stay. Assertions on `row.userId` / `row.originalOwnerEmail` become `row.user_id` / `row.original_owner_email`. Run `uv run pytest tests/integration/test_migration.py -v`.

- [ ] **Step 6: `test_mcp_highspot.py`.** `db.mcpoauthtoken.create(data={… Json(...)})` → `rows.add(McpOAuthToken(...))`, with snake_case fields as in `src/db/models/mcp.py` and plain dict/list values for JSONB. `db.mcpserver.update` → `rows.set(McpServer, McpServer.id == sid, ...)`. It is a `live` module (real Highspot), so locally run only `uv run pytest tests/integration/test_mcp_highspot.py --collect-only` plus pyright. CI does not run `live` either. Note in the Outcome that it was only collected.

- [ ] **Step 7: Commit** `test: move durable-execution, migration and Highspot tests off Prisma` (why: same as Task 3; the cancel race keeps its own guarded UPDATE so it still proves single-winner semantics). Then do Task 3 Step 6 (the shim removal).

---

### Task 5: Test-side asyncpg, dead Prisma-shaped mocks, scripts, stale wording

**Files:**
- Modify: `tests/_rate_limit_reset.py`, `tests/_checkpoint_reset.py`
- Modify: the unit test files in "Every remaining … use" → `tests` → row "Prisma-shaped mocks", plus `tests/integration/test_embedded_auth.py`
- Modify: `scripts/seed_templates.py`, `frontend/scripts/promote_admin.py`; Delete: `scripts/poc_persistence_row_lock.py`
- Create: `tests/integration/test_seed_templates.py`, `tests/integration/test_promote_admin.py`
- Modify: `src/engine/context.py:1`, `src/api/test_cleanup.py:66-69`, `src/migration/transformers.py:1,37`, `frontend/lib/api/generated/schema.ts` (regenerated)

**Interfaces:**
- Consumes: `one_shot_session(settings)` from `src.db.runtime`; `workflows_repo.get_by_slug/create/update`, `WorkflowValues`, `WorkflowChanges`; `users_repo.get_by_email/set_role`; `UserRole` from `src.db.models`.
- Produces: `scripts.seed_templates.seed(session: AsyncSession) -> list[tuple[str, str]]` (`(outcome, slug)` pairs) and `main() -> None`; `frontend/scripts/promote_admin.promote(email: str) -> None`.

- [ ] **Step 1: psycopg reset helpers.** `tests/_rate_limit_reset.py`:

```python
import os

import psycopg

from src.db.url import to_libpq_url


async def reset_rate_limit_buckets() -> None:
    if not os.environ.get("TEST_DATABASE_URL"):
        return
    async with await psycopg.AsyncConnection.connect(
        to_libpq_url(os.environ["TEST_DATABASE_URL"]), autocommit=True, prepare_threshold=None
    ) as conn:
        await conn.execute("DELETE FROM rate_limit_buckets")
```

Edit the module docstring: "Takes a bare psycopg connection" (instead of asyncpg); delete the sentence about a "cross-event-loop Prisma deadlock" and keep the reason it avoids the `app` fixture (`test_events_ws_hardening.py` overrides it). In `tests/_checkpoint_reset.py`, replace `asyncpg.connect` with the same connect; `conn.fetchval("SELECT to_regclass($1)", t)` with `(await (await conn.execute("SELECT to_regclass(%s)", (t,))).fetchone())[0]`; `conn.fetch(sql)` with `await (await conn.execute(sql)).fetchall()` (rows are tuples, so `r[0]`); and the delete with `await conn.execute(f"DELETE FROM {table} WHERE thread_id <> ALL(%s)", (list(kept),))`. Type `_threads_in(conn: psycopg.AsyncConnection[Any], …)`. Note: on Windows, psycopg async needs the selector event loop. pytest-asyncio's loop is already the one the app uses here (the tests' psycopg engine works). If `ProactorEventLoop` errors appear, the event-loop policy fixture in `tests/conftest.py` is the place to look; do not add a second policy.

- [ ] **Step 2: Run** `uv run pytest -m "integration and not live" tests/integration/db tests/integration/sqla_phase4a` and then the full `-m "integration and not live"`. Expected: Task 1's counts, and 0 rows left in the five checkpoint tables afterwards (`SELECT count(*)` on each).

- [ ] **Step 3: Remove the dead Prisma-shaped mocks.** In each listed unit file, delete the `db = MagicMock()` / `db.user = …` / `db.user.find_unique = AsyncMock(…)` / `db.workflow.find_unique = …` / `db.workflowexecution.find_unique = …` lines and the `app.state.db = db` (or `= MagicMock()`) line, plus comments that only explain them. These include `test_executions.py:163` "The Prisma client is only passed through to enqueue_execution", `test_internal_poll_file_triggers.py:66` "Prisma handle…", `test_workflows_crud.py:115` and `:827`, and `test_mcp_servers.py:398` "In test mode the db is a MagicMock (not Prisma)…". Rewrite those last two to state what the test relies on today (dev-mode role resolution through the overridden session). If a removed `db` mock was the source of a value the test still asserts (for example `test_executions_size_limit.py`'s `workflow_row`), check whether a repo fake already supplies it. If a test fails after the removal, the mock was not dead: stop and report rather than re-adding it. Run `uv run pytest -m "not integration"` after each 5 files. Expected: unit count unchanged.

- [ ] **Step 4: Port `scripts/seed_templates.py`** (lines 36 and 2997–3048; the template data is untouched):

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.repos import workflows as workflows_repo
from src.db.runtime import one_shot_session


async def _upsert_template(session: AsyncSession, tmpl: dict[str, Any]) -> str:
    """Idempotent upsert by `externalSlug`. Returns "created" or "updated"."""
    slug: str = tmpl["externalSlug"]
    changes: workflows_repo.WorkflowChanges = {
        "name": tmpl["name"],
        "description": tmpl["description"],
        "category": tmpl["category"],
        "tags": tmpl["tags"],
        "difficulty": tmpl["difficulty"],
        "estimated_time": tmpl["estimatedTime"],
        "nodes": tmpl["nodes"],
        "edges": tmpl["edges"],
        "is_template": True,
        "is_public": True,
        "is_production": False,
        "user_id": None,
    }
    existing = await workflows_repo.get_by_slug(session, slug)
    if existing is None:
        values: workflows_repo.WorkflowValues = {
            "name": tmpl["name"],
            "nodes": tmpl["nodes"],
            "edges": tmpl["edges"],
            "description": tmpl["description"],
            "category": tmpl["category"],
            "tags": tmpl["tags"],
            "difficulty": tmpl["difficulty"],
            "estimated_time": tmpl["estimatedTime"],
            "is_template": True,
            "is_public": True,
            "is_production": False,
            "external_slug": slug,
            "user_id": None,
        }
        await workflows_repo.create(session, values)
        return "created"
    await workflows_repo.update(session, existing.id, changes)
    return "updated"


async def seed(session: AsyncSession) -> list[tuple[str, str]]:
    return [(await _upsert_template(session, t), t["externalSlug"]) for t in _TEMPLATES]


async def main() -> None:
    async with one_shot_session(get_settings()) as session:
        results = await seed(session)
        await session.commit()
    for (outcome, slug), tmpl in zip(results, _TEMPLATES, strict=True):
        print(f"  {outcome:7s}  {slug:40s}  {tmpl['name']}")


if __name__ == "__main__":
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(main())
    else:
        asyncio.run(main())
```

Add `import sys`. Check the keys of `WorkflowValues` and `WorkflowChanges` in `src/db/repos/workflows.py:48-88`; if a key here is not in the TypedDict, the repo is the source of truth. The selector loop is needed because psycopg async cannot use Windows' Proactor loop (CLAUDE.md gotcha 6).

- [ ] **Step 5: Test it** (`scripts` is outside pyright's `include`, so this test is the type and behaviour check):

```python
# tests/integration/test_seed_templates.py
"""scripts/seed_templates.py seeds every template once and is idempotent."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scripts.seed_templates import _TEMPLATES, seed  # pyright: ignore[reportPrivateUsage]
from src.db.models import Workflow
from tests.integration._rows import Rows

pytestmark = pytest.mark.integration


async def test_seed_creates_then_updates_without_duplicates(rows: Rows) -> None:
    sm: async_sessionmaker[AsyncSession] = rows.sessionmaker
    slugs = [t["externalSlug"] for t in _TEMPLATES]
    await rows.remove(Workflow, Workflow.external_slug.in_(slugs))
    try:
        async with sm() as s:
            first = await seed(s)
            await s.commit()
        async with sm() as s:
            second = await seed(s)
            await s.commit()
        assert {o for o, _ in first} == {"created"}
        assert {o for o, _ in second} == {"updated"}
        seeded = await rows.where(Workflow, Workflow.external_slug.in_(slugs))
        assert len(seeded) == len(slugs)
        assert all(w.is_template and w.is_public and w.user_id is None for w in seeded)
    finally:
        await rows.remove(Workflow, Workflow.external_slug.in_(slugs))
```

The test database may already hold seeded templates. The test deletes them by slug first and again at the end, so it never depends on prior state. `scripts/` has no `__init__.py`; it imports as a namespace package from the repo root, the same way `python -m scripts.seed_templates` works. If pytest cannot import it, add an empty `scripts/__init__.py` (not a policy file). Run it: PASS.

- [ ] **Step 6: Port `frontend/scripts/promote_admin.py`** (run by `frontend/e2e/admin.spec.ts` as `<repo>/.venv/.../python frontend/scripts/promote_admin.py <email>`, with an unknown cwd):

```python
"""Flip a user to admin role by email. Used by Playwright admin tests."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `src`

from src.config import get_settings  # noqa: E402
from src.db.models import UserRole  # noqa: E402
from src.db.repos import users as users_repo  # noqa: E402
from src.db.runtime import one_shot_session  # noqa: E402


async def promote(email: str) -> None:
    async with one_shot_session(get_settings()) as session:
        user = await users_repo.get_by_email(session, email)
        if user is None:
            raise SystemExit(f"user not found: {email}")
        await users_repo.set_role(session, user.id, UserRole.ADMIN)
        await session.commit()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python promote_admin.py <email>")
    if sys.platform == "win32":
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(promote(sys.argv[1]))
    else:
        asyncio.run(promote(sys.argv[1]))


if __name__ == "__main__":
    main()
```

Check the `UserRole` member name in `src/db/models/enums.py`. `get_settings()` reads `DATABASE_URL` from the environment or `.env` exactly as the Prisma client did, since the E2E job sets `DATABASE_URL`. Test it as a subprocess, the way E2E calls it: `tests/integration/test_promote_admin.py` creates a member with `rows.add(User(email=…, role=UserRole.MEMBER, …))`, runs `subprocess.run([sys.executable, "frontend/scripts/promote_admin.py", email], cwd=tmp_path, env={**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"]})`, asserts return code 0 and the role `ADMIN`, checks that an unknown email exits non-zero with "user not found", and removes the user afterwards. Run it: PASS.

- [ ] **Step 7: Delete `scripts/poc_persistence_row_lock.py`** (D7). In `docs/decisions.md` ADR-0031's "Implemented by." line, add: "(Removed in SQLAlchemy migration phase 6, 2026-11-26: it exercised Prisma's raw-SQL API, which no longer exists; see git history before the phase 6 merge.)" ADR-0031 is otherwise historical and stays.

- [ ] **Step 8: Stale wording in `src`.** In `src/engine/context.py:1` the docstring says "Per-execution context — Prisma client available to executors." Describe what the context carries today (read the file). In `src/migration/transformers.py:1` change "Composer Prisma dict" to "Composer insert dict (camelCase; `writer.py` maps keys to columns)", and at line 37 change "Prisma @id accepts any unique string" to "the text primary key accepts any unique string". In `src/api/test_cleanup.py:66-69` replace the `schema.prisma` references with "the database's `ON DELETE CASCADE` foreign keys (Alembic `0001_baseline`)". That docstring is published in OpenAPI, so regenerate `frontend/lib/api/generated/schema.ts`: start the backend (`uv run uvicorn src.main:app --reload`, against the test database) and run `npm --prefix frontend run generate-client`. Then check with `git diff --stat frontend/lib/api/generated/schema.ts` that only those comment lines changed. If other lines moved, the generated file was already stale: keep only the two comment hunks and note the drift in the Outcome.

- [ ] **Step 9 (after Codex merges and the rebase only): comments in Codex's files.** `src/api/executions.py:430` ("via Prisma `onDelete: Cascade`" → "via the `ON DELETE CASCADE` foreign key", and regenerate `schema.ts` line 672 as in Step 8); `src/api/internal.py:241` and `:573`; `tests/unit/engine/test_langgraph_executor.py:3,95,102,152` (describe the mocks as repo fakes); `tests/unit/api/test_internal.py:231`; `tests/integration/sqla_phase4a/conftest.py:925` ("today `app.state.db`" → the sessionmaker). If Codex has not merged when the PR is ready, skip this step and list it under Follow-ups.

- [ ] **Step 10: Run and commit.** Run the unit suite, the integration suite, pyright (both) and ruff. Commit in three commits: `test: reset helpers on psycopg instead of asyncpg`; `test: drop Prisma-shaped app.state.db mocks no code reads`; `chore(scripts): seed and promote scripts on SQLAlchemy; remove the Prisma POC`. Each message gives the why (the packages leave in Task 6; the E2E admin spec depends on promote_admin).

---

### Task 6: Packages and `prisma/` out (needs D1, D6)

**Files:**
- Modify: `pyproject.toml` (lines 18–21, 29–30, 85, 104, 133, 177), `uv.lock`
- Delete: `prisma/` (whole directory), `tests/unit/db/test_prisma_frozen.py`, `tests/unit/test_prisma_execution_schema.py`
- Modify: `tests/unit/db/test_baseline_frozen.py` (delete `test_baseline_sql_equals_the_prisma_migration_files` and `PRISMA_DIR`)
- Modify: `tests/unit/db/test_prisma_import_boundary.py`

**Interfaces:**
- Produces: the boundary test constants `SCANNED_ROOTS = ["src", "tests", "scripts", "frontend/scripts"]` and `FORBIDDEN_ROOTS = ("prisma", "src.storage", "asyncpg")`, plus the new tests `test_packages_are_not_installed`, `test_no_legacy_prisma_shim_or_app_state_db_writes`.

- [ ] **Step 1: Widen the boundary test first (failing)**

```python
SCANNED_ROOTS = ["src", "tests", "scripts", "frontend/scripts"]


def test_nothing_imports_prisma() -> None:
    assert _offenders("prisma", "src.storage") == []


def test_nothing_imports_asyncpg() -> None:
    assert _offenders("asyncpg") == []


def test_packages_are_not_installed() -> None:
    import importlib.util

    assert importlib.util.find_spec("prisma") is None
    assert importlib.util.find_spec("asyncpg") is None


def _writes_app_state_db(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        targets = node.targets if isinstance(node, ast.Assign) else []
        for t in targets:
            if (
                isinstance(t, ast.Attribute)
                and t.attr == "db"
                and isinstance(t.value, ast.Attribute)
                and t.value.attr == "state"
            ):
                return True
    return False


def test_no_legacy_prisma_shim_or_app_state_db_writes() -> None:
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path.name == "test_prisma_import_boundary.py":
            continue
        if "NEEDS_LEGACY_PRISMA" in text or _writes_app_state_db(text):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


def test_app_state_db_detector() -> None:
    assert _writes_app_state_db("app.state.db = x")
    assert not _writes_app_state_db("app.state.db_sessionmaker = x")
```

Rename `test_src_does_not_import_prisma` / `_asyncpg` to the two `test_nothing_imports_*` names above, keep the detector tests, and rewrite the module docstring: "Nothing in `src`, `tests`, `scripts` or `frontend/scripts` imports Prisma, `src.storage` or asyncpg, and neither package is installed (SQLAlchemy migration phase 6). Tests never write `app.state.db` (no code reads it since phase 5)." Run it: `test_packages_are_not_installed` FAILS (the packages are still installed); the rest PASS after Tasks 3–5.

- [ ] **Step 2: Delete `prisma/` and its pinned tests**

```bash
git rm -r prisma tests/unit/db/test_prisma_frozen.py tests/unit/test_prisma_execution_schema.py
```

In `tests/unit/db/test_baseline_frozen.py`, delete `PRISMA_DIR` and `test_baseline_sql_equals_the_prisma_migration_files`. The fingerprint now carries that guarantee.

- [ ] **Step 3: Remove the packages**

```bash
uv remove prisma asyncpg
uv remove --optional dev asyncpg-stubs
git diff --stat uv.lock
git diff uv.lock | grep -E '^[-+]name = ' | sort
```

Expected: only `-name = "prisma"`, `-name = "asyncpg"`, `-name = "asyncpg-stubs"`, `-name = "jinja2"` and `-name = "tomlkit"`, with no `+name` lines. `git diff uv.lock | grep -E '^[-+]version = '` shows only those packages' versions. If `uv` moved any other version, revert `uv.lock` and run `uv lock` with no upgrade flags, or stop and report. Then edit `pyproject.toml` by hand: reword the database comment (D1), remove the `# Postgres LISTEN/NOTIFY (P1-4)` comment, and remove `"prisma/generated"` from lines 104, 133 and 177. Run `uv sync --all-extras` and `uv lock --check`.

- [ ] **Step 4: Run everything**

`uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests && uv run pytest -m "not integration" && uv run pytest -m "integration and not live"`. Expected: all green. Unit count = Task 1 − 4 (deleted `test_prisma_frozen.py`'s 2 tests, `test_prisma_execution_schema.py`'s 1, the byte-equality test) + 4 new `test_baseline_frozen.py` tests + 3 new boundary tests. Integration = Task 1 + 7 (baseline schema) + 1 (seed) + 1 (promote, 2 if split). Recompute the exact numbers and write them into the Outcome.

- [ ] **Step 5: Fresh database by Alembic alone.** `dropdb`/`createdb composer_test`; then `DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head`, then `uv run pytest tests/integration/db/test_schema_parity.py -v`, then the full integration suite once more on this database. This is exactly what CI will do.

- [ ] **Step 6: Commit** `build: remove Prisma, asyncpg and the frozen Prisma schema` (why: archived upstream, nothing imports them since phase 5, Alembic now builds fresh databases alone; prisma/ history stays in git).

---

### Task 7: Image, CI and deploy (needs D5)

**Files:**
- Modify: `Dockerfile`, `.dockerignore` (57–59), `.gitignore` (58–60)
- Modify: `.github/workflows/ci.yml` (64–71, 342–349), `.github/workflows/deploy-gcp.yml` (100, 129–141, 160–175), `.github/workflows/live-provider-smoke.yml` (80–87)
- Modify: `tests/unit/test_deploy_workflow.py`
- Create: `tests/unit/test_build_files.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_build_files.py
"""Prisma and Node.js stay out of the backend build and the pipelines (SQLAlchemy migration
phase 6). The frontend jobs' actions/setup-node is fine: it is not the backend image."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD_FILES = [ROOT / "Dockerfile", ROOT / ".dockerignore", *sorted((ROOT / ".github/workflows").glob("*.yml"))]


def test_no_build_or_pipeline_file_mentions_prisma() -> None:
    offenders = [p.name for p in BUILD_FILES if "prisma" in p.read_text(encoding="utf-8").lower()]
    assert offenders == []


def test_backend_image_installs_no_node_toolchain() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
    for marker in ("nodesource", "nodejs", "apt-get install -y --no-install-recommends nodejs"):
        assert marker not in text


def test_security_refresh_arg_is_kept() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^ARG SECURITY_REFRESH=\d{4}-\d{2}-\d{2}$", text, re.MULTILINE)
    assert 'echo "security refresh ${SECURITY_REFRESH}"' in text


def test_every_workflow_that_prepares_a_database_uses_alembic_only() -> None:
    for name in ("ci.yml", "live-provider-smoke.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "uv run alembic upgrade head" in text, name
```

In `tests/unit/test_deploy_workflow.py`, rename the guard test to `test_checkpoint_guard_runs_before_alembic_and_before_deploy_without_override`. Delete line 35 (`assert guard > lines.index("uv run prisma migrate deploy")`) and add `assert lines.index("uv sync --frozen") < guard`, `assert lines.index("uv run alembic upgrade head") < lines.index("uv run alembic current")` and `assert not any("prisma" in ln for ln in lines)`. Run both files: the new tests FAIL.

- [ ] **Step 2: Dockerfile.** Header (lines 1–16):

```dockerfile
# Composer backend (FastAPI + LangGraph + SQLAlchemy) — Cloud Run image.
#
# Build:   docker build -t composer-backend .
# Run:     docker run -e DATABASE_URL=... -e JWT_SECRET=... -p 8080:8080 composer-backend
#
# Multi-stage build:
#   builder  - Python 3.12 + uv. Installs the locked dependencies and the project into /app/.venv.
#   runtime  - Python 3.12 only. Copies /app from builder (the venv and the source). No uv,
#              no apt cache, no build tools.
#
# Migrations are not run from the image: the deploy workflow runs `alembic upgrade head`
# from its own checkout before the new revision takes traffic.
```

Builder: `ENV` keeps `PYTHONDONTWRITEBYTECODE`, `PYTHONUNBUFFERED`, `PIP_DISABLE_PIP_VERSION_CHECK`, `UV_LINK_MODE=copy` and `UV_PROJECT_ENVIRONMENT=/app/.venv`; the `PRISMA_*` lines and their comment are deleted. The `RUN apt-get … nodesource … nodejs` block is deleted. The `COPY --from=ghcr.io/astral-sh/uv:0.8.15@sha256:a572…` line is unchanged. The manifests comment becomes "Dependency manifests first so this layer caches independently of source changes." and `COPY prisma/schema.prisma prisma/` is deleted. The `RUN uv run prisma generate` line and its comment are deleted. Runtime: delete `PRISMA_HIDE_UPDATE_MESSAGE` and `PRISMA_BINARY_CACHE_DIR`. The copy comment becomes "brings the venv, the source and the project manifest — nothing else." The user comment becomes "code + venv are NOT writable by the app". **Keep** the base-image digests, the `SECURITY_REFRESH` block (lines 80–89 verbatim), the user setup, `HOME`, `USER`, `EXPOSE` and `CMD`. `.dockerignore`: delete lines 57–59. `.gitignore`: delete lines 58–60 (`# Prisma`, `prisma/generated/`, `prisma/migrations/dev.db*`).

- [ ] **Step 3: Workflows.** In `ci.yml` (backend job and E2E job) and `live-provider-smoke.yml`, delete the "Generate Prisma client" and "Apply migrations" steps and rename "Apply Alembic migrations" to "Apply migrations (Alembic)". In `deploy-gcp.yml`:
  - line 100: "because our Dockerfile has a few expensive layers (uv sync)";
  - lines 129–141 become:

```yaml
      # Apply pending Alembic migrations BEFORE the new revision takes traffic. The
      # models select every mapped column, so code that is ahead of the database breaks
      # even plain reads (2026-11-19: GET/POST /executions returned 500 for a missing
      # column). A failed migration fails the job here, so the previous revision keeps
      # serving. Migrations must stay additive/backward-compatible, because the old
      # revision runs against the new schema until the deploy finishes. The URL comes
      # from the same Secret Manager entry the backend uses; the Neon "-pooler" host is
      # swapped for the direct endpoint, since migrations take a lock that PgBouncer
      # transaction pooling breaks.
```

  - in the run block, delete `uv run prisma migrate deploy` and `uv run prisma migrate status`; the guard comment and `uv run composer checkpoints guard` stay; the Alembic comment becomes `# Alembic is the only migration tool (phase 6). On an empty database 0001 creates the base schema.`

- [ ] **Step 4: Run** `uv run pytest tests/unit/test_build_files.py tests/unit/test_deploy_workflow.py -v`: PASS. Validate the YAML with `uv run python -c "import yaml,glob;[yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"`. Run `actionlint` if it is installed.

- [ ] **Step 5: Build and smoke the image locally**

```bash
docker build -t composer-backend:p6-after .
docker build --target builder -t composer-backend:p6-after-builder .
docker image inspect -f '{{.Size}}' composer-backend:p6-after composer-backend:p6-after-builder
docker run --rm --entrypoint sh composer-backend:p6-after -c \
  'command -v node || echo no-node; python -c "import importlib.util as u; print(u.find_spec(\"prisma\"), u.find_spec(\"asyncpg\"))"; ls /app'
docker run --rm -p 18080:8080 -e DATABASE_URL="postgresql://composer:composer@host.docker.internal:55433/composer_test" \
  -e JWT_SECRET=local-only-secret-at-least-32-characters-long -e ENVIRONMENT=development composer-backend:p6-after
```

Expected: `no-node`, `None None`, no `.prisma-cache` in `/app`. Then `curl -fsS http://localhost:18080/ready` returns 200 (add whatever other env the app's config validation requires in development mode; `docs/operations/gcp-cloud-run-setup.md` lists them). Optional, but worth doing since CI minutes are scarce: run Trivy locally with the CI command (`ci.yml:252-266`) against `composer-backend:p6-after`, and record the result.

- [ ] **Step 6: Record before/after** sizes (runtime and builder), the size delta, and the build time of a cold `--no-cache` build before and after if you have 10 minutes. Put them in the Outcome and the CHANGELOG entry.

- [ ] **Step 7: Opus review gate (required).** Dispatch a code-reviewer subagent on **Opus** with D5, the diffs of the Dockerfile and the three workflows, and `tests/unit/test_deploy_workflow.py`. Ask it to confirm that the deploy still: fails closed on a migration error; runs the guard before Alembic; uses the direct endpoint; keeps the `SECURITY_REFRESH` cache-busting; and that the CI backend and E2E jobs now build their databases with Alembic alone (exercising Task 2's create path on every run). Also ask whether any step still needs Node.js for the backend. Fix its findings, each with a test.

- [ ] **Step 8: Commit** `build: drop Prisma and Node.js from the image, CI and deploy` (why: nothing uses them; Alembic builds and migrates databases alone; smaller builder and less CVE surface).

---

### Task 8: Docs, done greps, full local run, the PR (needs D8 for `CLAUDE.md`)

- [x] **Step 1: Done-criterion greps.** Each must print nothing (results in the Outcome section):

```bash
grep -rnE "^\s*(from|import) (prisma|asyncpg)\b" src tests scripts frontend/scripts
grep -rn "NEEDS_LEGACY_PRISMA" tests
grep -rnE "app\.state\.db\s*=" tests
grep -rni "prisma" Dockerfile .dockerignore .github/workflows pyproject.toml uv.lock
grep -rniE "nodesource|nodejs" Dockerfile
grep -rn "asyncpg" pyproject.toml uv.lock tests/_rate_limit_reset.py tests/_checkpoint_reset.py
test ! -e prisma && echo "prisma/ gone"   # prints "prisma/ gone"
uv run alembic heads                       # exactly one head
```

  Remaining `prisma` mentions in `src` are only the kept historical notes from the list above: `grep -rni prisma src | grep -v -e "src/db/" -e "src/checkpoints/legacy.py"` prints only the Codex-file lines if Task 5 Step 9 was skipped.

- [x] **Step 2: Docs.** (Done except the spec's PR number, which is filled in when the PR exists.)
  - `CHANGELOG.md` `[Unreleased]`, first entry, "Removed — SQLAlchemy migration phase 6: Prisma, asyncpg and Node.js (2026-11-xx)". Cover: packages removed (and the two transitive ones); `prisma/` deleted; `0001_baseline` creates the base schema with Prisma's exact SQL, adopts complete Prisma-only databases and refuses partial ones, and its downgrade is refused; no revision ids changed, and production's database is untouched; fresh databases need only `alembic upgrade head`; image sizes before and after; CI and deploy are Alembic-only; the scripts ported, the POC removed; `_prisma_migrations` and the old checkpoint tables stay until phase 7.
  - `docs/decisions.md`: **ADR-0037 "Prisma removed; Alembic builds the base schema"**. Context: phase 6 of ADR-0035. Decision: D2(A), D3(a), D4. Consequences: fresh databases come from Alembic alone, no stamp anywhere, and the spec's "squashed base" is realised as 0001 plus the existing chain. Related: ADR-0031, ADR-0035, ADR-0036. Add a note to ADR-0035 saying phase 6 is done (ADR-0037).
  - The spec: §3.3 phase 6 row "(done, PR #<n>)"; §3.2's last sentence → "After phase 6, `0001_baseline` creates the base schema with the exact SQL of Prisma's 20 migrations, and the existing revisions follow (ADR-0037); no database needed a stamp."; §2.3 "Removed in phase 6: `prisma`, `asyncpg`, `asyncpg-stubs` (and `jinja2`, `tomlkit`, which only Prisma needed)."; §3.1 rule 5 → past tense.
  - `docs/architecture.md` (30, 129, 135, 137, 145, 147): the stack line becomes SQLAlchemy/Alembic; the repo tree loses `prisma/`; the phase 5 paragraph's "phase 6 removes the client, schema and Node.js" becomes "removed in phase 6".
  - `docs/operations/production-deployment.md`: §Phase 3 (45–67) is Alembic-only, and "Done when" becomes "`uv run alembic current` prints the head revision". 184–190: replace `prisma migrate status` with `alembic current`; the read-only remark becomes "use the direct (non-pooler) URL for migrations". 320–324: "The deploy workflow runs `uv run composer checkpoints guard` BEFORE `alembic upgrade head` …" (drop "after `prisma migrate deploy`"). Add a short note: "A database that only Prisma built (before 2026-11-20) is adopted by `alembic upgrade head` when its Prisma history is complete; otherwise the upgrade refuses and names the missing migrations."
  - `docs/operations/gcp-cloud-run-setup.md` (91, 95, 253), `postgres-setup.md` (5, 52–77, 194), `disaster-recovery.md` (136–137: a restore from before 2026-11-20 is adopted, as above), `incident-response.md` (158, 170), `scaling.md:74` (Prisma's pool is gone; only the SQLAlchemy and saver pools and the one LISTEN connection remain), `docs/operations.md:15`, `docs/getting-started.md` (74–88: `uv run alembic upgrade head` only), `README.md` (quick start: no `prisma generate` or `migrate deploy`; architecture box `(Prisma)` → `(SQLAlchemy)`; stack and tooling rows; Node.js prerequisite "for the frontend"), `docs/overview.md` (97, 104), `docs/saas/overview.md:60`, `docs/saas/privacy.md` (15, 22), `docs/telemetry/signal-catalog.md:369`, and `alembic.ini:1` ("Alembic is the only migration tool; 0001 creates the base schema.").
  - `CLAUDE.md` (D8): as decided.
  - The phase 5 plan's "After phase 5: what phase 6 removes": tick each item, with a link here. This plan: tick the checkboxes and add an Outcome section (what changed against the plan; Task 1 and Task 6 counts; image sizes; review-gate findings; Codex rebase result; whether Task 5 Step 9 ran).
  - Run `uv run python scripts/check_doc_links.py` (the repo's link checker).

- [ ] **Step 3: Rebase onto main** (expected to include Codex's merge, per D9). Re-run Task 5 Step 9 if Codex has merged. Check `uv run alembic heads`: one head (`0005_execution_attempts` if Codex merged). Run `test_baseline_schema.py` again: the goldens pin 0001 and 0004, so 0005 must not change them, and the head-parity test covers 0005.

- [ ] **Step 4: Run everything CI runs, locally, on a database built by Alembic alone** (`dropdb`/`createdb composer_test` + `alembic upgrade head`): `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests && uv run pytest -m "not integration" && uv run pytest -m "integration and not live"`; the frontend checks (`npm --prefix frontend ci && npm --prefix frontend run format:check && npm --prefix frontend run typecheck && npm --prefix frontend test -- --run && npm --prefix frontend run build`, matching `ci.yml`'s frontend job); and, if the E2E stack can run locally, `frontend/e2e/admin.spec.ts` at least, since it exercises `promote_admin.py`. Run the integration suite twice in a row from a truncated database and confirm 0 rows in the five checkpoint tables afterwards, as phase 5 did.

- [ ] **Step 5: Commit** `docs(db): record phase 6 outcome` (plus the docs commits from Step 2, one per area if large).

- [ ] **Step 6: Push and open ONE PR — only after the CI minute cycle has reset** (Balaji confirms). Title: `SQLAlchemy migration phase 6: remove Prisma, asyncpg and Node.js`. Body: the Decisions as approved, the before/after image sizes, the "production database untouched" statement with the reason (production is already past 0001), and the Codex sequencing. End with the attribution line from the session's system reminder. Wait for the checks; if one fails, fix it locally and push once.

- [ ] **Step 7: Merge only with Balaji's go-ahead** (merging deploys to production). After the deploy: the workflow log shows the guard `ok`, `alembic upgrade head` with no new revision (or only 0005), and `alembic current` at head. Check that `/ready` is healthy, that one run completes and one approval run pauses and resumes (the same manual check as phase 5), and that the backend logs have no new errors for 24 h. Delete the container (`docker stop composer-p6-pg && docker rm composer-p6-pg`) and the local `composer-backend:p6-*` images.

---

## Phase 7 preview (cleanup, about 2026-12-09: two weeks after phase 5 went live on 2026-11-25)

One Alembic revision, plus deletions:

- **Revision `000N_drop_legacy_tables`** (the next free number after whatever is head then; `down_revision` = that head): `DROP TABLE langgraph_checkpoint_writes`, `DROP TABLE langgraph_checkpoints`, `DROP TABLE _prisma_migrations`, with the same `SET LOCAL lock_timeout = '5s'` / `statement_timeout = '60s'` as 0002–0004. **Preconditions, checked in the upgrade:** no `queued`/`running`/`waiting_approval` execution whose newest checkpoint is in the old tables only. This is the guard's query, run inside the migration so the drop fails closed. Downgrade: recreate the two checkpoint tables empty (their DDL is in 0001's SQL) and recreate `_prisma_migrations` empty. Data is not restored; the runbook says so.
- **Code deleted:** `src/checkpoints/legacy.py`, `copy.py`, `versions.py`, `sync.py` (the guard and `bring_forward`), the `composer checkpoints` CLI (`src/cli/checkpoints.py` and its `main.py` wiring), the legacy half of `src/db/repos/checkpoints.py` (`delete_threads` then clears only the three saver tables, and its drift test stays), the `LangGraphCheckpoint` / `LangGraphCheckpointWrite` models, `tests/fixtures/legacy_checkpoint_rows.json`, the copy and guard tests, `_LEGACY_TABLES` in `tests/_checkpoint_reset.py`, the guard step in `deploy-gcp.yml` and its assertions in `tests/unit/test_deploy_workflow.py`, and `_prisma_migrations` in `src/db/schema_filters.py` (plus `tests/unit/db/test_schema_filters.py`).
- **Adoption rule (D4) after phase 7:** a restore from before 2026-11-20 still carries `_prisma_migrations`, so 0001's adoption still works for it. Only production loses the table, and production is past 0001. **Never re-run a pre-phase-6 deploy workflow** after phase 7: its `prisma migrate deploy` would find a non-empty schema without `_prisma_migrations` and fail (loudly, so no data is at risk). The runbook should say so.
- Docs: the production-deployment "Checkpointer cutover" section is retired, the spec's phase 7 row is marked done, the CHANGELOG is updated, and ADR-0036 gets a note.

## Follow-ups (not in this phase)

- Remove the Prisma-only query parameters from the `composer-database-url` secret, then remove their stripping from `src/db/url.py` (D10).
- The `# syntax=docker/dockerfile:1.7` line in the `Dockerfile` sits after comment lines, so BuildKit ignores it as a parser directive. Move it to line 1 (behaviour-neutral today, because nothing in the file needs 1.7 features), or delete it.
- If Task 5 Step 9 was skipped (Codex had not merged): comment cleanups in `src/api/executions.py`, `src/api/internal.py`, `tests/unit/engine/test_langgraph_executor.py`, `tests/unit/api/test_internal.py` and `tests/integration/sqla_phase4a/conftest.py`.
- `scripts/` is outside pyright's `include`. Consider adding it (Task 5's tests cover `seed_templates` behaviourally, not its types).

## Risks

| Risk | Mitigation |
|---|---|
| 0001's DDL differs from what existing databases have, so fresh databases (CI, new environments, restores into empty databases) drift from production | The SQL is Prisma's own files verbatim (byte-equality test while `prisma/` exists, then a fingerprint). Golden catalogs from a Prisma-built database are compared at 0001 and 0004. The head-parity test runs on a scratch database built by Alembic alone. Opus review gate. |
| The adoption check misjudges a database: it runs DDL on a non-empty schema, or adopts a half-migrated one | DDL runs only when `public` has no tables except `alembic_version`. Adoption needs all 20 names, finished and not rolled back. Everything else raises with an actionable message. One test per branch. |
| A database at 0001–0003 (Neon branch, dev database) breaks | D2(A) changes no ids, and such a database never re-runs 0001. The 0002/0003/0004 up/down tests still pass. |
| Merge-order conflict with Codex's 0005 | D2(A) is order-independent. The goldens pin 0001/0004. Codex merges first (D9), and phase 6 avoids Codex's files until the rebase. |
| E2E breaks because `promote_admin.py` still imports Prisma | Ported in Task 5 with a subprocess test. The done grep covers `frontend/scripts`. Run `admin.spec.ts` locally if possible. |
| The image build breaks without the apt layer (CA certificates, curl) | `uv` is copied from its image and brings its own TLS roots; the slim base has `ca-certificates`. Local `docker build`, plus a container `/ready` smoke and a local Trivy run, before CI. |
| CI minutes: a red PR costs a second ~45-minute run that this cycle cannot afford | Everything verified locally (Task 8 Step 4, including the frontend and, if possible, E2E). PR opened only after the cycle resets. One push. |
| A removed `app.state.db` mock was not actually dead | No `src` code reads `app.state.db` (grep on 2026-11-26). A test that fails after removal is a stop-and-report, not a re-add. |
| psycopg async on Windows (Proactor loop) in the test reset helpers or the scripts | The scripts use `asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)`, as `env.py` does. The tests run on the loop the existing psycopg-based tests already use. |
| Golden catalog text differs across Postgres major versions (for example `pg_get_constraintdef` formatting) when run on Neon | Goldens were captured on Postgres 15, and CI runs Postgres 15. If a Neon run differs, compare semantically before suspecting the baseline; never regenerate the goldens without Prisma. |
| A rollback is needed after the phase 6 deploy | The code revert is enough: the database is untouched. The previous image still has the Prisma client but never imports it. Re-running the old deploy workflow works until phase 7 (`_prisma_migrations` is still present). |

## Self-review notes (writing-plans checklist)

- **Spec coverage:** §3.3 phase 6, "Prisma client and schema" (Tasks 3–6; `prisma/` deleted in Task 6); "Node.js and asyncpg out of the image" (Tasks 5–7; asyncpg removed from the dependencies and from test code, Node.js from the builder; measured in Tasks 1 and 7); "remaining Prisma-shaped mocks" (Tasks 3–5; boundary test in Task 6); "squashed Alembic base" (Task 2, D2–D4; the deviation in form is recorded in the spec and ADR-0037, Task 8). §3.2 fresh databases come from Alembic alone (Task 2; CI exercises it from Task 7 on). Divergence notes: the base is generated from Prisma's migrations as the state *at 0001*, and 0002/0003 apply the divergences; the goldens come from a live database, never from `schema.prisma`. The "base must include the saver tables and the ten `checkpoint_migrations` rows" requirement (phase 5 plan) is met by the chain: fresh builds run 0004, and `golden_0004` checks the rows. §5.2 overall done: Task 8 Step 1 greps; docs in Task 8 Step 2. Phase 7 preview included.
- **Placeholders:** `FROZEN_FINGERPRINT` is computed in Task 2 Step 6, which gives the command; the SQL tuple is generated by a stated mechanical rule and checked by an equality test; "2026-11-xx" in the CHANGELOG heading is the merge date, which is unknown today. No TBDs.
- **Names used across tasks:** `snapshot`, `dump`, `IGNORED` (Task 1, used in Task 2); `PRISMA_MIGRATIONS`, `PRISMA_MIGRATION_NAMES`, `_statements`, `_base_state` (Task 2, used in Tasks 2 and 6); `Rows` with `add/get/where/count/set/remove/drop_threads/sessionmaker` and the `rows` fixture (Task 3, used in Tasks 4 and 5); `seed`, `_upsert_template` (Task 5); `promote` (Task 5); `SCANNED_ROOTS`, `FORBIDDEN_ROOTS`, `_writes_app_state_db` (Task 6).

## Outcome (Tasks 1 to 8, docs half)

Status: code Tasks 1 to 7 are committed on branch `feat/sqlalchemy-phase-6`; Task 8 Steps 1, 2 and the docs commits of Step 5 are done. Steps 3 (rebase), 4 (full CI-equivalent run), 6 (push and PR) and 7 (merge) are pending and need Balaji. PR number: pending.

**What changed against the plan**

- **Decisions D1 to D10 were implemented as approved.** D2(A) holds: no revision id changed, `uv run alembic heads` prints exactly one head (`0004_langgraph_saver_tables` until Codex's `0005_execution_attempts` is rebased in).
- **Task 5 Step 9 (comment cleanups in files the telemetry work edits) was skipped, as planned for "Codex has not merged".** These Prisma-history comments remain and are listed under Follow-ups: `src/api/executions.py:430` ("via Prisma `onDelete: Cascade`"), `src/api/internal.py:241` and `:573`, plus the comments in `tests/unit/engine/test_langgraph_executor.py`, `tests/unit/api/test_internal.py` and `tests/integration/sqla_phase4a/conftest.py`. `feat/telemetry-phase-5-attempts` changes `src/api/executions.py` (23 insertions, 11 deletions against main), so the `executions.py:430` fix was deliberately not made here.
- **Review-gate findings that had to land in this PR (merging deploys), all documentation:** `disaster-recovery.md` told operators to pause `prisma migrate deploy`; `incident-response.md` carried a `from prisma import Prisma` sweep script; `production-deployment.md`, `gcp-cloud-run-setup.md` and `postgres-setup.md` still described `prisma generate` / `migrate` / `status`. All now use Alembic (`composer checkpoints guard`, then `alembic upgrade head`, then `alembic current`, on the direct non-pooler host). The rollback section of `production-deployment.md` gained "Rolling back across the Prisma removal": an older image is a revision swap with no database action; an old deploy workflow still works until phase 7; once a later revision such as `0005` is in production an older image reports `/ready` 503 `schema=mismatch` but keeps serving (the startup probe is TCP), and an old workflow fails closed at `alembic upgrade head`. A note records that the image ships no `alembic.ini`, so the `alembic` CLI is not runnable inside it.
- **`CLAUDE.md` (D8):** stack table ORM and driver rows, development commands (all Prisma lines removed) and Phase 0 gotchas 1 and 2 (gotcha 1 is now the refused `alembic downgrade base`; gotcha 2 is a historical stub so the numbers other docs cite stay valid). The phase-status rows that mention Prisma historically and the rest of the file are unchanged.

**Step 1 done-criterion greps (run on `feat/sqlalchemy-phase-6`)**

- `grep -rnE "^\s*(from|import) (prisma|asyncpg)\b" src tests scripts frontend/scripts`: no output.
- `grep -rn "NEEDS_LEGACY_PRISMA" tests`: one hit, `tests/unit/db/test_prisma_import_boundary.py:87`, which is the guard that fails if the marker comes back (plus a stale `.pyc`).
- `grep -rnE "app\.state\.db\s*=" tests`: one hit, `tests/unit/db/test_prisma_import_boundary.py:93`, the detector's own self-test string.
- `grep -rni "prisma" Dockerfile .dockerignore .github/workflows pyproject.toml uv.lock`: two hits, `pyproject.toml:18-19`, a comment saying SQLAlchemy replaced Prisma Python (kept as history).
- `grep -rniE "nodesource|nodejs" Dockerfile`: no output.
- `grep -rn "asyncpg" pyproject.toml uv.lock tests/_rate_limit_reset.py tests/_checkpoint_reset.py`: no output.
- `test ! -e prisma`: prints "prisma/ gone". `uv run alembic heads`: one head, `0004_langgraph_saver_tables`.
- `grep -rni prisma src | grep -v -e "src/db/" -e "src/checkpoints/legacy.py"`: only `src/api/executions.py:430` and `src/api/internal.py:241,573` (the skipped Step 9 files).

**Measurements (Task 1 / Task 7)**

- Test counts before: not recorded in this docs pass (pending the final run).
- Image sizes before and after (runtime and builder), `.prisma-cache` size and the `node` result: pending the Task 7 local build; the CHANGELOG says "measured at release".

**Pending (explicitly not done here)**

- Codex rebase result: **D9 reversed by Balaji on 2026-11-27.** Phase 6 merges first because Codex's telemetry phase 5 will take several more sessions. Codex's `feat/telemetry-phase-5-attempts` branch rebases onto phase 6 instead. `0005`'s `down_revision` (`0004_langgraph_saver_tables`) is unaffected. Task 5 Step 9 (comment cleanups in Codex's files) moves to Codex's rebase, or becomes a follow-up after it.
- Final full-run counts (ruff, pyright, unit and integration pytest, frontend checks, E2E `admin.spec.ts`): pending (Task 8 Step 4, run by Balaji).
- Push, PR and merge (Steps 6 and 7): pending Balaji's go-ahead.
