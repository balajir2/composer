# SQLAlchemy Migration Phase 7 — Cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Prisma → SQLAlchemy migration. One Alembic revision, `0006_drop_legacy_tables`, drops the two old LangGraph checkpoint tables (`langgraph_checkpoints`, `langgraph_checkpoint_writes`) and Prisma's ledger (`_prisma_migrations`). The code that only existed to bridge the old tables goes with them: `LegacyCheckpointSaver`, the copy tool, the deploy guard, copy-on-resume (`bring_forward`), the legacy models and repo functions, the guard step in the deploy workflow, and the test helpers and tests that seed the old tables.

**Architecture:** The revision locks the two old tables, then checks, inside the migration transaction, that no `queued` / `running` / `waiting_approval` execution has a newest checkpoint that exists only in the old tables (the deploy guard's query, frozen into the revision). If one does, the revision refuses: nothing is dropped and the deploy stops before `gcloud run deploy`. Otherwise it drops the three tables. Its downgrade recreates the two checkpoint tables empty with the exact DDL from `0001_baseline`. It does not recreate `_prisma_migrations` (D3). Code removal and the revision ship in ONE PR and ONE deploy. That PR also carries the `composer watch` back-off and the Dependabot catch-up. The swap window and the rollback path are analysed in "Expand/contract analysis" below. The old image tolerates neither missing table on resume or delete, so the plan contains the risk operationally (D11) and makes rollback safe by running the downgrade first (D12).

**Tech Stack:** SQLAlchemy 2.1 async + psycopg 3.3, Alembic 1.20, PostgreSQL 15, `langgraph-checkpoint-postgres` 3.2.1, FastAPI, pytest + pytest-asyncio, pyright strict, ruff, uv 0.8.15.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md): §3.3 phase 7 row; §3.4 ("Deletes … Until phase 7 they also clear the old tables"; "The old tables are kept untouched until phase 7"); §7 risks row "A paused run can't resume after the checkpointer cutover". Previous plans: [phase 5](2026-11-25-sqlalchemy-phase-5-checkpointer-and-notifications.md) (D5 guard, D6 rollback, D9 legacy saver; "After phase 5: what phase 6 removes", whose last paragraph lists phase 7's deletions) and [phase 6](2026-11-26-sqlalchemy-phase-6-removal.md) ("Phase 7 preview" and Outcome). ADRs: ADR-0035, ADR-0036, ADR-0037 in `docs/decisions.md`.

**Starting point (verified on `chore/phase7-deps-watch` at `6ce5495`, 2026-11-28):** Alembic head `0005_execution_attempts`, which is also production's revision. Phase 5 went live on 2026-11-25 and phase 6 on 2026-11-27. On the last deploy the guard printed "ok: no active run has checkpoints only in the old tables". Production has no paused runs. Composer has a single user. Phase 7 runs 11 days earlier than the planned ~2026-12-09, at Balaji's request. He accepts that a pre-phase-6 deploy workflow cannot be re-run after `_prisma_migrations` is gone. That was already true once `0005` reached production: such a workflow fails at `alembic upgrade head`. Rolling back to an older image stays possible (D12).

## Global Constraints

All Global Constraints from phases 1–6 apply unchanged:

- Repos are the only SQLAlchemy query code in `src`. They are imported as modules, return frozen-dataclass records, and never commit.
- Short-session rule: never hold a SQLAlchemy session across a graph run, a saver call, an LLM call, network I/O, `asyncio.sleep` or a WebSocket wait.
- Fail closed. Never log secrets, connection strings, thread ids or execution payloads. Log exception class names, execution ids and counts only.
- CI-parity checks before every commit: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`.
- Commits are authored by Balaji Rajan `<balajirajan@gmail.com>` (verify with `git log -1 --format='%an <%ae>'`) and end with exactly one line `Co-Authored-By: <the model that actually did the work> <noreply@anthropic.com>`.
- Never touch `D:/GitHub/open-agent-builder` (frozen, read-only).

Additionally, for this phase:

- **Policy files change only in the task named for them, and only after Balaji has confirmed the decision listed:**
  - `src/db/migrations/versions/0006_drop_legacy_tables.py` (new): Task 2 only (D1–D4).
  - `.github/workflows/deploy-gcp.yml`: Task 6 only (D8). The Dependabot catch-up in the same PR may bump action SHAs in this file. Keep the two edits in separate commits and resolve any overlap by hand; never drop a SHA bump.
  - `CLAUDE.md`: Task 7 only (D13).
  - `src/db/migrations/versions/0001`–`0005`: **never edited.** 0004's docstring and downgrade message mention `composer checkpoints copy --to legacy`. That is history, and the runbook (Task 7) explains where the tool now lives (`git checkout 6ce5495`).
  - `pyproject.toml` / `uv.lock`: **not touched by phase 7.** No dependency becomes unused: `langgraph-checkpoint` is still needed by the official saver. The Dependabot catch-up owns these files in this PR.

  If a task hits an error it can only fix by editing a policy file outside its list, it stops and reports BLOCKED.
- **Revision ids never change.** After Task 2, `uv run alembic heads` prints exactly one head: `0006_drop_legacy_tables`. If the Dependabot catch-up bumps `langgraph-checkpoint-postgres` to a release whose `MIGRATIONS` list grew, `tests/unit/checkpoints/test_library_contract.py` fails and the bump needs its own saver revision (`0007_…`, `down_revision = "0006_drop_legacy_tables"`). That is out of scope here: stop and report.
- **Shared PR.** The same branch carries the `composer watch` back-off (`src/cli/watch.py`, `tests/unit/cli/test_watch.py`, `CHANGELOG.md`, `docs/designer-guide.md`) and the Dependabot catch-up (`pyproject.toml`, `uv.lock`, possibly workflow action SHAs). Either may be committed or still uncommitted when phase 7 starts, so check `git status` first. Phase 7 never edits `src/cli/watch.py`, `tests/unit/cli/test_watch.py`, `pyproject.toml` or `uv.lock`. It edits `src/cli/main.py` (Task 5) only in the `checkpoints` subparser, the dispatch and the docstring line. Phase 7 commits stage only their own files (`git add <paths>`, never `git add -A`). Its `CHANGELOG.md` entry is a separate section placed above the watch entry.
- **CI minutes are scarce.** Everything is verified locally against a throwaway Postgres 15 container. CI runs once, on the shared PR.
- **Local database** (Docker must be running; it was down when this plan was written):
  ```bash
  docker run -d --name composer-p7-pg -e POSTGRES_PASSWORD=composer -e POSTGRES_USER=composer \
    -e POSTGRES_DB=composer_test -p 55434:5432 postgres:15
  export TEST_DATABASE_URL=postgresql://composer:composer@localhost:55434/composer_test
  DATABASE_URL=$TEST_DATABASE_URL .venv/Scripts/python -m alembic upgrade head
  ```
  The container's user is a superuser, as in CI, so the scratch-database tests in `tests/integration/db/test_baseline_schema.py` can `CREATE DATABASE`.
- **Tool paths:** this machine's shell has no `uv` or `python` on `PATH`. Use `.venv/Scripts/python -m <tool>` and `"C:/Users/Balaji Rajan/AppData/Roaming/Python/Python313/Scripts/uv.exe"`. Commands below say `uv run …` for readability. Always prefix pytest with `LANGSMITH_TRACING=false` on the same line.
- **Run `ruff format --check`**, not only `ruff check`: CI has failed on format alone before.

## Decisions for Balaji (each with a recommendation)

**Confirmed by Balaji on 2026-11-28:** D3, D5, D6, D8 and D13 accepted as recommended. D11: one PR; before merge, no runs queued, running or waiting; no approvals or deletes until the `/ready` smoke test passes; the post-deploy query runs afterwards.

**Needs confirmation:** D3, D5, D6, D8, D11, D13 (marked **[confirm]**). The other decisions follow directly from the spec, the phase 6 preview or the code, and are recorded so the reviewer can check them.

| # | Topic | Recommendation |
|---|---|---|
| D1 | **Revision scope and id.** The phase 6 preview names `000N_drop_legacy_tables`, with N the next free number. | **`0006_drop_legacy_tables`**, `down_revision = "0005_execution_attempts"`; the id is 23 characters, within `alembic_version.version_num`'s 32. It drops exactly `langgraph_checkpoint_writes`, `langgraph_checkpoints` and `_prisma_migrations`. Nothing else belongs to them: in `0001_baseline` the old tables own only their primary keys and one index each (`langgraph_checkpoints_thread_id_idx`, `langgraph_checkpoint_writes_thread_id_checkpoint_id_idx`), with no foreign keys, sequences, enums or functions. The goldens confirm this. The two checkpoint tables are dropped with plain `DROP TABLE`. Every database at 0005 has them (0001 creates them), so if one is missing the schema has drifted and the revision must fail. `_prisma_migrations` is dropped with `DROP TABLE IF EXISTS`: databases built by Alembic alone (CI, every fresh database since phase 6) never had it, while production and databases adopted from Prisma do. |
| D2 | **Guard inside the migration.** | **Lock, check, then drop, all in the migration's single transaction** (`env.py` wraps every pending revision in one transaction). Step 1: `LOCK TABLE langgraph_checkpoint_writes, langgraph_checkpoints IN ACCESS EXCLUSIVE MODE`. The order matches `delete_threads` (writes, then checkpoints), so the lock cannot deadlock with an old-image delete. Once the locks are held nothing can add old-table rows between the check and the drop. Only `LegacyCheckpointSaver` ever wrote them, through the copy CLI, never the serving path. Step 2: the deploy guard's query, frozen into the revision (no import from `src`, so the revision stays valid when app code moves). It finds executions in `queued`/`running`/`waiting_approval` with a `(thread, namespace)` whose newest old-table `checkpoint_id` (`COLLATE "C"`) is missing from, or newer than, the newest in `checkpoints`. That is the user's "checkpoints exist only in the old tables", plus the "newer in old" case the guard also refused, because there too the newest state would be lost. Step 3: if any rows match, raise `RuntimeError` naming up to 20 execution ids with their statuses, the total, and the fix: `git checkout 6ce5495 && uv run composer checkpoints copy --to postgres --execution <id>` (the last commit with the copy tool; it works against a database at 0005 because the refusal left everything in place), or cancel the run, then re-run the deploy. Thread ids and checkpoint contents are never printed. Step 4: the drops. Before the check, print one line, `0006: active runs queued=<n> running=<n> waiting_approval=<n>`, so the deploy log keeps the record the guard step used to print. |
| D3 **[confirm]** | **Downgrade.** Recreate the two checkpoint tables? Recreate `_prisma_migrations`, empty or with its 20 rows? | **Recreate the two checkpoint tables empty, with `0001_baseline`'s exact statements (copied verbatim; a unit test asserts each one is a substring of 0001's first Prisma migration). Do NOT recreate `_prisma_migrations`.** Data is not restored. The docstring and the runbook say so. Why the checkpoint tables: the image currently in production (`6ce5495`) queries them on every resume and every delete. With them present and empty its `bring_forward` finds nothing and returns, so a rollback works (D12). Why not `_prisma_migrations`: (1) only `0001_baseline._base_state` reads it, and that runs only on a database with no `alembic_version` row. A database being downgraded always has one, so the table is never needed. (2) No image reads it. `git grep _prisma_migrations 58d2829 -- src` (the last pre-phase-6 commit) hits only the Alembic autogenerate filter. (3) Pre-phase-6 deploy workflows already fail at `alembic upgrade head`, because `0005` is not in their history, so restoring the ledger would not revive them. (4) An EMPTY ledger is worse than none: a pre-phase-6 `prisma migrate deploy` would see no history and try to re-apply all 20 migrations, failing part-way and leaving a P3009 "failed migration" row. With no ledger it refuses cleanly (P3005, schema not empty). (5) Twenty rows with invented ids, timestamps, logs and checksums would be fabricated history. (6) Databases built by Alembic alone never had the table, so recreating it would make a downgraded fresh database differ from a fresh build at 0005. The phase 6 preview said "recreate `_prisma_migrations` empty". This decision deliberately reverses that, for reasons (1)–(6), and records the reversal. |
| D4 | **Locks and timeouts on production.** | **Same as 0004/0005:** `SET LOCAL lock_timeout = '5s'` and `SET LOCAL statement_timeout = '60s'`, in a `_bound_locks()` helper called first in both `upgrade()` and `downgrade()`. The upgrade takes ACCESS EXCLUSIVE on the two old tables (and on `_prisma_migrations` when dropping it). The only live lockers are the old image's `bring_forward` (ACCESS SHARE for one SELECT) and `delete_threads` (ROW EXCLUSIVE for one DELETE), each held for milliseconds. If one is in flight past 5 s, the migration fails with "lock timeout", nothing changes, and re-running the deploy job is safe. The guard query reads `workflow_executions` and `checkpoints` with ACCESS SHARE, which blocks no writer. Expected duration on production is well under a second. The old tables hold only pre-2026-11-25 history plus nothing new, so the `GROUP BY` scan is small. `CONCURRENTLY` is not applicable to drops. A test proves that a held lock fails the upgrade fast and leaves everything in place (Task 2). |
| D5 **[confirm]** | **`composer checkpoints` CLI: delete it, or reduce the guard to a no-op?** | **Delete the whole subcommand** (`src/cli/checkpoints.py`, the `checkpoints` subparser and dispatch in `src/cli/main.py`, and the docstring line). A no-op guard has no caller once the deploy step goes (D8), and a no-op that prints "ok" is a false assurance. The only workflow that would call it is a pre-phase-7 one, re-run from an old ref. Its own checkout still has the real guard, which fails closed with `failed: UndefinedTable` once the tables are gone (see "Expand/contract analysis"). `composer checkpoints …` then exits 2 with argparse's "invalid choice". A unit test pins that (Task 5). |
| D6 **[confirm]** | **Failure codes `checkpoint_sync_failed` / `checkpoint_missing`.** After `bring_forward` goes, the `try` around it in `LangGraphExecutor.resume` guards only `_has_checkpoint`, a probe read of the official saver. | **`checkpoint_missing` stays unchanged:** a resume with no checkpoint still fails closed. **`checkpoint_sync_failed` keeps its wire value but is re-scoped to "reading the run's saved state failed at the start of a resume; the graph did not run and the state is intact".** The Python exception is renamed `CheckpointSyncFailed` → `CheckpointReadFailed`. The guidance in `src/api/execution_observability.py` changes from "could not be saved" (already inaccurate) to "Execution state could not be read. Contact an administrator.", and `docs/api-reference.md`, `docs/telemetry/signal-catalog.md` (line 356) and the runbook are reworded. The value stays in `src/telemetry/attributes.py` `VOCABULARIES["failure_code"]` and in the catalog's JSON `failure_code` list, which a test keeps equal to that vocabulary. Three reasons: production rows may already carry it; a rolled-back old image emits it (D12); and a distinct "state intact, retry" signal is more useful to an operator than `execution_error`. Alternatives: (B) stop emitting it, so probe failures become `execution_error` and the value stays historical; (C) add `checkpoint_read_failed`, which means vocabulary, catalog, guidance, API doc and monitoring changes. (A) is the smallest accurate change. |
| D7 | **Legacy models, repo functions, saver, copy and versions.** | **Delete:** `src/checkpoints/legacy.py`, `copy.py`, `versions.py` and `sync.py`; the `LangGraphCheckpoint` / `LangGraphCheckpointWrite` models (and their `src/db/models/__init__.py` exports); from `src/db/repos/checkpoints.py`, everything except `SAVER_THREAD_TABLES`, `_SAVER_MODELS` and `delete_threads`. That removes `ACTIVE_STATUSES`, `LegacyCheckpointRow`, `LegacyWriteRow`, `_to_row`, `legacy_get`, `legacy_list`, `legacy_writes`, `legacy_upsert_checkpoint`, `legacy_upsert_writes`, `legacy_namespaces`, `saver_namespaces`, `waiting_threads`, `UnsyncedRun`, `_NEW_NEWEST*`, `_unsynced_from*`, `_OLD_AHEAD`, `unsynced_active_runs`, `legacy_ahead_namespaces`, `legacy_ahead` and `active_status_counts`; only the CLI, `sync.py`, `legacy.py` and tests use them (grep in Task 5). `delete_threads` clears only the three saver tables. This is the change behind "the delete paths in `src/api/executions.py`": `DELETE /executions/{id}` and `POST /executions/delete-bulk` reach the old tables only through `executions_repo.delete_terminal` → `checkpoints_repo.delete_threads`, so no edit to `src/api/executions.py` is needed beyond its docstring (lines 435–439). Its drift test against the library's migrations stays. `src/checkpoints/` keeps `__init__.py`, `runtime.py`, `saver.py` and `schema.py`. |
| D8 **[confirm]** | **Deploy workflow guard step.** It runs from the merged commit's checkout, before `alembic upgrade head`, while the old tables still exist. | **Remove the guard lines and their comment from "Apply database migrations"** (`deploy-gcp.yml` lines 160–167: the comment block and `uv run composer checkpoints guard`). The step must go in the same PR as the CLI deletion, because the deploy that runs 0006 uses this PR's checkout, where the command no longer exists. Keeping it is impossible (D5), and the migration's own check (D2) replaces it for this deploy: same query, same fail-closed effect (the job fails before `gcloud run deploy`, the previous revision keeps serving), but atomic with the drop. Keep `DB_CONNECT_TIMEOUT_SECONDS: "15"`, rewording its comment: Alembic is now the first connection, and `env.py` already enforces a 15 s floor, so the variable is belt and braces. `tests/unit/test_deploy_workflow.py` is updated: the guard test becomes "migrations run `uv sync --frozen` → `alembic upgrade head` → `alembic current` before the deploy, with no `composer checkpoints` line, no `prisma`, no `continue-on-error`, no `\|\| true`". |
| D9 | **`_prisma_migrations` in schema filters.** | **Remove it from `src/db/schema_filters.py` `IGNORED_TABLES`** (it becomes `frozenset({"alembic_version"})`, with its comment reworded). At head the table never exists, so the filter only hid it from autogenerate. **Keep it in `tests/integration/db/schema_catalog.py` `IGNORED`**, with a comment: the adoption tests build `_prisma_migrations` by hand and compare against the 0001/0004 goldens at those revisions, where it still exists. 0001's own uses (`_base_state`) are untouched (frozen file). `tests/unit/db/test_schema_filters.py` flips its assertion: `include_name("_prisma_migrations", "table", {}) is True` (no longer special). |
| D10 | **Goldens and schema-parity tests.** `golden_0001_baseline.json` and `golden_0004_langgraph_saver_tables.json` include the old tables. | **Leave both goldens unchanged; add no `golden_0006`.** The goldens pin revisions 0001 and 0004, and the tests migrate to exactly those revisions (`BASE_HEAD = "0004_langgraph_saver_tables"`), where the old tables do exist. That stays true. Head is pinned by the models: `test_fresh_database_at_head_matches_the_models` (scratch database) and `test_models_match_the_migrated_database_exactly` (test database) compare `Base.metadata` with the database. Once the legacy models and the tables are gone together, both still return no differences, and if either side is missed they fail. New tests (Task 2) add: (a) a fresh database at head has none of the three tables; (b) a Prisma-adopted database loses `_prisma_migrations` at head; (c) 0006's downgrade reproduces the fresh-0005 catalog exactly; (d) the two recreated tables equal golden_0004's entries for them. A head golden would need regenerating with every future revision and would add nothing the model comparison does not cover. |
| D11 **[confirm]** | **Swap-window risk (see "Expand/contract analysis").** For the minute or two between 0006's commit and Cloud Run moving 100% of traffic to the new revision, the old revision serves against a database without the old tables. A resume that starts on it fails the run with `checkpoint_sync_failed`, and a delete returns 500. | **Accept it, with operational containment, and ship one PR.** (1) Pre-merge check: zero `waiting_approval`, `running` and `queued` executions (SQL in Task 8). Single user, no paused runs today. (2) During the deploy job, do not approve, reject or delete anything until "Smoke test /ready" has passed. (3) Post-deploy check: no run failed with `checkpoint_sync_failed` since the job started (SQL in Task 8). Recovery for a hit: start a new run (no state surgery). The alternative is a true expand/contract in two PRs: first the code removal, which makes the deployed image tolerant (needs a separate deploy), then 0006. It removes the window entirely but costs a second CI run and deploy (about 45 minutes of scarce CI). Not worth it for one user with no paused runs. A stricter in-migration precondition ("refuse while any execution is active") was also considered and rejected: it would block dev and Neon-branch databases with stale active rows, and break `test_alembic_0005`, which upgrades with active rows present. The pre-merge check gives the same protection for the one production run. |
| D12 | **Rollback after 0006.** | **The downgrade first, then the image.** Runbook (Task 7): from a checkout of the phase 7 merge commit, using the direct `DATABASE_URL`, run `uv run alembic downgrade 0005_execution_attempts`. That recreates the two tables empty, and is safe while the new image serves, because the new image never touches them. Then shift traffic to the previous revision. The old image's `bring_forward` finds empty old tables and returns, and its deletes work. Its `/ready` reports `schema=mismatch` neither before nor after, because the database is back at its head (0005). If the image was swapped first by mistake, run the downgrade immediately: runs resumed in between failed with `checkpoint_sync_failed`, and their users start new runs. Rolling back to a **pre-phase-5** image (one that reads only the old tables) is not supported after phase 7, except by downgrading and then copying new → old with the copy tool from `git checkout 6ce5495`. The runbook says so. |
| D13 **[confirm]** | **`CLAUDE.md` edit** (policy file). | **Stack table, ORM row only:** replace "the migration is complete except phase 7 cleanup (old checkpoint tables, `_prisma_migrations`)" with "the migration is complete (phase 7, `0006_drop_legacy_tables`, dropped the old checkpoint tables and `_prisma_migrations`)". Gotcha 8 ("Never run `AsyncPostgresSaver.setup()`; the saver's tables are Alembic revision 0004") stays. Nothing else in `CLAUDE.md` changes. |
| D14 | **Tests and fixtures that exist only for the bridge.** | **Delete:** `tests/integration/db/checkpoints/` (whole directory: `conftest.py`, `test_copy_cli_smoke.py`, `test_copy_round_trip.py`, `test_guard.py`, `test_guard_cli.py`, `test_legacy_saver.py`, `__init__.py`), `tests/integration/sqla_phase5/test_checkpoint_sync_failure.py`, `tests/unit/checkpoints/test_versions.py`, `tests/unit/cli/test_checkpoints_cli.py` and `tests/fixtures/legacy_checkpoint_rows.json`. **Convert** (seed the saver tables instead, or drop the old-table assertions): the files listed in Tasks 4 and 5. A deleted test whose guarantee still matters moves to a converted test. The only such guarantee is "deletes clear checkpoint rows in the caller's transaction", which `tests/integration/db/repos/test_checkpoints.py` keeps on the saver tables. |

## Every remaining reference (branch at `6ce5495`)

`git grep -nE "langgraph_checkpoint|LangGraphCheckpoint|LegacyCheckpointSaver|bring_forward|_prisma_migrations|checkpoints guard|checkpoints copy|src\.checkpoints\.(copy|sync|versions|legacy)|CheckpointSyncFailed" -- src tests .github` lists the following. Task 8's done grep must show only the "keep" rows.

| Where | What | Fate |
|---|---|---|
| `src/checkpoints/legacy.py`, `copy.py`, `versions.py`, `sync.py` | Old saver, copy tool, version conversion, guard + `bring_forward` | Delete (Task 5) |
| `src/cli/checkpoints.py`; `src/cli/main.py` lines 7, 68–78, 124–129 | `composer checkpoints copy\|guard` | Delete / remove (Task 5) |
| `src/engine/langgraph_executor.py` lines 24, 53–58, 440–470, 539–543 | `bring_forward` import and call, `CheckpointSyncFailed`, docstring | Edit (Task 3) |
| `src/db/repos/checkpoints.py` | Legacy half; `delete_threads` clears five tables | Trim to saver-only (Task 5) |
| `src/db/models/checkpoints.py` lines 1, 13–51; `src/db/models/__init__.py` lines 5–6, 34–35 | `LangGraphCheckpoint`, `LangGraphCheckpointWrite` | Delete (Task 5) |
| `src/db/schema_filters.py` line 7 | `_prisma_migrations` ignored | Remove (Task 4) |
| `src/api/executions.py` lines 435–439 (docstring) | "LangGraph checkpoints + checkpoint_writes" | Reword (Task 5) |
| `src/api/execution_observability.py` line 25 | guidance for `checkpoint_sync_failed` | Reword (Task 3, D6) |
| `src/db/migrations/versions/0001_baseline.py`, `0004_langgraph_saver_tables.py` | DDL, adoption check, docstrings | **Keep** (frozen) |
| `src/db/migrations/versions/0006_drop_legacy_tables.py` | The drop | **Keep** (new) |
| `.github/workflows/deploy-gcp.yml` lines 147, 160–167 | Guard step and comments | Remove / reword (Task 6) |
| `tests/unit/test_deploy_workflow.py` lines 21–43, 57 | Guard order assertions | Rewrite (Task 6) |
| `tests/_checkpoint_reset.py` | Five tables, `_LEGACY_TABLES` | Three tables (Task 4) |
| `tests/integration/conftest.py:29`, `tests/regression/conftest.py:25` | Docstring "all five tables" | Reword (Task 4) |
| `tests/integration/db/schema_catalog.py:19` | `IGNORED` includes `_prisma_migrations` | **Keep** with comment (Task 4, D9) |
| `tests/integration/db/test_baseline_schema.py` | Adoption tests build `_prisma_migrations` | **Keep**; add head tests (Task 2) |
| `tests/fixtures/schema/golden_0001_baseline.json`, `golden_0004_…json` | Old tables at 0001/0004 | **Keep** (D10) |
| `tests/unit/db/test_baseline_frozen.py` | 0001 fingerprint | **Keep** |
| `tests/unit/db/test_schema_filters.py:5` | Asserts `_prisma_migrations` ignored | Flip (Task 4) |
| `tests/unit/db/test_models_metadata.py` lines 18–19, 149–159 | Expected tables; old writes PK order | Edit (Task 5) |
| `tests/integration/db/test_round_trip.py` lines 16–17, 113–157 | Bytes round trip on old tables | Convert to saver tables (Task 5) |
| `tests/integration/db/repos/test_checkpoints.py` | Legacy SQL tests; five-table delete | Trim / convert (Task 5) |
| `tests/integration/db/repos/test_executions.py` lines 15–16, 245–286 | `_artifacts` seeds old tables | Convert (Task 5) |
| `tests/integration/test_durable_execution.py` lines 33–34, 453–533 | Cancel retains old-table rows | Convert (Task 5) |
| `tests/integration/sqla_phase4a/conftest.py` lines 52–53, 540–580 | `add_checkpoint`, `checkpoint_counts` on old tables (used by `test_delete.py`) | Convert (Task 5) |
| `tests/integration/sqla_phase5/conftest.py` lines 72–79 | `CHECKPOINT_TABLES` (five) | Three (Task 5) |
| `tests/integration/sqla_phase5/test_deliberate_changes.py` | Old-table seeding; three cutover tests; `OLD` | Trim (Task 5) |
| `tests/integration/sqla_phase5/test_checkpoint_sync_failure.py` | `bring_forward` failure | Delete (Task 5) |
| `tests/unit/engine/test_resume_checkpoint_sync.py` | `bring_forward` tests + probe tests | Trim and rename (Task 3) |
| `tests/integration/db/checkpoints/**`, `tests/unit/checkpoints/test_versions.py`, `tests/unit/cli/test_checkpoints_cli.py`, `tests/fixtures/legacy_checkpoint_rows.json` | Bridge-only tests | Delete (Task 5) |

Docs listed in Task 7.

## Expand/contract analysis

**Deploy order** (`deploy-gcp.yml`, backend job): build and push the image → "Apply database migrations" (`uv sync --frozen`, `alembic upgrade head` (0006 commits here), `alembic current`) → "Deploy to Cloud Run" (new revision starts, passes its TCP startup probe, gets 100% of traffic) → env-var updates (new revisions of the same image) → "Smoke test /ready". The **swap window** runs from 0006's commit to the traffic shift: roughly the length of `gcloud run deploy`, typically 30–120 s and bounded by the 60 s startup deadline plus the rollout. After the shift, old instances still finish their in-flight requests.

**What the old image (`6ce5495`, serving during the window) does with the tables gone, path by path:**

| Path in `6ce5495` | Touches old tables? | With the tables gone | Harm |
|---|---|---|---|
| `LangGraphExecutor.run` (new runs), saver writes | No: phase 5 moved all writes to the official saver | Unaffected | None |
| `LangGraphExecutor.resume` → `bring_forward` (production's checkpointer is `AsyncPostgresSaver`, so it is not skipped) → `legacy_ahead_namespaces` → `SELECT … FROM langgraph_checkpoints` | Yes, first step of every resume | `UndefinedTable` → caught → `CheckpointSyncFailed` → `_mark_failed(failure_code="checkpoint_sync_failed")`: the run becomes terminal `failed`. Its official checkpoint is intact, but the API cannot resume a failed run | **Loses that paused run** (user starts a new one) |
| A resume that started before 0006 committed | Already past `bring_forward`; later steps never touch the old tables | If its SELECT still holds ACCESS SHARE, 0006 waits up to 5 s, then fails the migration (lock timeout), and the deploy can be re-run | None |
| `DELETE /executions/{id}`, `POST /executions/delete-bulk` → `delete_terminal` → `delete_threads` → `DELETE FROM langgraph_checkpoint_writes …` | Yes | `UndefinedTable` → whole transaction rolled back → HTTP 500, nothing deleted | Deletes unavailable; retry later |
| `LegacyCheckpointSaver`, `composer checkpoints` | CLI only, not in the serving path | n/a | None |
| Startup, `/ready`, saver schema check | No (`alembic_version` and `checkpoint_migrations` only) | Instances started in the window report `/ready` 503 `schema=mismatch` (head 0005 ≠ 0006) but serve, because the startup probe is TCP | None |
| Sweeper, claim, cancel, approvals, events, WebSocket | No | Unaffected | None |

The current image cannot be changed retroactively. So the window is safe exactly when no resume starts on an old instance during it. That needs a run in `waiting_approval` approved during the window, or a resume delivery (an approved run in `running` waiting for its Cloud Tasks claim) delivered during it. With zero active runs before merge and no approvals during the job (D11), neither can happen. The post-deploy SQL proves it after the fact.

**Rollback after 0006** (an image at head 0005 serving a database at 0006): resumes fail their runs (`checkpoint_sync_failed`) for as long as that state lasts, deletes return 500, and `/ready` is 503 `schema=mismatch` while traffic flows. That is not a crash, but it does lose paused runs. Mitigation (D12): `alembic downgrade 0005_execution_attempts` BEFORE the traffic shift. The downgrade recreates both tables empty with the exact original DDL, so every old-image path finds what it expects: `bring_forward` sees no old rows and returns, and `delete_threads` deletes nothing from them. The new image ignores the tables, so the downgrade is safe while it still serves. A re-run of the old (`6ce5495`) deploy workflow against a database at 0006 fails closed at its guard step (`failed: UndefinedTable`, exit 1) before touching anything. After the downgrade it works again: guard ok, `alembic upgrade head` is a no-op at 0005.

## File structure

| Path | Change | Task |
|---|---|---|
| `src/db/migrations/versions/0006_drop_legacy_tables.py` | **New** revision | 2 |
| `tests/integration/db/test_alembic_0006_drop_legacy_tables.py` | **New**: round trip, refusal, lock timeout | 2 |
| `tests/unit/db/test_revision_0006.py` | **New**: DDL verbatim from 0001, single head, guard statuses | 2 |
| `tests/integration/db/test_baseline_schema.py` | Add head/adoption/downgrade-parity tests; reword `BASE_HEAD` comment | 2 |
| `tests/integration/db/test_alembic_0005_execution_attempts.py` | Restore with `upgrade head` (no longer 0005) | 2 |
| `src/engine/langgraph_executor.py`, `src/api/execution_observability.py` | Remove `bring_forward`; `CheckpointReadFailed` (D6) | 3 |
| `tests/unit/engine/test_resume_checkpoint_sync.py` → `tests/unit/engine/test_resume_checkpoint_probe.py` | Trim and rename | 3 |
| `tests/unit/api/test_execution_observability.py` | Guidance text | 3 |
| `src/db/schema_filters.py`, `tests/unit/db/test_schema_filters.py`, `tests/integration/db/schema_catalog.py` | D9 | 4 |
| `tests/_checkpoint_reset.py`, `tests/integration/conftest.py`, `tests/regression/conftest.py` | Three tables | 4 |
| `src/checkpoints/{legacy,copy,versions,sync}.py`, `src/cli/checkpoints.py` | **Delete** | 5 |
| `src/cli/main.py`, `src/db/repos/checkpoints.py`, `src/db/models/checkpoints.py`, `src/db/models/__init__.py`, `src/api/executions.py` (docstring) | Trim | 5 |
| Test files in D14 | Delete / convert | 5 |
| `tests/unit/cli/test_main_subcommands.py` | **New**: `checkpoints` is gone | 5 |
| `tests/unit/db/test_legacy_tables_gone.py` | **New**: no reference outside frozen revisions | 5 |
| `.github/workflows/deploy-gcp.yml`, `tests/unit/test_deploy_workflow.py` | D8 | 6 |
| Docs (Task 7 list), `CLAUDE.md`, `CHANGELOG.md`, spec, ADR notes, this plan's Outcome | Record | 7 |

---

### Task 1: Baseline

**Files:** none changed.

- [ ] **Step 1: Confirm the starting point.** `git -C D:/GitHub/composer/.worktrees/phase7-deps-watch log --oneline -1` shows `6ce5495` (or later commits from the watch/Dependabot work on top). `uv run alembic heads` prints `0005_execution_attempts (head)`.
- [ ] **Step 2: Start the container** (Global Constraints) and migrate it: `DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head`, then `uv run alembic current` prints `0005_execution_attempts (head)`.
- [ ] **Step 3: Record test counts before.** `LANGSMITH_TRACING=false uv run pytest -m "not integration" --no-cov -q | tail -3` and `LANGSMITH_TRACING=false uv run pytest -m integration --no-cov -q | tail -3`. Write both counts in the Outcome section. Unit tests on this branch at plan time (with the uncommitted watch changes): see "Plan-time measurements" at the end.
- [ ] **Step 4: Record the reference grep** from "Every remaining reference" into a scratch file (not committed), to diff against Task 8.

### Task 2: Revision `0006_drop_legacy_tables` (needs D1–D4)

**Files:**
- Create: `src/db/migrations/versions/0006_drop_legacy_tables.py`
- Create: `tests/unit/db/test_revision_0006.py`
- Create: `tests/integration/db/test_alembic_0006_drop_legacy_tables.py`
- Modify: `tests/integration/db/test_baseline_schema.py`
- Modify: `tests/integration/db/test_alembic_0005_execution_attempts.py`

- [ ] **Step 1: Write the unit test first** (`tests/unit/db/test_revision_0006.py`):

```python
"""Revision 0006 without a database: its downgrade DDL is 0001's, verbatim, and it is the head."""

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[3]
VERSIONS = ROOT / "src" / "db" / "migrations" / "versions"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, VERSIONS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_downgrade_ddl_is_0001s_verbatim() -> None:
    first_migration_sql = _load("0001_baseline").PRISMA_MIGRATIONS[0][1]
    statements = _load("0006_drop_legacy_tables").CREATE_OLD_TABLES
    assert len(statements) == 4  # two tables, two indexes
    for statement in statements:
        assert statement + ";" in first_migration_sql, statement


def test_0006_is_the_single_head_on_top_of_0005() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert script.get_heads() == ["0006_drop_legacy_tables"]
    assert script.get_revision("0006_drop_legacy_tables").down_revision == "0005_execution_attempts"


def test_guard_covers_every_active_status() -> None:
    module = _load("0006_drop_legacy_tables")
    assert module.ACTIVE_STATUSES == ("queued", "running", "waiting_approval")
    for status in module.ACTIVE_STATUSES:
        assert f"'{status}'" in module.LEGACY_ONLY_ACTIVE_RUNS
```

Run: `LANGSMITH_TRACING=false uv run pytest tests/unit/db/test_revision_0006.py --no-cov -q`. Expected: FAIL (file not found).

- [ ] **Step 2: Write the revision.** `src/db/migrations/versions/0006_drop_legacy_tables.py`. Copy `CREATE_OLD_TABLES` byte-for-byte from `0001_baseline.py` lines 87–97, 100–111, 126 and 129 (the first Prisma migration, `20260420081000_phase_1_execution_engine`), without the trailing `;`. The unit test enforces this.

```python
"""Drop what only the Prisma era used: the old LangGraph checkpoint tables and Prisma's ledger.

SQLAlchemy migration phase 7 (ADR-0035, ADR-0036, ADR-0037). Since phase 5 (2026-11-25) every
checkpoint is written by the official saver (0004's tables). langgraph_checkpoints and
langgraph_checkpoint_writes were kept only so a paused run could be copied between the savers and
a rollback was possible; _prisma_migrations was kept only for pre-phase-6 deploy workflows, which
cannot run against a database at 0005 anyway.

Upgrade, in env.py's single transaction: bound lock waits (5 s) and statements (60 s); lock the
two old tables ACCESS EXCLUSIVE (writes first, the order delete_threads uses); refuse while a
queued, running or waiting_approval execution has a (thread, namespace) whose newest old-table
checkpoint id is missing from, or newer than, the newest in `checkpoints` (the phase 5 deploy
guard's query, frozen here; ids are time-ordered UUIDv6 strings compared COLLATE "C"); then drop
the two tables, and _prisma_migrations if it exists (databases built by Alembic alone never had
it). The refusal names execution ids and statuses only, and the copy tool, which lives at commit
6ce5495 (the last release that has it): the database is untouched, so that checkout's
`composer checkpoints copy --to postgres` still works.

Downgrade recreates the two checkpoint tables EMPTY with 0001's exact DDL (checked by
tests/unit/db/test_revision_0006.py), so an image from before phase 7 (which reads them on every
resume and delete) runs correctly after a rollback. Their rows are NOT restored: restore from a
backup if the history matters. _prisma_migrations is deliberately not recreated: only 0001 reads
it, and only on a database without alembic_version; an empty ledger would make a pre-phase-6
`prisma migrate deploy` re-apply its migrations; invented rows would be fabricated history.

Revision ID: 0006_drop_legacy_tables
Revises: 0005_execution_attempts
Create Date: 2026-11-28
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006_drop_legacy_tables"
down_revision: str | None = "0005_execution_attempts"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

COPY_TOOL_REF = "6ce5495"  # last commit with `composer checkpoints copy`
ACTIVE_STATUSES = ("queued", "running", "waiting_approval")
SHOWN = 20

LEGACY_ONLY_ACTIVE_RUNS = """
SELECT e.id, e.status
FROM (
    SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
    FROM langgraph_checkpoints GROUP BY thread_id, checkpoint_ns
) old
JOIN workflow_executions e ON e.thread_id = old.thread_id
LEFT JOIN (
    SELECT thread_id, checkpoint_ns, max(checkpoint_id COLLATE "C") AS cid
    FROM checkpoints GROUP BY thread_id, checkpoint_ns
) nw ON nw.thread_id = old.thread_id AND nw.checkpoint_ns = old.checkpoint_ns
WHERE e.status IN ('queued', 'running', 'waiting_approval')
  AND (nw.cid IS NULL OR nw.cid < old.cid)
GROUP BY e.id, e.status
ORDER BY e.id
"""

ACTIVE_COUNTS = (
    "SELECT status, count(*) FROM workflow_executions "
    "WHERE status IN ('queued', 'running', 'waiting_approval') GROUP BY status"
)

# Verbatim from 0001_baseline (Prisma migration 20260420081000_phase_1_execution_engine).
CREATE_OLD_TABLES = (
    '''CREATE TABLE "langgraph_checkpoints" (
    "thread_id" TEXT NOT NULL,
    ...exact lines 88-96 of 0001...
)''',
    '''CREATE TABLE "langgraph_checkpoint_writes" (
    ...exact lines 101-110 of 0001...
)''',
    'CREATE INDEX "langgraph_checkpoints_thread_id_idx" ON "langgraph_checkpoints"("thread_id")',
    'CREATE INDEX "langgraph_checkpoint_writes_thread_id_checkpoint_id_idx" '
    'ON "langgraph_checkpoint_writes"("thread_id", "checkpoint_id")',
)


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    bind = op.get_bind()
    op.execute("LOCK TABLE langgraph_checkpoint_writes, langgraph_checkpoints IN ACCESS EXCLUSIVE MODE")
    counts = dict.fromkeys(ACTIVE_STATUSES, 0)
    for status, n in bind.execute(sa.text(ACTIVE_COUNTS)):
        counts[str(status)] = int(n)
    print("0006: active runs " + " ".join(f"{s}={counts[s]}" for s in ACTIVE_STATUSES), flush=True)
    blocked = [(str(r[0]), str(r[1])) for r in bind.execute(sa.text(LEGACY_ONLY_ACTIVE_RUNS))]
    if blocked:
        shown = ", ".join(f"{eid} ({status})" for eid, status in blocked[:SHOWN])
        more = f" and {len(blocked) - SHOWN} more" if len(blocked) > SHOWN else ""
        raise RuntimeError(
            f"0006_drop_legacy_tables: {len(blocked)} active run(s) have their newest checkpoint "
            f"only in the old tables: {shown}{more}. Nothing was dropped. Copy them forward with "
            f"the last release that has the copy tool (git checkout {COPY_TOOL_REF} && uv run "
            "composer checkpoints copy --to postgres --execution <id>, direct DATABASE_URL), or "
            "cancel them; then re-run the deploy."
        )
    op.execute("DROP TABLE langgraph_checkpoint_writes")
    op.execute("DROP TABLE langgraph_checkpoints")
    op.execute("DROP TABLE IF EXISTS _prisma_migrations")


def downgrade() -> None:
    _bound_locks()
    for statement in CREATE_OLD_TABLES:
        op.execute(statement)
```

  The `...exact lines...` markers above are the only part to fill in mechanically: paste 0001's text as it is, including its column indentation (4 spaces) and the blank line before `CONSTRAINT`. Step 1's test fails until it is exact. `op.execute` with a string passes through SQLAlchemy `text()`. The DDL has no `:name` tokens or `%`, so nothing is mis-parsed (0004 does the same).

- [ ] **Step 3: Run the unit test.** `LANGSMITH_TRACING=false uv run pytest tests/unit/db/test_revision_0006.py --no-cov -q`. Expected: 3 passed. Then `uv run alembic heads`: exactly `0006_drop_legacy_tables (head)`.

- [ ] **Step 4: Write the integration tests first** (`tests/integration/db/test_alembic_0006_drop_legacy_tables.py`). They run against the shared `TEST_DATABASE_URL` through `alembic` subprocesses, like `test_alembic_0005_execution_attempts.py`. Copy its `_run_alembic`, `_alembic` and `_conn` helpers, and pass `to_libpq_url(os.environ["TEST_DATABASE_URL"])` to `psycopg.connect`. Every test restores with `_alembic("upgrade", "head")` in `finally`.

```python
"""Revision 0006 drops the old checkpoint tables and Prisma's ledger, refuses while an active run
still depends on the old tables, fails fast on a held lock, and downgrades to empty tables with
the original shape."""

PREV = "0005_execution_attempts"
OLD = ("langgraph_checkpoint_writes", "langgraph_checkpoints")
EARLY, MID, LATE = (
    "1f000000-0000-6000-8000-000000000001",
    "1f000000-0000-6000-8000-000000000002",
    "1f000000-0000-6000-8000-000000000003",
)

def _present(*names: str) -> set[str]: ...      # names whose to_regclass is not NULL
def _version() -> str: ...                       # SELECT version_num FROM alembic_version
def _seed(tag, status, *, old_id, new_id) -> tuple[str, str, str]:
    """workflow w-<tag>; execution e-<tag> (status, thread t-<tag>); an old-table checkpoint with
    old_id (and one write); an official checkpoint with new_id unless None. Returns (wid, eid, thread)."""
def _cleanup(wid, eid, thread) -> None:
    """DELETE the rows above (old tables only if present), then the execution and workflow."""

def test_head_has_none_of_the_dropped_tables() -> None:
    assert _present(*OLD, "_prisma_migrations") == set()

def test_downgrade_recreates_empty_tables_with_the_original_shape_and_upgrade_drops_them() -> None:
    try:
        _alembic("downgrade", PREV)
        assert _present(*OLD) == set(OLD)
        assert _present("_prisma_migrations") == set()       # D3: not recreated
        # row counts are 0 in both tables
        catalog = snapshot(<libpq url>); golden = json.loads(<golden_0004 file>)
        for t in OLD:
            assert catalog["columns"][t] == golden["columns"][t]
        for key in ("constraints", "indexes"):
            assert [r for r in catalog[key] if r[0] in OLD] == [r for r in golden[key] if r[0] in OLD]
        _alembic("upgrade", "head")
        assert _present(*OLD) == set()
    finally:
        _alembic("upgrade", "head")

@pytest.mark.parametrize(
    ("status", "old_id", "new_id", "refused"),
    [
        ("waiting_approval", MID, None, True),   # only in the old tables
        ("running", MID, None, True),
        ("queued", MID, None, True),
        ("waiting_approval", LATE, MID, True),   # old tables newer: newest state would be lost
        ("waiting_approval", MID, MID, False),   # already copied
        ("waiting_approval", EARLY, MID, False), # official tables ahead
        ("completed", MID, None, False),         # terminal runs never resume
        ("failed", MID, None, False),
        ("canceled", MID, None, False),
    ],
)
def test_upgrade_refuses_only_while_an_active_run_depends_on_the_old_tables(
    status: str, old_id: str, new_id: str | None, refused: bool
) -> None:
    tag = secrets.token_hex(5)
    wid = eid = thread = ""
    try:
        _alembic("downgrade", PREV)
        wid, eid, thread = _seed(tag, status, old_id=old_id, new_id=new_id)
        done = _run_alembic("upgrade", "head")
        out = done.stdout + done.stderr
        assert "0006: active runs" in out
        if refused:
            assert done.returncode != 0
            assert f"{eid} ({status})" in out
            assert "composer checkpoints copy --to postgres" in out and "6ce5495" in out
            assert thread not in out                   # ids and statuses only
            assert _version() == PREV                  # nothing applied
            assert _present(*OLD) == set(OLD)          # nothing dropped
        else:
            assert done.returncode == 0, out
            assert _present(*OLD) == set()
    finally:
        _cleanup(wid, eid, thread)
        _alembic("upgrade", "head")

def test_an_orphan_old_checkpoint_with_no_execution_does_not_block() -> None:
    # an old-table thread with no workflow_executions row: upgrade succeeds and drops it

def test_upgrade_fails_fast_when_an_old_table_is_locked() -> None:
    _alembic("downgrade", PREV)
    blocker = psycopg.connect(<libpq url>)  # not autocommit: the lock is held until close()
    try:
        blocker.execute("LOCK TABLE langgraph_checkpoints IN ROW EXCLUSIVE MODE")
        blocked = _run_alembic("upgrade", "head")
        assert blocked.returncode != 0
        assert "lock timeout" in (blocked.stdout + blocked.stderr).lower()
        assert _version() == PREV and _present(*OLD) == set(OLD)
    finally:
        blocker.close()
        _alembic("upgrade", "head")
```

  Seed SQL: `INSERT INTO workflows (id, name, nodes, edges, updated_at) VALUES (%s, 'test', '[]', '[]', now())`; `INSERT INTO workflow_executions (id, workflow_id, status, node_results, variables, thread_id) VALUES (%s, %s, %s, '{}', '{}', %s)` (the same columns `test_alembic_0005` uses); `INSERT INTO langgraph_checkpoints (thread_id, checkpoint_id, checkpoint, metadata) VALUES (%s, %s, %s, %s)` with `b"c"`, `b"m"`; `INSERT INTO langgraph_checkpoint_writes (thread_id, checkpoint_id, task_id, idx, channel, value) VALUES (%s, %s, 't', 0, 'ch', %s)`; `INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (%s, %s, '{}')`.

- [ ] **Step 5: Add scratch-database tests** to `tests/integration/db/test_baseline_schema.py`, reusing its `scratch_db`, `_ok`, `_tables`, `_versions`, `_prisma_built` and `snapshot`:

```python
LEGACY = {"langgraph_checkpoints", "langgraph_checkpoint_writes", "_prisma_migrations"}

def test_fresh_database_at_head_has_none_of_the_legacy_tables(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "head")
    assert _tables(scratch_db) & LEGACY == set()

def test_a_prisma_built_database_loses_its_prisma_history_at_head(scratch_db: str) -> None:
    _prisma_built(scratch_db)
    _ok(scratch_db, "upgrade", "head")
    assert _tables(scratch_db) & LEGACY == set()
    assert _versions(scratch_db) == [(alembic_head_revision(),)]

def test_0006_downgrade_restores_the_fresh_0005_schema_exactly(scratch_db: str) -> None:
    _ok(scratch_db, "upgrade", "0005_execution_attempts")
    before = snapshot(scratch_db)
    _ok(scratch_db, "upgrade", "head")
    _ok(scratch_db, "downgrade", "0005_execution_attempts")
    assert snapshot(scratch_db) == before
    assert "_prisma_migrations" not in _tables(scratch_db)
```

  (If `_tables` returns a set including `alembic_version`, the intersections still hold.) Reword the `BASE_HEAD` comment: "… later revisions (0005 onward, including 0006, which drops the old checkpoint tables the goldens contain) don't move them; head itself is covered by the model comparison and the 0006 tests below."

- [ ] **Step 6: Keep the 0005 tests restoring to head.** In `tests/integration/db/test_alembic_0005_execution_attempts.py`, rename `HEAD = "0005_execution_attempts"` to `THIS = "0005_execution_attempts"`. Keep `_alembic("upgrade", THIS)` where the test exercises 0005's own upgrade (line 89 and the in-test upgrade near line 164). Change every **restoring** call (in `finally` blocks and at test ends: lines ~168, ~211, ~241) to `_alembic("upgrade", "head")`. Otherwise the session's database is left at 0005 with the old tables present, and `test_database_is_at_the_alembic_head` and the parity test fail later in the run. `downgrade PREV` from head now runs 0006's downgrade first. That is fine: the recreated tables are empty, so the later `upgrade head` passes the guard even with the test's active executions present. `test_alembic_0002/0003/0004` already restore with `"head"`. Check with `grep -n '"upgrade"' tests/integration/db/test_alembic_000*.py` that no other file restores to a fixed revision.

- [ ] **Step 7: Run the migration tests.**
  ```bash
  DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head   # applies 0006 on the container
  LANGSMITH_TRACING=false uv run pytest tests/integration/db/test_alembic_0006_drop_legacy_tables.py tests/integration/db/test_baseline_schema.py tests/integration/db/test_alembic_0002_idempotency_scope.py tests/integration/db/test_alembic_0003_assignment_user_fks.py tests/integration/db/test_alembic_0004_saver_tables.py tests/integration/db/test_alembic_0005_execution_attempts.py -m integration --no-cov -q
  ```
  Expected: all pass. `test_schema_parity.py` and `test_fresh_database_at_head_matches_the_models` FAIL at this point, because the models still declare the old tables. Task 5 fixes that; note it and do not "fix" it here.

- [ ] **Step 8: Commit** (only these files): `feat(db): 0006 drops the old checkpoint tables and _prisma_migrations, refusing while an active run still needs them`.

- [ ] **Review gate (Opus):** review 0006 against D1–D4 and the expand/contract analysis: lock order, the guard query against `src/db/repos/checkpoints.py` `_unsynced_from` / `_OLD_AHEAD` (same semantics), the refusal message (ids only), the DDL equality, and that no `src` import exists in the revision.

### Task 3: Resume without copy-on-resume (needs D6)

**Files:**
- Modify: `src/engine/langgraph_executor.py`
- Modify: `src/api/execution_observability.py`
- Rename and trim: `tests/unit/engine/test_resume_checkpoint_sync.py` → `tests/unit/engine/test_resume_checkpoint_probe.py`
- Modify: `tests/unit/api/test_execution_observability.py` (only if it pins the guidance text)

- [ ] **Step 1: Tests first.** `git mv tests/unit/engine/test_resume_checkpoint_sync.py tests/unit/engine/test_resume_checkpoint_probe.py`. In the moved file:
  - Delete `test_resume_brings_the_thread_forward_before_the_graph_runs`, `test_a_failing_bring_forward_fails_the_run_with_its_own_code`, `test_bring_forward_ignores_savers_that_are_not_the_official_one`, `test_bring_forward_copies_only_the_namespaces_the_old_tables_lead`, `test_bring_forward_does_nothing_when_the_new_tables_are_ahead` and `test_bring_forward_logs_ids_only`. Delete the imports `from src.checkpoints import sync`, `from src.db.repos import checkpoints as checkpoints_repo` and `AsyncPostgresSaver` if unused.
  - `_setup` loses its `bring` mock (the 4th return value). Update its callers.
  - Keep `test_resume_without_a_checkpoint_fails_closed_and_never_runs_the_graph`, `test_missing_checkpoint_log_carries_the_id_only` and `test_checkpoint_failures_pass_the_attempt_id_to_the_fenced_transition`. Rename `test_a_failing_checkpoint_probe_fails_the_run_with_its_own_code`: its assertion stays `failure_code == "checkpoint_sync_failed"`, and it adds a log check: the execution id and `OSError` appear, the message "connection reset" does not, and there is no `exc_info`.
  - Add:
    ```python
    def test_resume_no_longer_imports_the_old_table_bridge() -> None:
        import src.engine.langgraph_executor as mod
        assert not hasattr(mod, "bring_forward")
        assert not hasattr(mod, "CheckpointSyncFailed")
        assert hasattr(mod, "CheckpointReadFailed")
    ```
  - Update the module docstring: "``LangGraphExecutor.resume``: fail closed when no checkpoint exists or the probe read fails. No database: repos and the graph are fakes."

  Run: `LANGSMITH_TRACING=false uv run pytest tests/unit/engine/test_resume_checkpoint_probe.py --no-cov -q`. Expected: the new test FAILS.

- [ ] **Step 2: Implement.** In `src/engine/langgraph_executor.py`:
  - Remove `from src.checkpoints.sync import bring_forward`.
  - Replace `class CheckpointSyncFailed` with `class CheckpointReadFailed(Exception): """Probing the run's checkpoint failed before the graph ran; the saved state is intact."""`.
  - `CheckpointMissing` docstring: "A resume found no checkpoint for the run; it must not restart the graph."
  - In `resume`, the docstring's second paragraph becomes: "Loads the checkpoint from the official Postgres saver and feeds the decision via Command(resume=decision). A run with no checkpoint fails with ``failure_code="checkpoint_missing"``, and a failed probe with ``"checkpoint_sync_failed"``; neither restarts the graph." The block becomes:
    ```python
            await ensure_execution_running(self.sessionmaker, execution_id)
            try:
                has_checkpoint = await self._has_checkpoint(execution.thread_id)
            except Exception as read_exc:
                # Not "execution_error": the graph never ran and the saved state is intact, so
                # the run can be retried. Log the class only (messages may carry conninfo).
                logger.error(
                    "Execution %s checkpoint read failed (%s)", execution_id, type(read_exc).__name__
                )
                raise CheckpointReadFailed(type(read_exc).__name__) from None
    ```
  - `except CheckpointSyncFailed as exc:` becomes `except CheckpointReadFailed as exc:`, still `failure_code="checkpoint_sync_failed"` (the wire value is kept, D6). Add a comment: `# Wire value kept from phase 5 (dashboards, stored rows); since phase 7 it means a failed read.`
  - In `src/api/execution_observability.py`, the `checkpoint_sync_failed` guidance becomes `"Execution state could not be read. Contact an administrator."`. Update any test that pins the old text: `grep -n "could not be saved" tests`.
  - `src/telemetry/attributes.py` and the catalog's JSON block: **no change** (the vocabulary keeps the value).

- [ ] **Step 3: Run.** `LANGSMITH_TRACING=false uv run pytest tests/unit/engine tests/unit/api/test_execution_observability.py tests/unit/telemetry --no-cov -q`: all pass. Then pyright on `src/engine/langgraph_executor.py`.

- [ ] **Step 4: Commit:** `refactor(engine): resume reads the official saver only; the old-table copy-forward is gone`.

### Task 4: Schema filter and test reset helpers (D9)

**Files:** `src/db/schema_filters.py`, `tests/unit/db/test_schema_filters.py`, `tests/integration/db/schema_catalog.py`, `tests/_checkpoint_reset.py`, `tests/integration/conftest.py`, `tests/regression/conftest.py`.

- [ ] **Step 1: Test first.** In `tests/unit/db/test_schema_filters.py`, replace line 5 with `assert include_name("_prisma_migrations", "table", {}) is True  # dropped by 0006; nothing special any more`. Run: it FAILS.
- [ ] **Step 2:** `src/db/schema_filters.py`: `IGNORED_TABLES = frozenset({"alembic_version"})`, with the comment "Alembic's own bookkeeping table. (`_prisma_migrations` was ignored until revision 0006 dropped it.)". Run the unit test: it passes.
- [ ] **Step 3:** `tests/integration/db/schema_catalog.py` keeps `IGNORED` unchanged. Add above it: `# _prisma_migrations stays ignored: the adoption tests build it by hand and compare with the 0001/0004 goldens, at revisions where it still exists (0006 drops it).`
- [ ] **Step 4:** `tests/_checkpoint_reset.py`: `_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")`. Remove `_SAVER_TABLES` / `_LEGACY_TABLES`. `checkpoint_thread_ids()` returns `set[str] | None`: None when there is no test database **or** the saver tables are absent (a 0004 downgrade test). `delete_checkpoint_threads_except(keep: set[str] | None)` returns early when `keep is None`, and per table skips any that no longer exist. Rewrite the docstring: three tables, why the 0004 downgrade needs them clean, the `_exists` checks kept. Update the two conftests' docstrings ("all five tables" → "the three saver tables") and their type annotations if they name the old dict type. Grep: `grep -rn "checkpoint_thread_ids\|delete_checkpoint_threads_except" tests`. Only the two conftests call them.
- [ ] **Step 5: Run** `LANGSMITH_TRACING=false uv run pytest tests/unit/db/test_schema_filters.py --no-cov -q` and pyright on `tests/_checkpoint_reset.py tests/integration/conftest.py tests/regression/conftest.py`.
- [ ] **Step 6: Commit:** `test(db): checkpoint reset and schema filter forget the dropped tables`.

### Task 5: Remove the bridge code and convert the tests (D5, D7, D14)

**Files:** as in the file map, Task 5 rows.

- [ ] **Step 1: New guard tests first.**
  - `tests/unit/cli/test_main_subcommands.py`:
    ```python
    import pytest
    from src.cli.main import _build_parser  # pyright: ignore[reportPrivateUsage]

    def test_checkpoints_subcommand_is_gone() -> None:
        with pytest.raises(SystemExit) as exc:
            _build_parser().parse_args(["checkpoints", "guard"])
        assert exc.value.code == 2
    ```
  - `tests/unit/db/test_legacy_tables_gone.py`: walk `src/**/*.py` (skipping `src/db/migrations/versions/`), `tests/**/*.py` (skipping this file, `tests/integration/db/test_baseline_schema.py`, `tests/integration/db/schema_catalog.py`, `tests/integration/db/test_alembic_0006_drop_legacy_tables.py` and `tests/unit/db/test_revision_0006.py`) and `.github/workflows/*.yml`. Assert that none contains `langgraph_checkpoint`, `LangGraphCheckpoint`, `LegacyCheckpointSaver`, `bring_forward`, `_prisma_migrations`, `composer checkpoints` or `src.checkpoints.sync`. Each hit is reported as `path:line`. Skip `__pycache__`.
  Run both: they FAIL now.

- [ ] **Step 2: Delete the bridge modules and the CLI.**
  ```bash
  git rm src/checkpoints/legacy.py src/checkpoints/copy.py src/checkpoints/versions.py \
         src/checkpoints/sync.py src/cli/checkpoints.py
  git rm -r tests/integration/db/checkpoints
  git rm tests/integration/sqla_phase5/test_checkpoint_sync_failure.py \
         tests/unit/checkpoints/test_versions.py tests/unit/cli/test_checkpoints_cli.py \
         tests/fixtures/legacy_checkpoint_rows.json
  ```
  `src/cli/main.py`: delete docstring line 7, the `# checkpoints` block (lines 68–78) and the `if args.subcommand == "checkpoints":` block (lines 124–129).

- [ ] **Step 3: Trim the repo and the models.**
  - `src/db/repos/checkpoints.py`: keep the module docstring (reworded to "LangGraph checkpoint rows: deleting threads from the official saver's tables in the caller's transaction."), the imports it still needs (`sa_delete`, `AsyncSession`, the three `Saver*` models, `in_array`), `SAVER_THREAD_TABLES`, `_SAVER_MODELS` and `delete_threads`. Delete everything else (the D7 list). `delete_threads`'s loop becomes `for model in _SAVER_MODELS:`, and its docstring: "Delete every checkpoint, blob and pending write of ``thread_ids`` from the saver's three tables, in the caller's transaction (``adelete_thread`` runs on the saver's own autocommit connection and cannot join it; ADR-0036 D7)."
  - `src/db/models/checkpoints.py`: delete `LangGraphCheckpoint` and `LangGraphCheckpointWrite`. The module docstring becomes "The official LangGraph saver's tables (Alembic 0004; the library writes them). Mapped for schema parity and deletes." Drop imports that are now unused (`datetime`, `created_at_column`).
  - `src/db/models/__init__.py`: remove the two names from the import and from `__all__`.
  - `src/api/executions.py` lines 435–439 (docstring of `delete_execution`): "LangGraph checkpoints, blobs and pending writes (the official saver's three tables), keyed by `thread_id` …" (keep the rest of the sentence).
  - `src/engine/langgraph_executor.py`: no further change (Task 3).
  - Check: `grep -rnE "legacy_|UnsyncedRun|active_status_counts|waiting_threads|saver_namespaces|ACTIVE_STATUSES" src/db/repos/checkpoints.py`: no output. `grep -rn "checkpoints_repo\." src`: only `delete_threads` in `src/db/repos/executions.py:239`.

- [ ] **Step 4: Convert the tests that seeded the old tables.** Use the saver models. `SaverCheckpoint(thread_id=…, checkpoint_id=…, checkpoint={})` has `checkpoint_ns` defaulting to `''` and `metadata` defaulting server-side. `SaverCheckpointWrite(thread_id=…, checkpoint_id=…, task_id="t", idx=0, channel="ch", blob=b"v")`. `SaverCheckpointBlob(thread_id=…, channel="ch", version="1", type="bytes", blob=b"\x00\x01")`.
  - `tests/unit/db/test_models_metadata.py`: remove `"langgraph_checkpoint_writes"` and `"langgraph_checkpoints"` from `EXPECTED_TABLES`, and rename `test_every_prisma_table_is_mapped_exactly_once` to `test_every_table_is_mapped_exactly_once`. In `test_checkpoint_writes_primary_key_order`, use `Base.metadata.tables["checkpoint_writes"]`; the expected list is the same five columns, and `idx.autoincrement is False` still holds.
  - `tests/integration/db/test_round_trip.py`, `test_bytes_text_defaults_and_double_precision`: replace the two legacy rows with `SaverCheckpointBlob(thread_id=thread_id, channel="x", version="1", type="bytes", blob=b"\x00\x01")` and `SaverCheckpointWrite(thread_id=thread_id, checkpoint_id="c1", task_id="t", idx=0, channel="x", blob=b"\xff")`. Read back the blob, then assert `blob == b"\x00\x01"` and `checkpoint_ns == ""`. Clean up via `checkpoints_repo.delete_threads` or the test's existing cleanup (check the test's tail).
  - `tests/integration/db/repos/test_executions.py`: `_artifacts` adds a `SaverCheckpoint` and a `SaverCheckpointWrite`; `_artifact_counts` counts `SaverCheckpoint` and `SaverCheckpointWrite`. The `[1, 1, 1, 1]` / `[0, 0, 0, 0]` expectations are unchanged.
  - `tests/integration/test_durable_execution.py`, `test_cancel_retains_checkpoint_and_pending_writes`: drop the two `rows.add(LangGraph…)` calls and their asserts. Keep the `aput` of an `empty_checkpoint()`. Add `await app.state.checkpointer.aput_writes(stored_config, [("result", "retained-write")], "task-1")`, where `stored_config` is the config `aput` returned. After the cancel, assert that `aget_tuple` is not None and that its `pending_writes` contains `("task-1", "result", "retained-write")`.
  - `tests/integration/sqla_phase4a/conftest.py`: `add_checkpoint` adds a `SaverCheckpoint` plus a `SaverCheckpointWrite`; `checkpoint_counts` counts those two. The section comment becomes `# ---- checkpoints (the saver's tables) and approvals`. Remove the `LangGraphCheckpoint*` imports. `tests/integration/sqla_phase4a/test_delete.py` needs no change: it uses the helpers. Read it to confirm that it expects `(0, 0)` after a delete and `(1, 1)` otherwise.
  - `tests/integration/db/repos/test_checkpoints.py`: delete the `legacy_*` section (lines 84–160) and `test_legacy_ahead_namespaces_lists_only_namespaces_the_old_tables_lead` (309 onward), plus their helpers and imports. Convert `test_delete_threads_removes_both_tables_for_exactly_those_threads` to seed saver rows. Rename `test_delete_threads_clears_all_five_tables_for_those_threads_only` to `…_all_three_saver_tables_…` and drop its old-table seeding. Add `test_old_tables_do_not_exist_at_head`, which asserts `to_regclass('langgraph_checkpoints')` and `to_regclass('langgraph_checkpoint_writes')` are NULL. Keep `test_delete_threads_runs_in_the_callers_transaction`, `test_delete_threads_matches_adelete_thread`, the 70 000-id test and `test_delete_threads_covers_every_saver_table`, adjusted to saver rows where they seeded old ones.
  - `tests/integration/sqla_phase5/conftest.py`: `CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")`, with the comment "The official saver's tables (the old Prisma ones were dropped by Alembic 0006)." `checkpoint_row_counts` iterates that tuple, so callers indexing `"langgraph_checkpoints"` must go (see the next bullet). `test_pause_resume.py:275` loops over the tuple and is unaffected.
  - `tests/integration/sqla_phase5/test_deliberate_changes.py`: delete `_seed_paused_on_old_tables`, `OLD`, `_only` if no longer used, and the tests `test_run_paused_on_the_old_tables_resumes_after_cutover`, `test_new_tables_ahead_are_not_overwritten_by_the_old_ones` and `test_resume_command_still_reaches_a_copied_graph_state`. Remove the imports of `copy_thread`, `LegacyCheckpointSaver` and `bring_forward`. In `test_new_runs_write_only_the_new_tables` and `test_dev_mode_runs_write_only_the_new_tables`, replace the `_only(counts, OLD)` assertions with a check that `to_regclass('langgraph_checkpoints') IS NULL` (via `a.sm`). Update the module docstring's list of deliberate changes. Keep `test_resume_with_no_checkpoint_fails_closed` and `test_saver_pool_serves_concurrent_runs`.
  - `grep -rn "CHECKPOINT_TABLES\|NEW\b" tests/integration/sqla_phase5`: fix any remaining import of the deleted names.

- [ ] **Step 5: Run everything touched.**
  ```bash
  LANGSMITH_TRACING=false uv run pytest -m "not integration" --no-cov -q
  LANGSMITH_TRACING=false uv run pytest -m integration --no-cov -q tests/integration/db tests/integration/sqla_phase4a tests/integration/sqla_phase5 tests/integration/test_durable_execution.py
  uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests
  uv run ruff check src tests && uv run ruff format --check src tests
  ```
  Expected: all green, including `test_schema_parity.py` and `test_fresh_database_at_head_matches_the_models` (models and head now agree), `test_legacy_tables_gone.py` and `test_main_subcommands.py`. `tests/unit/db/test_prisma_import_boundary.py` still passes.

- [ ] **Step 6: Commit** (two commits): `refactor(checkpoints): remove the old-table saver, copy tool, guard and legacy models` (src plus the deleted tests), then `test: seed the saver tables where tests used the dropped ones`.

- [ ] **Review gate (Opus):** confirm that every row of "Every remaining reference" is handled; that no deleted test carried a guarantee that is still needed (D14); and that `delete_threads` still runs in the caller's transaction.

### Task 6: Deploy workflow (needs D8)

**Files:** `.github/workflows/deploy-gcp.yml`, `tests/unit/test_deploy_workflow.py`.

- [ ] **Step 1: Test first.** In `tests/unit/test_deploy_workflow.py`, replace `test_checkpoint_guard_runs_before_alembic_and_before_deploy_without_override` with:
  ```python
  def test_migrations_run_before_the_deploy_without_override_or_guard() -> None:
      steps = _backend_steps()
      names = [s.get("name") for s in steps]
      migrate = steps[names.index("Apply database migrations")]
      lines = [ln.strip() for ln in migrate["run"].splitlines() if not ln.strip().startswith("#")]
      assert lines.index("uv sync --frozen") < lines.index("uv run alembic upgrade head")
      assert lines.index("uv run alembic upgrade head") < lines.index("uv run alembic current")
      # Phase 7: the old-table guard is gone; revision 0006 performs that check itself.
      assert not any("composer checkpoints" in ln for ln in lines)
      assert not any("prisma" in ln for ln in lines)
      assert names.index("Apply database migrations") < names.index("Deploy to Cloud Run")
      assert "continue-on-error" not in migrate
      assert not any("||" in ln for ln in lines if "alembic" in ln)
      assert "if" not in migrate
  ```
  (`_backend_steps` already exists lower in the file; move it above the test.) In `test_migration_step_tolerates_a_neon_cold_start`, reword the comment: "Alembic is the deploy's first connection to Neon …". Run: the new test FAILS.
- [ ] **Step 2: Edit the workflow.** In "Apply database migrations": the `env` comment (line 147) becomes `# Alembic is the first connection to Neon; allow a cold start (env.py also floors it at 15 s).`. Delete lines 160–167 (the comment block "SQLAlchemy phase 5 (until phase 7 …" through `uv run composer checkpoints guard`). Keep the two Alembic lines and their comment. Touch nothing else in the file (Dependabot SHA bumps are separate commits).
- [ ] **Step 3: Run** `LANGSMITH_TRACING=false uv run pytest tests/unit/test_deploy_workflow.py tests/unit/db/test_legacy_tables_gone.py --no-cov -q`: pass. Also `.venv/Scripts/python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy-gcp.yml'))"`.
- [ ] **Step 4: Commit:** `ci(deploy): drop the checkpoint guard step; revision 0006 checks the old tables itself`.

### Task 7: Docs (needs D13 for `CLAUDE.md`)

**Files and exact edits:**

- [ ] `docs/operations/production-deployment.md`:
  - §1.1a line 45: "runs `uv run alembic upgrade head`, then `uv run alembic current`" (drop the guard). Line 58: delete the guard line from the code block.
  - Replace the whole section "Checkpointer cutover (phase 5 to phase 7)" (lines 322–387, up to but not including "Notifications and connections") with "## Checkpointer (since phase 5)". It has a short paragraph (the official saver; tables from Alembic 0004; the old Prisma tables and the copy tool were removed in phase 7 by `0006_drop_legacy_tables`, which refused while an active run still had its newest checkpoint only in the old tables) and a "### Resume failure codes" subsection with the three codes: `checkpoint_missing` unchanged; `checkpoint_incompatible` unchanged; `checkpoint_sync_failed` reworded per D6 ("reading the run's saved state failed at the start of a resume; the graph did not run and the state is intact; fix the cause (database reachability) and start a new run, or re-queue it"). Keep the "Startup budget" paragraph and the TCP-probe warning paragraph, moved under it: they are not phase-5-specific.
  - "Rolling back across the Prisma removal": bullet 2 becomes "Re-running a pre-phase-6 deploy workflow does not work: since `0005` it fails at `alembic upgrade head`, and since phase 7 `_prisma_migrations` is gone too. Never re-run one; swap the Cloud Run revision instead." Bullet 1's "If the checkpointer is also being rolled back" sentence points to the new phase 7 section.
  - New subsection after the 0005 one: "### Rolling back across the phase 7 cleanup (`0006_drop_legacy_tables`)": (1) why: images built before phase 7 read the old tables on every resume and delete, and without them a resume fails the run with `checkpoint_sync_failed` and a delete returns 500; (2) the order: from a checkout of the phase 7 merge commit, with the direct URL (same `gcloud secrets … | sed` as §1.1a), `uv run alembic downgrade 0005_execution_attempts` FIRST, then `gcloud run services update-traffic …`; (3) the downgrade recreates the tables empty and restores no data; (4) a pre-phase-7 deploy workflow re-run fails at its guard (`failed: UndefinedTable`) until the downgrade, then works; (5) pre-phase-5 images: unsupported, except by downgrading and then copying new → old with `git checkout 6ce5495 && uv run composer checkpoints copy --to legacy --all-waiting`; (6) a forgotten downgrade: run it at once, and runs failed in between are restarted by their users; (7) the 0004 downgrade message's reference to `composer checkpoints copy` means the tool at `6ce5495`.
- [ ] `docs/operations/gcp-cloud-run-setup.md` line 95 (drop "run `uv run composer checkpoints guard` first, as the workflow does") and line 253 (step 3: "`uv run alembic upgrade head`, then `uv run alembic current`").
- [ ] `docs/operations/postgres-setup.md` line 68 (delete the guard line) and line 79 (add: "Revision `0006_drop_legacy_tables` later drops `_prisma_migrations`; adoption happens before that, so a pre-2026-11-20 restore is still adopted.").
- [ ] `docs/operations/disaster-recovery.md` line 137: replace the parenthetical with "(`_prisma_migrations` was dropped by `0006_drop_legacy_tables` in phase 7; a restore from before 2026-11-20 still has it, and `0001_baseline` adopts such a database.)"
- [ ] `docs/architecture.md` line 109 (delete the `legacy.py` tree line, and the `copy.py`, `sync.py` and `versions.py` lines if present) and line 147 (replace "The old Prisma tables stay until phase 7: … instead of restarting it." with "A resume that finds no checkpoint fails the run with `checkpoint_missing`, and a failed probe read with `checkpoint_sync_failed`, instead of restarting it. The old Prisma checkpoint tables, their SQLAlchemy port, the copy tool and the deploy guard were removed in phase 7 (Alembic `0006_drop_legacy_tables`)."; "cutover and rollback runbook" → "rollback runbook").
- [ ] `docs/operations.md:50`, `docs/operations/scaling.md:96`, `docs/saas/privacy.md:34`, `docs/saas/security.md:24`: `langgraph_checkpoints` → `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` (privacy row: "`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`"). `docs/claude-system-audit-fix-list-2026-11-17.md` and `docs/archive/**` are historical: leave them.
- [ ] `docs/api-reference.md:110`: "`checkpoint_sync_failed` | Reading the run's saved state failed at the start of a resume; the run was not restarted and its saved state is untouched. (Before phase 7 it also covered copying state forward from the old checkpoint tables.)"
- [ ] `docs/telemetry/signal-catalog.md:356`: "… a probe-read failure (`checkpoint_sync_failed`) leaves the saved state intact and is recoverable." The JSON vocabulary is unchanged.
- [ ] Spec `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`: §3.3 row 7: "**Cleanup (done, PR pending; 2026-11-28, ahead of the planned date):** Alembic `0006_drop_legacy_tables` drops …; the bridge code (old saver, copy tool, deploy guard, copy-on-resume) is removed". §3.4 "Deletes": "Until phase 7 they also cleared the old tables." "Rollback": add "(Phase 7 removed the old tables; see the runbook for rollback across `0006`.)" §7 risks row: "… old tables kept until phase 7 (dropped then, behind an in-migration check)".
- [ ] `docs/decisions.md`: ADR-0035, append `> **Phase 7 done (2026-11-28):** Alembic \`0006_drop_legacy_tables\` dropped \`langgraph_checkpoints\`, \`langgraph_checkpoint_writes\` and \`_prisma_migrations\`; the migration is complete.`. ADR-0036, append a note: D9's bridge (legacy saver, copy tool, guard, copy-on-resume) was removed in phase 7; D7 deletes cover the three saver tables; `checkpoint_sync_failed` now means a failed probe read; rollback across 0006 needs the downgrade first. ADR-0037, append a note: `_prisma_migrations` was dropped by 0006 and deliberately not recreated by its downgrade (D3 of the phase 7 plan); 0001's adoption logic is unchanged and still serves pre-2026-11-20 restores.
- [ ] `CLAUDE.md` stack table ORM row, per D13. Nothing else.
- [ ] `CHANGELOG.md` `[Unreleased]`: a new top section "### Removed — SQLAlchemy migration phase 7: old checkpoint tables, `_prisma_migrations` and the checkpoint bridge (2026-11-28)", placed above the `composer watch` entry. Cover: what 0006 drops and its in-migration check (statuses, only-or-newer, message, lock and statement timeouts); the downgrade (empty tables, exact DDL, no data, no `_prisma_migrations`, and why); code removed (modules, CLI, models, repo functions, guard step); `checkpoint_sync_failed` re-scoped and the exception renamed; tests deleted and converted; rollback order (downgrade first); "a pre-phase-6 deploy workflow can no longer be re-run (it already could not since 0005)".
- [ ] `docs/superpowers/plans/2026-11-26-sqlalchemy-phase-6-removal.md`, "Phase 7 preview": add one line at its end: "Superseded by [the phase 7 plan](2026-11-28-sqlalchemy-phase-7-cleanup.md); note its D3 (the downgrade does not recreate `_prisma_migrations`)."
- [ ] **Memory-relevant facts** (for the controller to write after the merge; this task does not edit memory files): (1) the Prisma → SQLAlchemy migration is complete: phase 7 dropped the old checkpoint tables and `_prisma_migrations` (`0006_drop_legacy_tables`); (2) the `composer checkpoints` CLI no longer exists, and the copy tool lives only at `6ce5495`; (3) rollback across 0006: downgrade first, then the image; (4) `checkpoint_sync_failed` now means a failed probe read. Files: `project_composer_stack.md` (still says "Prisma Python"), `project_platform_upgrade_decisions_2026_11.md` and `project_composer_phase_status.md`.
- [ ] Commit: `docs: phase 7 cleanup (runbook, ADR notes, spec, CHANGELOG, CLAUDE.md)`.

### Task 8: Done greps, full local run, the shared PR, deploy checks

- [ ] **Step 1: Done greps.**
  - `git grep -nE "langgraph_checkpoint|LangGraphCheckpoint|LegacyCheckpointSaver|bring_forward|CheckpointSyncFailed|src\.checkpoints\.(copy|sync|versions|legacy)" -- src tests .github`: only `src/db/migrations/versions/0001_baseline.py`, `0004_…`, `0006_…`, the 0006 tests, `test_baseline_schema.py`, the goldens and `test_legacy_tables_gone.py` (its pattern list).
  - `git grep -n "_prisma_migrations" -- src tests .github`: only 0001, 0006, `schema_catalog.py`, `test_baseline_schema.py`, `test_schema_filters.py`, the 0006 tests and `test_legacy_tables_gone.py`.
  - `git grep -n "checkpoints guard\|checkpoints copy" -- docs/operations CLAUDE.md .github`: only the phase 7 rollback subsection (`6ce5495` references).
  - `uv run alembic heads`: exactly `0006_drop_legacy_tables (head)`.
- [ ] **Step 2: Full CI-equivalent run on a fresh container** (drop and recreate it so the whole chain 0001 → 0006 runs from empty):
  ```bash
  docker rm -f composer-p7-pg && docker run -d --name composer-p7-pg … (as above)
  DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head
  uv run ruff check src tests && uv run ruff format --check src tests
  uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests
  LANGSMITH_TRACING=false uv run pytest --no-cov -q
  ```
  Then run the same suite a second time without recreating the database: the suite must leave it at head (Task 2 Step 6). Record the counts in the Outcome (before vs after; the difference is the deleted bridge tests minus the new ones). The frontend is untouched by phase 7, so its checks belong to the shared PR's other changes.
- [ ] **Step 3: Rehearse the production upgrade on a database shaped like production.** In a scratch database: `_prisma_built`-style (create `_prisma_migrations` with the 20 rows, as `test_baseline_schema._prisma_built` does) → `alembic upgrade 0005_execution_attempts` → insert one completed execution with old-table checkpoints → `alembic upgrade head` prints `0006: active runs queued=0 running=0 waiting_approval=0` and succeeds → `alembic downgrade 0005_execution_attempts` → `alembic upgrade head` again succeeds. Paste the output into the Outcome.
- [ ] **Step 4: The shared PR.** Phase 7 commits, the `composer watch` commit(s) and the Dependabot catch-up go in one PR from `chore/phase7-deps-watch`. Its description has a "Phase 7" section: what is dropped, the in-migration check, the decisions confirmed, the merge checklist below and the rollback order. It ends with the attribution line from the session's system reminder. Push once. CI must be green before merge.
- [ ] **Step 5: Merge checklist (Balaji; merging deploys).**
  1. In the Neon SQL editor on production: `SELECT status, count(*) FROM workflow_executions WHERE status IN ('queued','running','waiting_approval') GROUP BY status;` must return no rows (D11). If a run is waiting, approve or cancel it first and let it finish.
  2. Note the time (UTC), then merge. Do not approve, reject or delete anything until the deploy job's "Smoke test /ready" step has passed.
  3. In the deploy log, "Apply database migrations" shows `0006: active runs queued=0 running=0 waiting_approval=0`, then `alembic current` prints `0006_drop_legacy_tables (head)`.
- [ ] **Step 6: Post-deploy checks.**
  - `SELECT id FROM workflow_executions WHERE failure_code = 'checkpoint_sync_failed' AND completed_at >= '<merge time UTC>';` returns no rows.
  - `SELECT to_regclass('langgraph_checkpoints'), to_regclass('langgraph_checkpoint_writes'), to_regclass('_prisma_migrations');` returns three NULLs.
  - `/ready` is 200. One run completes; one approval run pauses and resumes (the phase 5 manual check); deleting a finished run works. Backend logs show no new errors for 24 h.
  - Delete the container: `docker stop composer-p7-pg && docker rm composer-p7-pg`.
- [ ] **Step 7: Outcome.** Append an "Outcome" section to this plan: what changed against the plan, counts, the rehearsal output, the deploy-log lines and the post-deploy query results. Then the controller updates the memory files listed in Task 7.

## Risks

| Risk | Mitigation |
|---|---|
| A paused run is approved during the swap window and fails on the old revision (`checkpoint_sync_failed`) | D11: zero active runs before merge, no approvals during the job, post-deploy query; the user starts a new run. Single user. |
| A rollback to a pre-phase-7 image fails every resume | D12: downgrade first (recreates empty tables); runbook subsection; a mistaken order is repaired by running the downgrade at once. |
| 0006 drops a run's only checkpoint | The in-migration check under ACCESS EXCLUSIVE locks refuses (the guard's query, frozen); parametrised tests cover active, terminal, copied, ahead and newer-in-old cases. |
| 0006 hangs a deploy behind a lock | `lock_timeout` 5 s and `statement_timeout` 60 s; lock-held test; the deploy can be re-run safely (one transaction). |
| The downgrade recreates tables that differ from the original | Statements copied verbatim from 0001 (unit test); catalog equality with a fresh 0005 build and with golden_0004's entries (integration tests). |
| The suite leaves the shared test database at 0005 (old fixed-revision restores) | Task 2 Step 6; Task 8 Step 2 runs the suite twice on one database. |
| A deleted test carried a guarantee still needed | D14 inventory; Task 5 review gate; `delete_threads` transaction and drift tests kept. |
| Merge conflict with the watch or Dependabot changes in the same PR | Disjoint files except `CHANGELOG.md` (separate section) and possibly `deploy-gcp.yml` (separate commits; SHA bumps kept). |
| A Dependabot bump of `langgraph-checkpoint-postgres` adds saver migrations | `test_library_contract.py` fails; stop, needs its own `0007` revision. |

## Self-review notes (writing-plans checklist)

- **Brief coverage:** (1) revision 0006: drop list, in-migration guard and message, downgrade DDL, `_prisma_migrations` decision (D3), locks (D4); Task 2. (2) Code removal: `LegacyCheckpointSaver`, CLI (D5), `bring_forward` and failure codes (D6, D7), models, deploy step (D8), schema filters (D9), reset helpers, delete paths; Tasks 3–6. (3) 0001 interaction: fresh databases unaffected (`IF EXISTS`), adopted databases lose the table at head (test), nothing else reads it (grep plus `test_legacy_tables_gone`), goldens (D10). (4) Swap window and rollback: "Expand/contract analysis", D11, D12. (5) Docs: Task 7. (6) Tests: Task 2 (up/down, refusal, fresh head, lock, golden), Tasks 3–5 (code paths without the old tables), Task 8 (rehearsal).
- **Placeholders:** the 0006 DDL strings are filled in mechanically from 0001 lines 87–129, and a unit test proves the copy is exact. "PR number" and "merge time" are unknown until merge. No TBDs.
- **Names used across tasks:** `CREATE_OLD_TABLES`, `LEGACY_ONLY_ACTIVE_RUNS`, `ACTIVE_STATUSES`, `COPY_TOOL_REF` (Task 2, used in its tests); `CheckpointReadFailed` (Task 3, checked in Task 5's grep); `CHECKPOINT_TABLES` (three entries, Task 5).

## Plan-time measurements (2026-11-28, before any change)

- Unit tests on `chore/phase7-deps-watch` (with the uncommitted watch changes): `LANGSMITH_TRACING=false .venv/Scripts/python -m pytest -m "not integration" --no-cov -q`: 2615 passed, 1027 deselected, 6 warnings, in 303 s.
- `tests/unit/api/test_execution_observability.py:87` pins the `checkpoint_sync_failed` guidance text, so Task 3 Step 2 updates it.
- Integration tests: not run (Docker down).
