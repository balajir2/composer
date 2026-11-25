# SQLAlchemy Migration Phase 5 — Checkpointer and Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the custom `PrismaCheckpointSaver` with LangGraph's official `AsyncPostgresSaver` (psycopg 3 pool, tables created by Alembic), move LISTEN/NOTIFY from asyncpg to psycopg, and delete the last Prisma client plumbing (`prisma_lifespan`, `get_db`, `enqueue_execution(db=)`), so that after this phase nothing in `src` imports `prisma`, `asyncpg` or `src.storage`. A run that paused before the cutover still resumes after it, and a rollback never strands a paused run.

**Architecture:** One new package, `src/checkpoints/`, owns everything about checkpoints: the official saver on its own `psycopg_pool.AsyncConnectionPool` (`saver.py`), a SQLAlchemy port of the old saver that reads and writes the old `langgraph_checkpoints` / `langgraph_checkpoint_writes` tables byte-for-byte as Prisma did (`legacy.py`, kept until phase 7 for the copy tool and rollback), a two-way copy tool that converts channel-version formats (`copy.py`, `versions.py`), the deploy guard and copy-on-resume safety net (`sync.py`), and the app-state registry (`runtime.py`). The saver's tables come from Alembic revision `0004`, never from `setup()`. NOTIFY is sent through the SQLAlchemy pool; LISTEN runs on ONE process-wide psycopg connection to Neon's direct (non-pooler) endpoint that is open only while at least one WebSocket is watching, and fans out to per-execution subscribers.

**Tech Stack:** SQLAlchemy 2 async + psycopg 3.3, `psycopg-pool` 3.3, `langgraph-checkpoint-postgres` 3.2.1 (on `langgraph-checkpoint` 4.3.2, already locked), LangGraph 1.2.22, Alembic, PostgreSQL 15, FastAPI, pytest + pytest-asyncio, pyright strict, ruff.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) — §2.2 (what replaces each Prisma piece), §2.3 dependencies, §3.3 phase 5 row, **§3.4 checkpointer cutover**, "Known schema divergence", §4.5 lifespan, §5 "Phase 5 extras", §7 risks. Previous plans and their Outcome sections: [phase 4a](2026-11-24-sqlalchemy-phase-4a-executions-and-events.md), [phase 4b](2026-11-24-sqlalchemy-phase-4b-approvals-and-resume.md). Parallel work: [Codex handoff for telemetry phase 5](../notes/2026-11-25-codex-handoff-telemetry-phase-5.md).

## Global Constraints

All Global Constraints from phases 1–4b apply unchanged:

- Repos are the only SQLAlchemy query code; repos are imported as modules (`from src.db.repos import checkpoints as checkpoints_repo`). The official saver's own SQL (inside `langgraph-checkpoint-postgres`) is the one sanctioned exception: it is library code behind LangGraph's `BaseCheckpointSaver` interface.
- Repos return frozen-dataclass records, never ORM objects; they never commit. Callers commit.
- Writes take timestamps from `utcnow()` (`src/db/types.py`).
- **Short-session rule.** NEVER hold a SQLAlchemy session across a graph run (`compiled.ainvoke`/`aget_state`), a saver call, an LLM call, network I/O, `asyncio.sleep`, or a WebSocket wait. Every function that mixes DB work and slow work gets a unit ordering test using `RecordingSessionmaker` + `assert_no_network_inside_session` (`tests/unit/db_fakes.py`).
- **The saver's pool is separate from SQLAlchemy's** (decision D4). The saver checks a connection out of its own pool for each method call (`aget_tuple`, `aput`, `aput_writes`, `alist`, `adelete_thread`) and returns it when the call ends (`psycopg_pool.AsyncConnectionPool.connection()` inside the library's `_cursor`), so a graph run holds no saver connection between super-steps and none while a node calls an LLM. A checkout waits at most `checkpointer_pool_timeout_seconds` (default 10) and then raises `psycopg_pool.PoolTimeout`, which fails the run (fail closed) instead of hanging.
- Out-of-request code takes an `async_sessionmaker[AsyncSession]` parameter or uses `get_registered_sessionmaker()`; it gets the saver from `get_registered_checkpointer()` (Task 7) and raises a clear `RuntimeError` when either is `None`. Routes use `require_sessionmaker(request)` (503 `database unavailable`, fail closed).
- **Fail closed.** A resume whose checkpoint cannot be found fails the run with `failure_code="checkpoint_missing"`; it never restarts the graph from scratch. The app refuses to start if the saver's tables are not at the version the installed library expects. The deploy guard blocks the deploy; it has no "skip" switch.
- **Never log secrets or execution payloads.** Never log a connection string, a checkpoint, a channel value or a pending write. Log exception class names, execution ids, thread ids and counts only. The CLI prints ids and counts only.
- CI-parity pyright before every commit: `uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`. Also `uv run ruff check src tests` and `uv run ruff format --check src tests`.
- No edits to `prisma/` (frozen). `pyproject.toml` / `uv.lock` change **only in Task 2**, and only after Balaji approves D1. `src/db/migrations/**` changes only in Task 3 (revision `0004`). `.github/**` changes only in Task 6 (the deploy-guard step). If a task hits an error it can only fix by editing a policy file outside its list, it stops and reports BLOCKED.
- Commits authored by Balaji Rajan `<balajirajan@gmail.com>` (verify with `git log -1 --format='%an <%ae>'`), ending with one `Co-Authored-By:` line naming the model that actually did the work, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Additionally, for this phase:

- **Old tables are read-only history except for the copy tool and deletes.** After the cutover nothing but `LegacyCheckpointSaver` (driven by the copy tool or copy-on-resume) and `checkpoints_repo.delete_threads` touches `langgraph_checkpoints` / `langgraph_checkpoint_writes`. They are dropped in phase 7, not here.
- **Never call `AsyncPostgresSaver.setup()` at runtime or in deploys.** Alembic owns the tables (D3). Tests call `setup()` only against a throwaway schema to prove Alembic produces the same tables.
- **Oracles are not edited** except the lines listed in Task 8 Step 5 (4a/4b fixtures that reach into asyncpg internals) and the one cross-saver test Task 7 deletes. The phase 5 oracle (`tests/integration/sqla_phase5/`) has no adapter: every seam it uses (`app.state.checkpointer` as a `BaseCheckpointSaver`, HTTP routes, `notify_execution_event(execution_id, *, seq)`, `create_app()`) keeps its interface.
- **CI minutes are scarce.** Every task verifies locally against a throwaway Postgres 15 container (`docker run -d --name composer-p5-pg -e POSTGRES_PASSWORD=composer -e POSTGRES_USER=composer -e POSTGRES_DB=composer_test -p 55432:5432 postgres:15`, `TEST_DATABASE_URL=postgresql://composer:composer@localhost:55432/composer_test`, then `uv run prisma migrate deploy` and `uv run alembic upgrade head` with `DATABASE_URL=$TEST_DATABASE_URL`). The whole phase ships as ONE PR; CI runs once on it.

## Decisions for Balaji (each with a recommendation)

**Confirmed by Balaji on 2026-11-25:** all recommendations accepted, including **D1** (add `langgraph-checkpoint-postgres>=3.2.1,<4`, `psycopg-pool>=3.3.3,<4` and an explicit `langgraph-checkpoint>=4.3.2,<5`, locked so nothing else moves). The deploy guard (D5) has no override.

**Status: awaiting Balaji.** Task 1 (the oracle) can start before these are confirmed. Task 2 needs D1. Tasks 3–9 assume the recommendations below; a different answer changes the named task only.

| # | Topic | Recommendation |
|---|---|---|
| D1 | **Dependency change** (needs approval: it edits `pyproject.toml` and `uv.lock`). The spec says `langgraph-checkpoint` must go from 4.1.1 to ≥ 4.3. **Re-checked 2026-11-25: `uv.lock` already pins `langgraph-checkpoint` 4.3.2** (an earlier lock refresh moved it), so no LangGraph upgrade is needed. A dry resolve of `langgraph==1.2.22`, `langgraph-checkpoint==4.3.2`, `psycopg[binary]==3.3.9` plus the two new packages succeeds with `langgraph-checkpoint-postgres` 3.2.1 (requires `langgraph-checkpoint>=4.3.0,<5`, `psycopg>=3.2.0`, `psycopg-pool>=3.2.0`, `orjson>=3.11.5`; `orjson` is already locked) and `psycopg-pool` 3.3.3. The package ships `py.typed`. Breaking-change notes: the official saver stores channel versions as zero-padded strings (`"000…0005.<random>"`) where ours stores integers, so copied runs need version conversion (Task 5); it stores checkpoints as JSONB plus blobs, so old and new tables are not interchangeable. | **Approve:** add `langgraph-checkpoint-postgres>=3.2.1,<4`, `psycopg-pool>=3.3.3,<4`, and an explicit floor `langgraph-checkpoint>=4.3.2,<5` (we now depend on its API directly). Lock with `uv lock --upgrade-package langgraph-checkpoint-postgres --upgrade-package psycopg-pool` so nothing else moves. **asyncpg removal: phase 6**, as the spec says. After phase 5 `src` no longer imports it (a new boundary test enforces that), but `tests/_rate_limit_reset.py` still does, and keeping this phase's only dependency edit purely additive keeps the rollback simple. |
| D2 | **Runs paused before the cutover.** Options: (a) copy their snapshots to the new tables with the copy tool before deploying; (b) let them drain on the old saver (not possible: the new revision has no Prisma saver, and an approval can wait up to `approval_wait_timeout_hours`); (c) refuse the deploy while any exists. The spec measured 0 paused or active runs; that number must be re-checked on the day. | **(c) + (a) + a safety net.** The deploy guard (D5) refuses while any `queued`/`running`/`waiting_approval` run has snapshots only in the old tables (more exactly: the old tables' newest checkpoint id for its thread is newer than the new tables', or the new tables have none). The fix is the copy tool, `composer checkpoints copy --to postgres --execution <id>`, which refuses anything but `waiting_approval` unless `--force`. **Safety net:** `LangGraphExecutor.resume` first calls `sync.bring_forward(...)`, which copies the thread old → new when the old tables are ahead, so a run that paused on an old instance *after* the guard ran (the window while Cloud Run switches traffic) still resumes. **Re-verify on the day** with `composer checkpoints guard` run read-only against production (Task 9 Step 6, with Balaji's go-ahead), which prints counts per status. |
| D3 | **Who creates the saver's tables.** The spec's §2.2 says deploys run `AsyncPostgresSaver.setup()`; CLAUDE.md says schema changes go through Alembic only. | **Alembic revision `0004_langgraph_saver_tables`** with the library's final DDL copied verbatim (tables `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`; three `thread_id` indexes; `task_path` column; nullable `blob`), plus `INSERT INTO checkpoint_migrations (v) SELECT generate_series(0, 9)` so the library considers all its 10 migrations applied. Plain `CREATE TABLE` (no `IF NOT EXISTS`): a table of that name that already exists fails the deploy loudly. Indexes are created without `CONCURRENTLY` (the tables are new and empty, and `env.py` runs all revisions in one transaction). A test runs `setup()` in a scratch schema and asserts identical columns, defaults, keys and indexes; the app refuses to start when `max(v)` differs from `len(MIGRATIONS) - 1`, so a future library upgrade that adds a migration fails at boot (and in CI) until an Alembic revision follows. Models are added for parity. **Divergence:** like `0002`/`0003`, these tables do not exist in `prisma/schema.prisma`; phase 6's squashed base must include them and the 10 `checkpoint_migrations` rows. |
| D4 | **Pools and connections.** The spec says "the shared psycopg pool". SQLAlchemy keeps its own pool of psycopg connections in a transaction (non-autocommit) mode with tuple rows; the saver needs `autocommit=True`, `row_factory=dict_row`, and it pipelines statements. One pool cannot serve both without per-checkout reconfiguration. LISTEN needs a session-level connection; Neon documents that LISTEN does not work through its PgBouncer transaction-mode pooler (`-pooler` host), and today's asyncpg code LISTENs on `DATABASE_URL`, which in production is the pooler host — so live WebSocket push may already be degraded to the 15 s poll in production (Task 9 Step 7 checks). | **Three separate things.** (1) SQLAlchemy pool unchanged (5 + 5 per instance, pooler host). (2) Saver pool: `AsyncConnectionPool` on the pooler host, `min_size=1`, `max_size=5`, `timeout=10`, `max_idle=300`, connection kwargs `autocommit=True, row_factory=dict_row, prepare_threshold=None`. (3) LISTEN: ONE connection per process on the **direct** endpoint (setting `database_direct_url`, else derived from `DATABASE_URL` by removing `-pooler` from the host's first label, exactly what the deploy workflow's `sed` does), opened only while at least one WebSocket watches and closed 30 s after the last one leaves (so it never keeps Neon compute awake by itself). NOTIFY goes through SQLAlchemy's pool (`pg_notify` works on the pooler; it is delivered at commit). Budget per instance: pooler 10 + 5 = 15 (Prisma's pool, about 3, goes away); direct 1. At 3 instances: 45 pooler, 3 direct, plus 1–2 one-shot direct connections during deploys. **Escape hatch:** `checkpointer_use_direct_endpoint=true` points the saver pool at the direct endpoint without a code change, if the library's pipelined writes misbehave through the pooler (checked in Task 9 Step 7). |
| D5 | **Deploy guard mechanism.** Options: a step in the deploy workflow, or a check at app startup. | **Deploy workflow step**, right after `alembic upgrade head` and before `gcloud run deploy`: `uv run composer checkpoints guard`. It exits 1 and prints each blocking execution id with its status, so the job fails and the previous revision keeps serving. Not at startup: a startup refusal would crash-loop the new revision (the 2026-08 incident pattern) and would also block a rollback. **Override:** none (fail closed). The runbook: copy `waiting_approval` runs (`composer checkpoints copy --to postgres --all-waiting`), wait for `running`/`queued` ones to finish, or cancel them; then re-run the deploy. The guard stays in the workflow until phase 7 drops the old tables. |
| D6 | **Rollback.** During `gcloud run deploy` traffic moves to the new revision, but old instances finish their in-flight claim-and-run requests (up to the request timeout) on `PrismaCheckpointSaver`. A rollback sends traffic back to a revision that reads only the old tables. | **Forward window:** a run that pauses on a draining old instance writes only old tables; copy-on-resume (D2) brings it forward on resume. **Rollback:** before moving traffic back, run `composer checkpoints copy --to legacy --all-waiting` (new → old, versions converted to integers, which the old revision requires: the base saver's `get_next_version` raises `NotImplementedError` on string versions). Without it, the old revision cannot resume runs that paused after the cutover (Task 1 records what it does instead). **Roll-forward again:** copy-on-resume compares newest checkpoint ids (they are time-ordered UUIDv6 strings) and copies only when the old tables are ahead, so whichever store saw the latest step wins. Old tables stay untouched until phase 7, so no rollback needs a database restore. |
| D7 | **Deletes.** The spec says deletes switch to the saver's `adelete_thread`. That call takes one thread, runs on the saver's own autocommit connection, and so cannot join the SQLAlchemy transaction that deletes the execution rows (4a D6 made that delete one transaction). | **Delete in the same SQLAlchemy transaction** through `checkpoints_repo.delete_threads`, which now clears the three saver tables as well as the two old ones, with one `DELETE … WHERE thread_id = ANY(:ids)` per table. A contract test proves it is equivalent to `adelete_thread` (afterwards the saver's `aget_tuple` is `None` and `alist` is empty), and a drift test parses the library's `MIGRATIONS` and fails if it ever creates a table with a `thread_id` column that `delete_threads` does not clear. |
| D8 | **NOTIFY sender.** Options: keep `notify_execution_event(execution_id, *, seq)` as a separate short SQLAlchemy session after the event commit; or fold `pg_notify` into `events_repo.append`'s transaction. | **Keep the function and its signature**, sending `SELECT pg_notify(:channel, :payload)` in its own short session. The executor, `events_wrapper` and the 4a ordering oracle (`test_event_row_is_committed_before_its_notify_is_sent`) do not change, and the dedicated asyncpg NOTIFY connection (and `close_notify_connection`) disappear. Folding into `append` (one fewer round trip per event) is a follow-up. |
| D9 | **Where the old saver lives until phase 7.** The copy tool and copy-on-resume need to read and write the old tables, and phase 6 removes the Prisma client. | **Port it to SQLAlchemy** as `src/checkpoints/legacy.py:LegacyCheckpointSaver` (same 4-byte-length-prefixed serialization, same upsert semantics), proven byte-compatible with `PrismaCheckpointSaver` while both exist (Task 4) and by a golden-row fixture afterwards. One deliberate change: the newest checkpoint is chosen by `checkpoint_id DESC` (as the official saver does), not `created_at DESC`, because `created_at` has millisecond precision and two checkpoints of one run can share it. Phase 7 deletes `legacy.py` with the tables. **Write semantics differ (oracle finding, `langgraph-checkpoint-postgres` 3.2.1 `aio.py`/`base.py`):** the official saver's `aput_writes` UPSERTS only when every channel in the batch is special (`WRITES_IDX_MAP`: `__error__`, `__interrupt__`, ...) and otherwise does `INSERT ... ON CONFLICT DO NOTHING` (first write wins for ordinary channels); `LegacyCheckpointSaver` (like Prisma's) upserts every write (last wins). The copy tool must therefore preserve whichever rows exist in the source and never rely on overwrite semantics; the oracle pins only what both savers do (repeating an identical write adds nothing; special channels are replaced). |

---

## Every remaining Prisma / asyncpg / `src.storage` use in `src` (main at `77e5e80`)

Found with `grep -rnE "^\s*(from|import) (prisma|asyncpg|src\.storage)\b" src` plus every `get_db`, `get_checkpointer`, `get_event_bus`, `db=` and `PrismaCheckpointSaver` reference.

| File:line | Use | Task |
|---|---|---|
| `src/storage/checkpointer.py` (whole file, `:44` `from prisma import Base64, Prisma`) | `PrismaCheckpointSaver`: every read/write of `langgraph_checkpoints` / `langgraph_checkpoint_writes` | 4 (SQLAlchemy port as `LegacyCheckpointSaver`), 7 (file deleted) |
| `src/storage/db.py:15-19` | `from prisma.engine.errors import EngineConnectionError`, `from prisma import Prisma` | 7 (file deleted) |
| `src/storage/db.py:44-73` | `_connect_with_retry` (Prisma connect policy) | 7 (replaced by the saver pool's open-with-retry in `src/checkpoints/saver.py`) |
| `src/storage/db.py:76-118` | `prisma_lifespan`: connects Prisma, sets `app.state.db` and `app.state.checkpointer`, closes the NOTIFY connection, the Cloud Tasks client and Prisma | 7 (`checkpointer_lifespan`; Cloud Tasks close moves to `src/main.py`), 8 (NOTIFY connection removed) |
| `src/storage/db.py:121-146` | `get_db`, `get_checkpointer`, `get_event_bus` | 7 (`get_db` deleted; the other two move to `src/api/app_state.py`) |
| `src/main.py:47,65` | `from src.storage.db import prisma_lifespan`; `async with prisma_lifespan(app), sqlalchemy_lifespan(app)` | 7, 8 |
| `src/execution/cloud_tasks.py:96-150` | `_run_in_process(execution_id, kind, db)` builds `PrismaCheckpointSaver(db)` | 7 (uses `get_registered_checkpointer()`) |
| `src/execution/cloud_tasks.py:158-170` | `enqueue_execution(execution_id, *, kind, db)` | 7 (`db` dropped) |
| `src/api/executions.py:13` | `from prisma import Prisma` (runtime import, annotation only) | 7 |
| `src/api/executions.py:35,141,236` | `get_db`; `create_execution(db: Prisma = Depends(get_db))`; `enqueue_execution(..., db=db)` | 7 |
| `src/api/executions.py:523,588` | `resume_execution(db: Any = Depends(get_db))`; `enqueue_execution(..., db=db)` | 7 |
| `src/api/run.py:35,38,99,202` | `get_db, get_event_bus` import; `TYPE_CHECKING` `Prisma`; `db: Prisma = Depends(get_db)`; `enqueue_execution(..., db=db)` | 7 |
| `src/api/approval_email.py:48,128,183` | `get_db`; `db: Any = Depends(get_db)`; `enqueue_execution(..., db=db)` | 7 |
| `src/api/internal.py:67` | `from src.storage.db import get_checkpointer, get_db, get_event_bus` | 7 |
| `src/api/internal.py:414,532` | `get_checkpointer(request)` (claim-and-run, poll-file-triggers) | 7 (import moves to `src/api/app_state.py`) |
| `src/api/internal.py:509,685` | `poll_file_triggers(db: Any = Depends(get_db))`; `enqueue_execution(..., db=db)` | 7 |
| `src/engine/events_notify.py:23-58` | `import asyncpg`; dedicated cached NOTIFY connection on `DATABASE_URL` | 8 |
| `src/api/events_ws.py:15,176-224` | `import asyncpg`; one LISTEN connection per WebSocket on `DATABASE_URL` | 8 |
| `src/db/repos/checkpoints.py:10` | `delete_threads` (already SQLAlchemy; old tables only) | 4 (legacy reads/writes added), 7 (saver tables added, D7) |
| `src/engine/langgraph_executor.py:381` | `TODO(phase 5)` in `resume`'s docstring | 7 (copy-on-resume + fail-closed missing checkpoint) |
| `src/config.py:44-60,66-69` | comments/descriptions naming Prisma's connect and pool | 7 (wording), 3/7/8 (new settings) |

Not Prisma, kept: `src/storage_providers/**` (file providers; `src.storage_providers` is not `src.storage`, and the boundary test already treats it as a lookalike). Comments in `src/db/**` that explain Prisma history stay until phase 6.

**Done criteria after phase 5** (Task 9 runs them; each prints nothing):

```bash
grep -rnE "^\s*(from|import) (prisma|asyncpg)\b" src
grep -rnE "^\s*from src\.storage(\.| import)|^\s*import src\.storage\b" src tests
grep -rn "PrismaCheckpointSaver\|prisma_lifespan\|get_db\b\|close_notify_connection" src tests
grep -rn "db=db" src
test ! -e src/storage && echo ok   # prints "ok": the package is deleted
```

and `MIGRATED_PATHS` in `tests/unit/db/test_prisma_import_boundary.py` is replaced by "all of `src`" (Task 9).

## Coordination with Codex (telemetry phase 5)

Consistent with the [handoff note](../notes/2026-11-25-codex-handoff-telemetry-phase-5.md). Until this phase's PR merges, Codex must not edit:

- `src/storage/**` (deleted here), `src/checkpoints/**` (new here), `src/main.py` (lifespan), `src/execution/cloud_tasks.py`, `src/engine/events_notify.py`, `src/engine/notifications.py` (new), `src/api/events_ws.py`, `src/api/app_state.py` (new), `src/db/repos/checkpoints.py`, `src/db/models/checkpoints.py`, `src/cli/main.py`, `src/cli/checkpoints.py` (new).
- In `src/engine/langgraph_executor.py`: `__init__`, `_emit`, and the head of `resume()` (before `compiled.ainvoke`). Codex may edit the bodies of `_mark_waiting_approval`, `_mark_completed` and `_mark_failed` (its attempt writes go there); whoever merges second rebases.
- In `src/api/executions.py`, `src/api/run.py`, `src/api/approval_email.py`, `src/api/internal.py`: this phase only removes the `db` parameters, the `db=db` arguments and the `src.storage` imports, and changes where `get_checkpointer`/`get_event_bus` are imported from. Codex's claim-transaction and resume-transaction edits in the same files are expected; keep those hunks away from the route signatures and rebase after our merge.
- **Alembic:** this phase takes `0004_langgraph_saver_tables` (down revision `0003_assignment_user_fks`). If Codex's `execution_attempts` revision merges first, rebase ours onto it (change `down_revision`, keep our id); otherwise Codex's revision becomes `0005_…` with `down_revision = "0004_langgraph_saver_tables"`. `uv run alembic heads` must print exactly one head before either PR merges.
- `src/telemetry/attributes.py`: this phase adds one value, `"checkpoint_missing"`, to the `failure_code` allow-list (Task 7). Tell Codex.

## File structure

| Path | Responsibility | Task |
|---|---|---|
| `tests/integration/sqla_phase5/` (`__init__.py`, `conftest.py`, `test_pause_resume.py`, `test_history_and_delete.py`, `test_dev_mode.py`, `test_events_live.py`) | Behaviour oracle on today's code | 1 |
| `pyproject.toml`, `uv.lock` | D1 dependencies | 2 |
| `src/db/migrations/versions/0004_langgraph_saver_tables.py` | Saver tables + `checkpoint_migrations` rows | 3 |
| `src/db/models/checkpoints.py` | Add `SaverCheckpoint`, `SaverCheckpointBlob`, `SaverCheckpointWrite`, `SaverCheckpointMigration` | 3 |
| `src/checkpoints/__init__.py`, `src/checkpoints/schema.py` | `SAVER_MIGRATION_COUNT`, `assert_saver_schema_current(conn)` | 3 |
| `src/db/repos/checkpoints.py` | Legacy table reads/writes, newest-id lookups, unsynced-run query, `delete_threads` across five tables | 4, 6, 7 |
| `src/checkpoints/legacy.py` | `LegacyCheckpointSaver` (SQLAlchemy, old tables) | 4 |
| `tests/fixtures/legacy_checkpoint_rows.json` | Golden rows written by `PrismaCheckpointSaver` | 4 |
| `src/checkpoints/versions.py` | Channel-version conversion both ways | 5 |
| `src/checkpoints/copy.py` | `copy_thread(source, target, thread_id, *, to)` | 5 |
| `src/checkpoints/saver.py` | Saver pool construction, open-with-retry, `checkpointer_lifespan`, `open_one_shot_saver` | 5 (one-shot), 7 (lifespan) |
| `src/cli/checkpoints.py`, `src/cli/main.py` | `composer checkpoints copy|guard` | 5, 6 |
| `src/checkpoints/sync.py` | `find_unsynced`, `bring_forward` | 6, 7 |
| `.github/workflows/deploy-gcp.yml` | Guard step | 6 |
| `src/checkpoints/runtime.py` | `register_checkpointer` / `get_registered_checkpointer` | 7 |
| `src/api/app_state.py` | `get_checkpointer(request)`, `get_event_bus(request)` | 7 |
| `src/storage/` | Deleted | 7 |
| `src/engine/events_notify.py` | NOTIFY via SQLAlchemy | 8 |
| `src/engine/notifications.py` | `ExecutionEventListener` (one lazy LISTEN connection, fan-out) | 8 |
| `src/db/url.py` | `to_libpq_url`, `direct_endpoint_url` | 5 (libpq), 8 (direct) |
| `src/api/events_ws.py` | Subscribe through the listener | 8 |
| `src/config.py` | `checkpointer_pool_*`, `checkpointer_use_direct_endpoint`, `database_direct_url` | 5, 8 |
| docs, CHANGELOG, boundary test | Lock and record | 9 |

---

### Task 1: Behaviour oracle on today's code

The oracle describes what users and operators can observe about checkpoints and live events today, on `PrismaCheckpointSaver` and asyncpg. It must pass unchanged on the finished phase. It uses only seams that keep their interface: HTTP routes, `create_app()` with its lifespan, `app.state.checkpointer` (a `BaseCheckpointSaver`), `app.state.event_bus`, and `src.engine.events_notify.notify_execution_event(execution_id, *, seq)`. Row counts are read with raw SQL over whichever checkpoint tables exist, so the same helper counts old tables today and old + new tables after Task 3.

**Files:** Create `tests/integration/sqla_phase5/__init__.py` (empty), `conftest.py`, `test_pause_resume.py`, `test_history_and_delete.py`, `test_dev_mode.py`, `test_events_live.py`.

**Interfaces:**
- Consumes (from `tests/integration/sqla_phase4a/conftest.py`, unchanged): `phase4a_env`, `app`, `sessionmaker_`, `make_user`, `member`, `db` (`Db`), `exec_api`, `Member`, `approval_graph`, `node`, `edge`, `start_node`, `end_node`, `install_email_recorder`, `jbody`, `unique`, `HTTPX_MARK`, `purge_execution_artifacts`. From `tests/integration/sqla_phase4b/conftest.py`: `resume`.
- Produces: `running_app()` async context manager yielding `RunningApp(app, client, sm)` with Cloud Tasks enqueue bound to that app; `checkpoint_row_count(sm, thread_id) -> int`; `wait_for_status(sm, execution_id, statuses, timeout) -> str`; `two_approval_graph()`.

- [ ] **Step 1: Write `conftest.py`**

```python
"""Fixtures for the SQLAlchemy phase 5 behaviour oracle (checkpointer and notifications).

Written against the post-phase-4 code (PrismaCheckpointSaver, asyncpg LISTEN/NOTIFY). These
tests must keep passing UNCHANGED after the official AsyncPostgresSaver and psycopg
notifications replace them. There is no adapter: every seam used here keeps its interface:
HTTP routes, create_app() + lifespan, app.state.checkpointer (a BaseCheckpointSaver),
app.state.event_bus and notify_execution_event(execution_id, *, seq).

"A restart" is a second create_app() with its own lifespan: a fresh checkpointer (fresh pool,
no in-memory state) reading what the first one wrote.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.main import create_app
from tests.integration.sqla_phase4a.conftest import (
    app,
    db,
    edge,
    end_node,
    exec_api,
    make_user,
    member,
    node,
    phase4a_env,
    sessionmaker_,
    start_node,
)

__all__ = [
    "app", "db", "exec_api", "make_user", "member", "phase4a_env", "sessionmaker_",
]

# Every checkpoint table that has ever existed. Old: Prisma's. New: langgraph-checkpoint-postgres.
CHECKPOINT_TABLES = (
    "langgraph_checkpoints",
    "langgraph_checkpoint_writes",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)
ENQUEUE_SITES = (
    "src.api.executions.enqueue_execution",
    "src.api.approval_email.enqueue_execution",
    "src.api.run.enqueue_execution",
    "src.api.internal.enqueue_execution",
)


@dataclass
class RunningApp:
    app: FastAPI
    client: AsyncClient
    sm: async_sessionmaker[AsyncSession]


@contextlib.asynccontextmanager
async def running_app(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[RunningApp]:
    """A fresh app process: own lifespan, own checkpointer. Enqueues run claim-and-run on IT."""
    get_settings.cache_clear()
    fresh = create_app()
    async with fresh.router.lifespan_context(fresh):
        async with AsyncClient(transport=ASGITransport(app=fresh), base_url="http://p5") as c:

            async def _enqueue(execution_id: str, *, kind: str, **_ignored: Any) -> None:
                resp = await c.post(
                    "/internal/claim-and-run", json={"executionId": execution_id, "kind": kind}
                )
                resp.raise_for_status()

            for site in ENQUEUE_SITES:
                monkeypatch.setattr(site, _enqueue)
            yield RunningApp(fresh, c, fresh.state.db_sessionmaker)


async def checkpoint_row_count(sm: async_sessionmaker[AsyncSession], thread_id: str) -> int:
    """Rows for `thread_id` across every checkpoint table that exists in this database."""
    total = 0
    async with sm() as s:
        for table in CHECKPOINT_TABLES:
            exists = await s.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table})
            if exists:
                total += int(
                    await s.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE thread_id = :t"),  # noqa: S608
                        {"t": thread_id},
                    )
                    or 0
                )
    return total


async def wait_for_status(
    sm: async_sessionmaker[AsyncSession], execution_id: str, statuses: set[str], timeout: float
) -> str:
    """Poll the row (fresh short session each time) until its status is in `statuses`."""
    deadline = asyncio.get_running_loop().time() + timeout
    status = ""
    while asyncio.get_running_loop().time() < deadline:
        async with sm() as s:
            status = str(
                await s.scalar(
                    text("SELECT status FROM workflow_executions WHERE id = :i"),
                    {"i": execution_id},
                )
            )
        if status in statuses:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"{execution_id} stayed {status!r}, wanted {sorted(statuses)}")


def two_approval_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """start -> ua1 -> (approved) set first -> ua2 -> (approved) set second -> end."""
    nodes = [
        start_node(),
        node("ua1", "user-approval", approvalMessage="First", approverEmail="a@example.com"),
        node("one", "set-state", stateKey="first", stateValue="yes"),
        node("ua2", "user-approval", approvalMessage="Second", approverEmail="a@example.com"),
        node("two", "set-state", stateKey="second", stateValue="yes"),
        end_node(),
    ]
    edges = [
        edge("s", "ua1"),
        edge("ua1", "one", branch="approved"),
        edge("one", "ua2"),
        edge("ua2", "two", branch="approved"),
        edge("two", "e"),
    ]
    return nodes, edges
```

- [ ] **Step 2: Write `test_pause_resume.py`** (pause in one process, resume in another; both decisions; chained pause across two restarts)

```python
"""Pause in one app process, resume in a fresh one: the checkpoint survives a restart."""

import pytest

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK, Db, Member, approval_graph, install_email_recorder, jbody,
)
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import running_app, two_approval_graph

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _pause(db: Db, member: Member, monkeypatch: pytest.MonkeyPatch, graph=approval_graph) -> str:
    install_email_recorder(monkeypatch)
    nodes, edges = graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    async with running_app(monkeypatch) as a:
        r = await a.client.post(
            "/executions", json={"workflowId": wid, "input": "x"}, headers=member.headers
        )
        assert r.status_code == 202, r.text
        eid = jbody(r)["id"]
    row = await db.must(eid)
    db.tracked_executions.append(eid)
    db.tracked_threads.append(row.thread_id)
    assert row.status == "waiting_approval", (row.status, row.error)
    return eid


@pytest.mark.parametrize(("decision", "result"), [("approved", "approved"), ("rejected", "rejected")])
async def test_resume_after_restart_continues_the_same_run(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch, decision: str, result: str
) -> None:
    eid = await _pause(db, member, monkeypatch)
    async with running_app(monkeypatch) as b:
        r = await resume(b.client, member, eid, decision)
        assert r.status_code == 200, r.text
    row = await db.must(eid)
    assert row.status == "completed", (row.status, row.error)
    # The branch taken after the pause proves the decision reached the saved graph state.
    assert row.variables["result"] == result
    assert "_pending_approval_node" not in row.variables


async def test_chained_pause_survives_two_restarts(
    db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid = await _pause(db, member, monkeypatch, graph=two_approval_graph)
    async with running_app(monkeypatch) as b:
        assert (await resume(b.client, member, eid, "approved")).status_code == 200
    row = await db.must(eid)
    assert row.status == "waiting_approval"
    assert row.variables["_pending_approval_node"] == "ua2"
    assert row.variables["first"] == "yes"
    async with running_app(monkeypatch) as c:
        assert (await resume(c.client, member, eid, "approved")).status_code == 200
    row = await db.must(eid)
    assert row.status == "completed", (row.status, row.error)
    assert (row.variables["first"], row.variables["second"]) == ("yes", "yes")
```

- [ ] **Step 3: Write `test_history_and_delete.py`**

```python
"""Checkpoint history through the BaseCheckpointSaver interface, and execution delete."""

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK, Db, ExecApi, Member, approval_graph, install_email_recorder, jbody,
)
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import checkpoint_row_count

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def _paused(exec_api: ExecApi, db: Db, member: Member, mp: pytest.MonkeyPatch) -> tuple[str, str]:
    install_email_recorder(mp)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    r = await exec_api.create(member, wid, "x")
    eid = jbody(r)["id"]
    row = await db.must(eid)
    assert row.status == "waiting_approval"
    return eid, row.thread_id


async def test_history_is_newest_first_and_latest_carries_the_interrupt(
    app, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    _eid, thread = await _paused(exec_api, db, member, monkeypatch)
    cfg = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    history = [t async for t in app.state.checkpointer.alist(cfg)]
    assert len(history) >= 2
    ids = [t.config["configurable"]["checkpoint_id"] for t in history]
    assert ids == sorted(ids, reverse=True)  # newest first; ids are time-ordered
    for newer, older in zip(history, history[1:]):
        assert newer.parent_config["configurable"]["checkpoint_id"] == older.config["configurable"]["checkpoint_id"]
    latest = await app.state.checkpointer.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}})
    assert latest is not None and latest.config == history[0].config
    assert any(channel == "__interrupt__" for _task, channel, _v in latest.pending_writes)


async def test_resume_adds_history_and_keeps_the_old_entries(
    app, client: AsyncClient, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid, thread = await _paused(exec_api, db, member, monkeypatch)
    cfg = {"configurable": {"thread_id": thread, "checkpoint_ns": ""}}
    before = [t.config["configurable"]["checkpoint_id"] async for t in app.state.checkpointer.alist(cfg)]
    assert (await resume(client, member, eid, "approved")).status_code == 200
    after = [t.config["configurable"]["checkpoint_id"] async for t in app.state.checkpointer.alist(cfg)]
    assert after[-len(before):] == before and len(after) > len(before)


async def test_delete_removes_every_checkpoint_row_of_the_thread_only(
    app, client: AsyncClient, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid, thread = await _paused(exec_api, db, member, monkeypatch)
    _other, other_thread = await _paused(exec_api, db, member, monkeypatch)
    assert (await resume(client, member, eid, "approved")).status_code == 200
    assert await checkpoint_row_count(app.state.db_sessionmaker, thread) > 0
    assert (await exec_api.delete(member, eid)).status_code == 204
    assert await checkpoint_row_count(app.state.db_sessionmaker, thread) == 0
    assert await checkpoint_row_count(app.state.db_sessionmaker, other_thread) > 0
    assert await app.state.checkpointer.aget_tuple({"configurable": {"thread_id": thread, "checkpoint_ns": ""}}) is None


async def test_active_run_delete_is_refused_and_keeps_its_checkpoints(
    app, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    eid, thread = await _paused(exec_api, db, member, monkeypatch)
    count = await checkpoint_row_count(app.state.db_sessionmaker, thread)
    r = await exec_api.delete(member, eid)
    assert r.status_code == 409, r.text
    assert await checkpoint_row_count(app.state.db_sessionmaker, thread) == count
```

(Before writing, confirm the delete status codes against `tests/integration/sqla_phase4a/test_delete.py` and use the same ones; the 204/409 above are what that file asserts at `77e5e80`.)

- [ ] **Step 4: Write `test_dev_mode.py`** (the real `enqueue_execution` with `CLOUD_TASKS_SERVICE_ACCOUNT` unset runs claim-and-run as an in-process task, today with a Prisma saver built from `db`)

```python
"""Dev-mode fallback: the real enqueue_execution runs the graph in-process, pause and resume."""

import pytest
from httpx import AsyncClient

from src.execution import cloud_tasks
from tests.integration.sqla_phase4a.conftest import (
    HTTPX_MARK, Db, ExecApi, Member, approval_graph, install_email_recorder, jbody,
)
from tests.integration.sqla_phase4b.conftest import resume
from tests.integration.sqla_phase5.conftest import ENQUEUE_SITES, wait_for_status

pytestmark = [pytest.mark.integration, HTTPX_MARK]


async def test_in_process_fallback_pauses_and_resumes(
    app, client: AsyncClient, exec_api: ExecApi, db: Db, member: Member, monkeypatch: pytest.MonkeyPatch
) -> None:
    for site in ENQUEUE_SITES:  # undo the autouse synchronous driver: use the real function
        monkeypatch.setattr(site, cloud_tasks.enqueue_execution)
    install_email_recorder(monkeypatch)
    nodes, edges = approval_graph()
    wid = await db.seed_workflow(member, nodes=nodes, edges=edges)
    r = await exec_api.create(member, wid, "x")
    assert r.status_code == 202
    eid = jbody(r)["id"]
    sm = app.state.db_sessionmaker
    assert await wait_for_status(sm, eid, {"waiting_approval", "failed"}, 20) == "waiting_approval"
    assert (await resume(client, member, eid, "approved")).status_code == 200
    assert await wait_for_status(sm, eid, {"completed", "failed"}, 20) == "completed"
    row = await db.must(eid)
    assert row.variables["result"] == "approved"
```

- [ ] **Step 5: Write `test_events_live.py`** (live push arrives well inside the 15 s poll, so it proves LISTEN/NOTIFY works, not the fallback poll; reconnect replays). Use Starlette's `TestClient` and `client.portal.call(...)` exactly as `tests/integration/sqla_phase4a/test_events_ws.py` does (copy its `WsEnv` shape: seed users/workflow/execution through the portal, mint the token through the same helper it uses).

```python
"""Live WebSocket delivery rides LISTEN/NOTIFY (well under the 15 s poll); reconnect replays."""

import json
import threading
import time

import pytest

from tests.integration.sqla_phase4a.conftest import HTTPX_MARK

pytestmark = [pytest.mark.integration, HTTPX_MARK]


def test_live_event_arrives_within_seconds_of_its_notify(ws_env) -> None:  # fixture below
    eid, token = ws_env.running_execution()
    with ws_env.connect(eid, token) as sock:
        assert json.loads(sock.receive_text())["type"] == "workflow_started"  # snapshot
        seq = ws_env.append(eid, "node_started", {"node_id": "n1"})
        stop = threading.Event()

        def _keep_notifying() -> None:  # NOTIFY is a wake-up, safe to repeat; covers LISTEN setup
            while not stop.is_set():
                ws_env.notify(eid, seq)
                time.sleep(0.3)

        t = threading.Thread(target=_keep_notifying, daemon=True)
        started = time.monotonic()
        t.start()
        try:
            msg = json.loads(sock.receive_text())
        finally:
            stop.set()
            t.join()
        assert msg["type"] == "node_started" and msg["seq"] == seq
        assert time.monotonic() - started < 3.0


def test_reconnect_with_cursor_replays_only_missed_events(ws_env) -> None:
    eid, token = ws_env.running_execution()
    s1 = ws_env.append(eid, "node_started", {"node_id": "a"})
    s2 = ws_env.append(eid, "node_completed", {"node_id": "a"})
    with ws_env.connect(eid, token, after=s1) as sock:
        sock.receive_text()  # snapshot
        msg = json.loads(sock.receive_text())
    assert msg["seq"] == s2
```

Add the `ws_env` fixture to this module: a class with `running_execution()` (seeds a member, a workflow and a `running` execution through `client.portal.call`, returns `(execution_id, token)`), `append(eid, type, payload) -> int` (`app.state.event_bus.append(ExecutionEvent(...))` through the portal), `notify(eid, seq)` (`src.engine.events_notify.notify_execution_event` through the portal), `connect(eid, token, after=None)`, and cleanup with `purge_execution_artifacts` plus user/workflow deletes. Build it by copying `WsEnv` from `tests/integration/sqla_phase4a/test_events_ws.py` **except** its `close_notify_connection()` call (that function is removed in Task 8; the 4a copy is edited there).

- [ ] **Step 6: Run on today's code**

Run: `uv run pytest tests/integration/sqla_phase5 -v` (with `TEST_DATABASE_URL` set to the throwaway container).
Expected: all PASS. If one fails, the test is wrong about today's behaviour: fix the test, not `src`.

- [ ] **Step 7: Record (do not pin) what Task 7 changes on purpose.** In a scratch test (not committed): pause a run, delete its checkpoint rows directly (raw SQL on both old tables), resume it over HTTP, and note the final `status`, `error` and `failure_code`. This is also what the old revision does in a rollback when a run paused only in the new tables (it sees no checkpoint). Write the observation into this plan's Outcome section in Task 9. Task 7 pins the new behaviour (`checkpoint_missing`).

- [ ] **Step 7b: Verify** `uv run ruff check tests/integration/sqla_phase5 && uv run ruff format --check tests/integration/sqla_phase5 && uv run pyright --pythonplatform Linux --pythonversion 3.12 tests/integration/sqla_phase5`.

- [ ] **Step 8: Commit:** `test(db): phase 5 behaviour oracle for checkpoints and live events`.

**Review gate:** spec-compliance reviewer checks every behaviour in "Phase 5 extras" that can be observed today is pinned, and that no test reaches into `PrismaCheckpointSaver`, asyncpg or `app.state.db`.

---

### Task 2: Dependencies (gated on D1)

**Do not start until Balaji has approved D1.**

**Files:** Modify `pyproject.toml` (dependencies list only), `uv.lock`. Create `tests/unit/checkpoints/__init__.py`, `tests/unit/checkpoints/test_library_contract.py`.

- [ ] **Step 1: Write the failing contract test**

```python
"""Pins the parts of langgraph-checkpoint-postgres this codebase depends on.

A dependency bump that changes any of these must come with a code change (and, for
MIGRATIONS, an Alembic revision); this test is where that is noticed first.
"""

from langgraph.checkpoint.postgres import BasePostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def test_migration_count_matches_alembic_0004() -> None:
    assert len(BasePostgresSaver.MIGRATIONS) == 10


def test_async_saver_accepts_a_pool() -> None:
    assert "conn" in AsyncPostgresSaver.__init__.__code__.co_varnames
```

- [ ] **Step 2:** Run `uv run pytest tests/unit/checkpoints -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'langgraph.checkpoint.postgres'`.

- [ ] **Step 3:** Add to `[project] dependencies`, next to the `psycopg[binary]` line, with the spec link comment above it already present:

```toml
    # Phase 5 (checkpointer): official LangGraph saver on its own psycopg pool.
    "langgraph-checkpoint>=4.3.2,<5",
    "langgraph-checkpoint-postgres>=3.2.1,<4",
    "psycopg-pool>=3.3.3,<4",
```

Run: `uv lock --upgrade-package langgraph-checkpoint-postgres --upgrade-package psycopg-pool` then `uv sync --all-extras`.

- [ ] **Step 4: Check the lock diff moved nothing else:** `git diff uv.lock | grep '^[-+]name = ' ` lists only `langgraph-checkpoint-postgres` and `psycopg-pool` as added; `git diff uv.lock | grep -A1 '^-name = '` is empty (no package removed or re-versioned). If anything else moved, stop and report.

- [ ] **Step 5:** Run `uv run pytest tests/unit/checkpoints -v` → PASS; then the full unit suite `uv run pytest -m "not integration"` → PASS; pyright (both invocations) → 0 errors.

- [ ] **Step 6: Commit:** `build(deps): add langgraph-checkpoint-postgres and psycopg-pool for the official checkpointer`.

---

### Task 3: Saver tables through Alembic

**Files:** Create `src/db/migrations/versions/0004_langgraph_saver_tables.py`, `src/checkpoints/__init__.py` (docstring only), `src/checkpoints/schema.py`, `tests/integration/db/test_alembic_0004_saver_tables.py`, `tests/integration/db/test_saver_schema_matches_setup.py`. Modify `src/db/models/checkpoints.py`, `src/db/models/__init__.py`.

**Interfaces:**
- Produces: `src.checkpoints.schema.SAVER_MIGRATION_COUNT: int` (= `len(BasePostgresSaver.MIGRATIONS)`), `async def assert_saver_schema_current(conn: psycopg.AsyncConnection[Any]) -> None` (raises `RuntimeError` naming the found and expected versions), models `SaverCheckpoint` (`checkpoints`), `SaverCheckpointBlob` (`checkpoint_blobs`), `SaverCheckpointWrite` (`checkpoint_writes`), `SaverCheckpointMigration` (`checkpoint_migrations`).

- [ ] **Step 1: Write the failing tests.** `test_alembic_0004_saver_tables.py`:

```python
"""Revision 0004 creates the official saver's tables and marks its migrations applied."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration


async def test_tables_exist_and_all_saver_migrations_are_recorded(session: AsyncSession) -> None:
    # `session` is the tests/integration/db/conftest.py fixture.
    for t in ("checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert await session.scalar(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": t}), t
    vs = (await session.execute(text("SELECT v FROM checkpoint_migrations ORDER BY v"))).scalars().all()
    assert vs == list(range(10))


async def test_setup_is_a_no_op_after_alembic(test_database_url) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from src.db.url import to_libpq_url

    async with await AsyncConnection.connect(
        to_libpq_url(test_database_url), autocommit=True, row_factory=dict_row, prepare_threshold=None
    ) as conn:
        before = await (await conn.execute("SELECT count(*) AS n FROM checkpoint_migrations")).fetchone()
        await AsyncPostgresSaver(conn).setup()
        after = await (await conn.execute("SELECT count(*) AS n FROM checkpoint_migrations")).fetchone()
    assert before == after == {"n": 10}
```

(`test_database_url` is a small fixture in `tests/integration/db/conftest.py` returning `os.environ["TEST_DATABASE_URL"]`; add it there if it does not exist. `to_libpq_url` is created in this task, Step 3b.)

`test_saver_schema_matches_setup.py`: create schema `saver_ref_<random>` on a psycopg autocommit connection, connect a second connection with `options="-c search_path=<schema>"`, run `AsyncPostgresSaver(conn).setup()` there, then compare for the four tables between `public` and the scratch schema: `information_schema.columns` (`column_name, data_type, is_nullable, column_default`), primary-key columns (`information_schema.key_column_usage` joined to `table_constraints` where `constraint_type='PRIMARY KEY'`, ordered by `ordinal_position`), and `pg_indexes.indexdef` with the schema name replaced by `public`. Assert equal; drop the scratch schema in `finally`. Also assert `checkpoint_migrations` row sets are equal.

Unit test `tests/unit/checkpoints/test_schema.py`: `assert_saver_schema_current` with a fake connection whose `execute(...).fetchone()` returns `{"v": 9}` passes, `{"v": 8}` and `None` raise `RuntimeError` whose message contains `"9"` and the found value and no connection string.

- [ ] **Step 2:** Run them. Expected: FAIL (`to_regclass` false; `ImportError` for `to_libpq_url` / `src.checkpoints.schema`).

- [ ] **Step 3: Write the revision.**

```python
"""Tables for LangGraph's official Postgres checkpointer (langgraph-checkpoint-postgres 3.2.1).

The DDL is the library's own MIGRATIONS list (langgraph/checkpoint/postgres/base.py) applied in
order and collapsed to its final state: blob nullable, task_path present, three thread_id
indexes. The ten rows in checkpoint_migrations tell the library every one of its migrations has
run, so AsyncPostgresSaver.setup() is never needed (and is never called by the app or deploys:
schema changes go through Alembic only). tests/integration/db/test_saver_schema_matches_setup.py
proves this revision and setup() produce identical tables.

Plain CREATE TABLE, not IF NOT EXISTS: if a table of the same name already exists (someone ran
setup() by hand), the deploy fails instead of silently adopting a table of unknown shape. The
indexes are created without CONCURRENTLY: the tables are new and empty, and env.py runs every
pending revision in one transaction, where CONCURRENTLY is not allowed.

The old tables (langgraph_checkpoints, langgraph_checkpoint_writes) are untouched; phase 7 drops
them. Like 0002 and 0003 this schema is not in prisma/schema.prisma (frozen); phase 6's squashed
base must include these tables and the ten checkpoint_migrations rows.

Downgrade refuses while any checkpoint exists in the new tables: those are runs that paused
after the cutover, and dropping them would make them impossible to resume. Copy them to the old
tables first (`composer checkpoints copy --to legacy --all-waiting`) and delete the rows.

Revision ID: 0004_langgraph_saver_tables
Revises: 0003_assignment_user_fks
Create Date: 2026-11-25
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004_langgraph_saver_tables"
down_revision: str | None = "0003_assignment_user_fks"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

SAVER_MIGRATIONS_APPLIED = 10  # len(BasePostgresSaver.MIGRATIONS) in 3.2.1; pinned by a unit test


def _bound_locks() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")


def upgrade() -> None:
    _bound_locks()
    op.execute("CREATE TABLE checkpoint_migrations (v INTEGER PRIMARY KEY)")
    op.execute(
        """CREATE TABLE checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
)"""
    )
    op.execute(
        """CREATE TABLE checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
)"""
    )
    op.execute(
        """CREATE TABLE checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
)"""
    )
    op.execute("CREATE INDEX checkpoints_thread_id_idx ON checkpoints(thread_id)")
    op.execute("CREATE INDEX checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)")
    op.execute("CREATE INDEX checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)")
    op.execute(
        f"INSERT INTO checkpoint_migrations (v) "
        f"SELECT generate_series(0, {SAVER_MIGRATIONS_APPLIED - 1})"
    )


def downgrade() -> None:
    _bound_locks()
    remaining = op.get_bind().scalar(sa.text("SELECT count(*) FROM checkpoints"))
    if remaining:
        raise RuntimeError(
            f"{remaining} checkpoints exist in the new tables; copy paused runs to the old "
            "tables (composer checkpoints copy --to legacy --all-waiting) and delete them first"
        )
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints", "checkpoint_migrations"):
        op.execute(f"DROP TABLE {table}")
```

Note: the `checkpoint_writes` column order differs from `setup()` (there `task_path` is appended by `ALTER TABLE` and so is also last — same order). If the schema-equality test compares `ordinal_position`, it matches.

- [ ] **Step 3b: Add `to_libpq_url` to `src/db/url.py`** (the saver and LISTEN use libpq URLs; SQLAlchemy uses `postgresql+psycopg`):

```python
def to_libpq_url(database_url: str) -> str:
    """``database_url`` as a plain libpq URL (``postgresql://``) without Prisma-only parameters."""
    return to_sqlalchemy_url(database_url).replace("postgresql+psycopg://", "postgresql://", 1)
```

Unit test in `tests/unit/db/test_url.py`: Prisma params stripped, scheme `postgresql://`, `sslmode=require` kept.

- [ ] **Step 4: Models** in `src/db/models/checkpoints.py` (export from `src/db/models/__init__.py`). Match the DDL exactly; JSONB from `sqlalchemy.dialects.postgresql`; `metadata_` mapped to column `"metadata"` (as `LangGraphCheckpoint` does); `server_default=text("''::text")` for `checkpoint_ns`/`task_path`, `server_default=text("'{}'::jsonb")` for `metadata`; `idx` with `autoincrement=False`; indexes declared in `__table_args__` with the names above; `SaverCheckpointMigration.v` `Integer` primary key, `autoincrement=False`. Module docstring: "The official saver's tables (Alembic 0004). Only the library writes them; Composer reads them for the deploy guard and deletes from them in `checkpoints_repo.delete_threads`."

- [ ] **Step 5: `src/checkpoints/schema.py`**

```python
"""The official saver's schema version check (fail closed at startup)."""

from typing import Any

from langgraph.checkpoint.postgres import BasePostgresSaver
from psycopg import AsyncConnection

SAVER_MIGRATION_COUNT = len(BasePostgresSaver.MIGRATIONS)


async def assert_saver_schema_current(conn: AsyncConnection[Any]) -> None:
    """Raise unless checkpoint_migrations records exactly the library's migrations.

    A library upgrade that adds a migration needs a matching Alembic revision; until it has one
    the app refuses to start rather than letting the library write to a table it expects to
    look different.
    """
    cur = await conn.execute("SELECT max(v) AS v FROM checkpoint_migrations")
    row = await cur.fetchone()
    found = None if row is None else (row["v"] if isinstance(row, dict) else row[0])
    expected = SAVER_MIGRATION_COUNT - 1
    if found != expected:
        raise RuntimeError(
            f"checkpoint tables are at saver migration {found}, the installed "
            f"langgraph-checkpoint-postgres expects {expected}: add an Alembic revision"
        )
```

- [ ] **Step 6:** Run: the two new integration files, `tests/integration/db/test_schema_parity.py` (models match tables), `tests/unit/checkpoints`, and `uv run alembic heads` (one head) and `uv run alembic downgrade -1 && uv run alembic upgrade head` against the container. Expected: PASS.

- [ ] **Step 7: Commit:** `feat(db): Alembic revision for the official LangGraph checkpointer tables`.

---

### Task 4: `LegacyCheckpointSaver` on SQLAlchemy

The copy tool (Task 5), copy-on-resume (Task 7) and a rollback need to read and write the old tables after Prisma is gone (phase 6). This task ports `PrismaCheckpointSaver` to SQLAlchemy, behind `checkpoints_repo`, with identical bytes.

**Files:** Create `src/checkpoints/legacy.py`, `tests/integration/db/checkpoints/__init__.py`, `tests/integration/db/checkpoints/conftest.py` (fixture `sessionmaker_` returning `app.state.db_sessionmaker` from the root `app` fixture, and `saver` opening `build_saver_pool(get_settings())` once Task 5 adds it), `tests/integration/db/checkpoints/test_legacy_saver.py`, `tests/integration/db/checkpoints/test_legacy_matches_prisma.py`, `tests/fixtures/legacy_checkpoint_rows.json`. Modify `src/db/repos/checkpoints.py`, `tests/integration/db/repos/test_checkpoints.py`.

**Interfaces:**
- Produces in `src/db/repos/checkpoints.py` (records are frozen dataclasses):

```python
@dataclass(frozen=True)
class LegacyCheckpointRow:
    thread_id: str
    checkpoint_ns: str
    checkpoint_id: str
    parent_checkpoint_id: str | None
    checkpoint: bytes
    metadata: bytes

@dataclass(frozen=True)
class LegacyWriteRow:
    task_id: str
    idx: int
    channel: str
    value: bytes

async def legacy_get(session, thread_id: str, checkpoint_ns: str, checkpoint_id: str | None) -> LegacyCheckpointRow | None
    # checkpoint_id None -> newest by checkpoint_id DESC (D9)
async def legacy_list(session, thread_id: str, checkpoint_ns: str, *, before_id: str | None, limit: int | None) -> list[LegacyCheckpointRow]
    # ORDER BY checkpoint_id DESC; before_id -> checkpoint_id < before_id
async def legacy_writes(session, thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> list[LegacyWriteRow]
    # ORDER BY task_id, idx
async def legacy_upsert_checkpoint(session, row: LegacyCheckpointRow) -> None
    # INSERT ... ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE SET parent_checkpoint_id, checkpoint, metadata
async def legacy_upsert_writes(session, thread_id: str, checkpoint_ns: str, checkpoint_id: str, task_id: str, writes: list[tuple[int, str, bytes]]) -> None
    # (idx, channel, value); ON CONFLICT (... task_id, idx) DO UPDATE SET channel, value
async def legacy_namespaces(session, thread_id: str) -> list[str]
```

- Produces `src.checkpoints.legacy.LegacyCheckpointSaver(sessionmaker: async_sessionmaker[AsyncSession])`, a `BaseCheckpointSaver[int]` with `aget_tuple`, `alist`, `aput`, `aput_writes`, `adelete_thread`; sync methods raise `NotImplementedError("Use the async API")`; `serde=JsonPlusSerializer()`; the `_pack`/`_unpack` format `>I` tag length | tag | payload, moved here verbatim in behaviour (write it fresh; do not import from `src.storage`). Each method opens one short session (`async with self._sm() as s:`), runs repo calls, commits writes, and closes before returning.

- [ ] **Step 1: Write the failing tests.**
  - `test_legacy_saver.py` (real Postgres): a saver round trip using LangGraph's own helpers: `empty_checkpoint()`, `create_checkpoint(...)` from `langgraph.checkpoint.base`; `aput` three checkpoints chained by parent, `aput_writes` two tasks; `aget_tuple` latest returns the third with its writes ordered by `(task_id, idx)` and its `parent_config`; `aget_tuple` by id; `alist` newest first, `before=` and `limit=`; `adelete_thread` removes writes and checkpoints of that thread only. **Same-millisecond test (D9):** insert two checkpoints whose `created_at` is forced equal (`UPDATE langgraph_checkpoints SET created_at = ...`), the one with the larger `checkpoint_id` is returned as latest.
  - `test_legacy_matches_prisma.py` (deleted in Task 7, listed there): parametrized both directions: write with `PrismaCheckpointSaver(app.state.db)`, read with `LegacyCheckpointSaver(sm)` and vice versa; assert equal `CheckpointTuple`s and **byte-identical** `checkpoint`, `metadata` and `value` columns for the same inputs. Also a real graph: compile `approval_graph()` with `LegacyCheckpointSaver`, run to the pause, then resume with a graph compiled on `PrismaCheckpointSaver`; it completes.
  - Golden fixture: a one-off helper in `test_legacy_matches_prisma.py` (`write_golden_fixture()`, run manually once, then left uncalled) dumps the rows `PrismaCheckpointSaver` wrote for a paused `approval_graph()` thread as hex into `tests/fixtures/legacy_checkpoint_rows.json`. `test_legacy_saver.py::test_reads_rows_written_by_prisma` inserts those rows with `legacy_upsert_*` and asserts `aget_tuple` returns a checkpoint whose `pending_writes` contain an `__interrupt__` and whose `channel_values` include `variables`. This survives Task 7.
  - Repo tests in `tests/integration/db/repos/test_checkpoints.py` for each new function.

- [ ] **Step 2:** Run. Expected: FAIL (`ImportError`).
- [ ] **Step 3:** Implement the repo functions (SQLAlchemy Core `insert(...).on_conflict_do_update(...)` from `sqlalchemy.dialects.postgresql`; `select(...).order_by(LangGraphCheckpoint.checkpoint_id.desc())`), then `LegacyCheckpointSaver`. `aput` stores `parent_checkpoint_id = config["configurable"].get("checkpoint_id")` exactly as the Prisma saver did; `aput_writes` enumerates `idx` from 0 per call exactly as the Prisma saver did (it ignores `WRITES_IDX_MAP`; keep that, it is what the old revision reads).
- [ ] **Step 4:** Run the new tests and `tests/integration/sqla_phase5` (unchanged). Expected: PASS.
- [ ] **Step 5: Commit:** `feat(checkpoints): SQLAlchemy port of the old checkpoint saver for the copy tool and rollback`.

---

### Task 5: Version conversion, copy tool, CLI `copy`

**Files:** Create `src/checkpoints/versions.py`, `src/checkpoints/copy.py`, `src/checkpoints/saver.py` (pool builder and one-shot opener only; the lifespan comes in Task 7), `src/cli/checkpoints.py`, `tests/unit/checkpoints/test_versions.py`, `tests/integration/db/checkpoints/test_copy_round_trip.py`, `tests/unit/cli/test_checkpoints_cli.py`. Modify `src/cli/main.py`, `src/config.py`.

**Round-trip coverage (added after the Task 1 gate):** the copy-tool round-trip tests (both directions, then resume) also run the oracle's fan-out graph (`tests/integration/sqla_phase5/conftest.py:fan_out_graph`: `s -> {ua, side}`), whose interrupted checkpoint carries the completed sibling's pending writes. After copying and resuming, `side` must not run again and `variables["side"]` must be present.

**Interfaces:**
- `src/checkpoints/versions.py`:

```python
Target = Literal["postgres", "legacy"]

def to_postgres_version(v: str | int | float) -> str
    # str -> unchanged; int/float n -> f"{int(n):032}.{0:016}" (the official saver's shape,
    # zero suffix; its get_next_version parses the integer prefix)
def to_legacy_version(v: str | int | float) -> int
    # int -> unchanged; float -> int(v); str -> int(v.split(".", 1)[0])
def convert_checkpoint(cp: Checkpoint, to: Target) -> Checkpoint
    # copies cp; converts every value of cp["channel_versions"] and of every
    # cp["versions_seen"][node]; leaves ids, ts, channel_values, updated_channels alone
```

- `src/checkpoints/copy.py`:

```python
@dataclass(frozen=True)
class CopyReport:
    thread_id: str
    checkpoints: int
    writes: int

async def copy_thread(
    source: BaseCheckpointSaver[Any],
    target: BaseCheckpointSaver[Any],
    thread_id: str,
    *,
    to: Target,
    namespaces: list[str],
) -> CopyReport
```

Algorithm (idempotent; both savers upsert): for each namespace, list the source oldest → newest (`reversed([t async for t in source.alist({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}})])`); for each tuple: `cp = convert_checkpoint(t.checkpoint, to)`; `parent = t.parent_config["configurable"]["checkpoint_id"] if t.parent_config else None`; `cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns} | ({"checkpoint_id": parent} if parent else {})}` (always pass `checkpoint_ns`: the official saver `pop`s it and raises `KeyError` without it); `await target.aput(cfg, cp, t.metadata, dict(cp["channel_versions"]))` (all channel versions as "new", so every blob is written); then group `t.pending_writes` by `task_id` in their order and call `await target.aput_writes({"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp["id"]}}, [(channel, value), ...], task_id)` once per task. Never logs values.

- `src/checkpoints/saver.py` (this task's part):

```python
def saver_conninfo(settings: Settings) -> str
    # to_libpq_url(settings.database_url), or direct_endpoint_url(...) when
    # settings.checkpointer_use_direct_endpoint (direct_endpoint_url arrives in Task 8;
    # until then this branch raises NotImplementedError and is untested)
SAVER_CONNECTION_KWARGS: dict[str, Any] = {"autocommit": True, "row_factory": dict_row, "prepare_threshold": None}
def build_saver_pool(settings: Settings) -> AsyncConnectionPool[AsyncConnection[DictRow]]
    # open=False, min_size=settings.checkpointer_pool_min_size, max_size=settings.checkpointer_pool_max_size,
    # timeout=settings.checkpointer_pool_timeout_seconds, max_idle=300, kwargs=SAVER_CONNECTION_KWARGS,
    # check=AsyncConnectionPool.check_connection (Neon drops idle connections), name="checkpointer"
@asynccontextmanager
async def open_one_shot_saver(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]
    # one AsyncConnection (SAVER_CONNECTION_KWARGS), assert_saver_schema_current, yield AsyncPostgresSaver(conn)
```

- `src/config.py` adds `checkpointer_pool_min_size: int = Field(default=1, ge=0)`, `checkpointer_pool_max_size: int = Field(default=5, ge=1)`, `checkpointer_pool_timeout_seconds: float = Field(default=10.0, gt=0)`, `checkpointer_use_direct_endpoint: bool = False`, each with a `description=` that states the D4 budget.

- CLI (`src/cli/checkpoints.py`, wired in `src/cli/main.py` as `composer checkpoints copy`):

```
composer checkpoints copy --to {postgres,legacy} (--execution ID [--execution ID ...] | --all-waiting) [--force]
```
For each target execution (looked up with a one-shot SQLAlchemy session: `executions_repo.get`; `--all-waiting` uses a new `checkpoints_repo.waiting_threads(session) -> list[tuple[str, str]]` returning `(execution_id, thread_id)` of `waiting_approval` rows): refuse (exit 2, message names id and status) unless status is `waiting_approval` or `--force`; namespaces from `checkpoints_repo.legacy_namespaces` (to postgres) or `checkpoints_repo.saver_namespaces` (to legacy; add it: `SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id = :t`); source/target are `LegacyCheckpointSaver(one-shot sessionmaker)` and the `open_one_shot_saver` saver; print `copied <execution_id>: <n> checkpoints, <m> writes`. Exit 0 when all succeed, 1 if any copy raised (print the exception class name only).

- [ ] **Step 1: Failing unit tests** `test_versions.py`: `to_postgres_version(5) == "00000000000000000000000000000005.0000000000000000"`; strings pass through; `to_legacy_version("00000000000000000000000000000007.0123456789") == 7`; ints pass through; ordering preserved (`sorted` of converted equals converted of `sorted` for `[1, 2, 10, 11]`); `convert_checkpoint` converts `channel_versions` and every `versions_seen` entry and does not mutate its input; round trip `to_legacy(to_postgres(n)) == n`.

- [ ] **Step 2: Failing integration tests** `test_copy_round_trip.py` (the spec's "copy-tool round-trip tests both directions with resume"). Use a module fixture that opens `build_saver_pool(get_settings())` (opened in the test) → `AsyncPostgresSaver(pool)`, and `LegacyCheckpointSaver(sessionmaker_)`. Graph: `build_graph(Workflow.model_validate({...approval_graph()...}), saver)` from `src/engine/graph_builder.py`, invoked directly (no HTTP) with `initial_state("x")` from the same module the executor uses, and `{"configurable": {"thread_id": <unique>}}`.
  1. `test_legacy_to_postgres_then_resume`: run to the pause on the legacy saver; `copy_thread(legacy, postgres, tid, to="postgres", namespaces=[""])`; compile on postgres; `ainvoke(Command(resume="approved"), cfg)` completes with `variables["result"] == "approved"`; `aget_state(cfg).next == ()`.
  2. `test_postgres_to_legacy_then_resume` (rollback direction): pause on postgres, copy `to="legacy"`, resume on a graph compiled with **`PrismaCheckpointSaver(app.state.db)`** (the old revision's saver; this test switches to `LegacyCheckpointSaver` in Task 7, listed there) → completes.
  3. `test_chained_pause_round_trip`: `two_approval_graph()`: pause 1 on legacy → copy to postgres → resume → pause 2 on postgres → copy to legacy → resume on legacy → completed with both variables set.
  4. `test_copy_is_idempotent`: copying twice gives the same `alist` and `aget_tuple` on the target.
  5. `test_naive_copy_without_conversion_breaks_resume`: documents why conversion exists: with `convert_checkpoint` monkeypatched to identity, the postgres → legacy resume raises `NotImplementedError` (base `get_next_version` on a string). Keep this test; it fails loudly if a future LangGraph removes the need.
  6. `test_history_shape_survives_copy`: same number of checkpoints, same ids, same parent chain, same pending-write channels per task in both stores.

- [ ] **Step 3:** CLI unit tests with fakes (`monkeypatch` `copy_thread`, the one-shot openers and the repo lookups): refuses `running` without `--force` (exit 2, prints the id and `running`, nothing copied); `--force` copies; `--all-waiting` copies each waiting thread; a copy that raises → exit 1 and the output contains the exception class name but not its message.

- [ ] **Step 4:** Run all three files. Expected: FAIL (imports).
- [ ] **Step 5:** Implement `versions.py`, `copy.py`, `saver.py` (this task's part), settings, CLI.
- [ ] **Step 6:** Run them plus `tests/integration/sqla_phase5`. Expected: PASS.
- [ ] **Step 7: Commit:** `feat(checkpoints): two-way checkpoint copy tool with channel-version conversion`.

**Review gate (high risk):** a frontier-model reviewer checks the copy algorithm against `AsyncPostgresSaver.aput`/`aput_writes` and `LegacyCheckpointSaver` (parent chain, namespace key, blob versions, write grouping, `WRITES_IDX_MAP` idx handling on the official side, idempotency) before Task 6 starts.

---

### Task 6: Deploy guard

**Files:** Create `src/checkpoints/sync.py` (guard half), `tests/integration/db/checkpoints/test_guard.py`. Modify `src/db/repos/checkpoints.py`, `src/cli/checkpoints.py`, `src/cli/main.py`, `.github/workflows/deploy-gcp.yml` (one step added, nothing else), `tests/unit/cli/test_checkpoints_cli.py`.

**Interfaces:**
- `src/db/repos/checkpoints.py`:

```python
@dataclass(frozen=True)
class UnsyncedRun:
    execution_id: str
    status: str
    thread_id: str

async def unsynced_active_runs(session: AsyncSession) -> list[UnsyncedRun]
async def legacy_ahead(session: AsyncSession, thread_id: str) -> bool
async def active_status_counts(session: AsyncSession) -> dict[str, int]
```

`unsynced_active_runs` is one statement: executions with `status IN ACTIVE_STATUSES` (from `src/db/repos/executions.py`) whose `max(langgraph_checkpoints.checkpoint_id)` for `checkpoint_ns = ''` is not null and either the `max(checkpoints.checkpoint_id)` for the same thread and namespace is null or smaller. Ordered by `execution_id`. `legacy_ahead` is the same comparison for one thread, regardless of status.

- `src/checkpoints/sync.py`: `async def find_unsynced(sessionmaker) -> list[UnsyncedRun]` (short session).
- CLI: `composer checkpoints guard` prints `active runs: queued=<n> running=<n> waiting_approval=<n>` then either `ok: no active run has checkpoints only in the old tables` (exit 0) or one line per blocking run `blocked: <execution_id> (<status>)` followed by the runbook line `copy waiting runs with 'composer checkpoints copy --to postgres --all-waiting', wait for running ones, or cancel them; then re-run the deploy` (exit 1). Uses `one_shot_session` only (no saver needed).

- [ ] **Step 1: Failing tests** `test_guard.py` (real Postgres, rows seeded through the repos and the savers): (a) no active runs → `[]`; (b) a `waiting_approval` run paused on `LegacyCheckpointSaver` → listed (the spec's "deploy guard refuses while a paused run exists"); (c) after `copy_thread(... to="postgres")` → not listed; (d) the legacy saver then gets one more checkpoint for the thread (a run that progressed on an old instance) → listed again; (e) a `completed` run with only legacy rows → not listed; (f) a `queued` run with no checkpoints anywhere → not listed; (g) a `running` run with legacy rows → listed with status `running`. CLI unit tests: exit codes and output lines for both outcomes; output never contains a thread id or connection string.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Deploy workflow.** In `.github/workflows/deploy-gcp.yml`, in the "Apply database migrations" step, after `uv run alembic current`, add:

```yaml
          # SQLAlchemy phase 5 (until phase 7 drops the old checkpoint tables):
          # refuse the cutover while an active run has checkpoints only in the
          # old tables. Fails the job here, so the previous revision keeps
          # serving. Runbook: docs/operations/production-deployment.md
          # "Checkpointer cutover".
          uv run composer checkpoints guard
```

Validate the YAML locally (`uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/deploy-gcp.yml'))"`; if PyYAML is not installed, use `actionlint` if available, otherwise review the diff by eye: two-space indentation inside the `run: |` block).

- [ ] **Step 6: Commit:** `feat(checkpoints): deploy guard refusing the cutover while a run is left on the old tables`.

---

### Task 7: Cutover — official saver in the app, copy-on-resume, Prisma plumbing removed

**Files:** Create `src/checkpoints/runtime.py`, `src/api/app_state.py`, `tests/unit/checkpoints/test_saver_lifespan.py`, `tests/unit/engine/test_resume_checkpoint_sync.py`, `tests/integration/sqla_phase5/test_deliberate_changes.py`. Modify `src/checkpoints/saver.py` (lifespan), `src/checkpoints/sync.py` (`bring_forward`), `src/db/repos/checkpoints.py` (`delete_threads`), `src/main.py`, `src/execution/cloud_tasks.py`, `src/api/executions.py`, `src/api/run.py`, `src/api/approval_email.py`, `src/api/internal.py`, `src/engine/langgraph_executor.py`, `src/telemetry/attributes.py` (one allow-list value), `src/config.py` (wording), `docs/api-reference.md` (failure code row), unit tests that import `src.storage` or pass `db=` (list in Step 9). Delete `src/storage/` and `tests/unit/storage/`, and `tests/integration/db/checkpoints/test_legacy_matches_prisma.py`.

**Interfaces:**
- `src/checkpoints/runtime.py`: `register_checkpointer(cp: BaseCheckpointSaver[Any] | None) -> None`, `unregister_checkpointer(cp) -> None` (clears only if still registered), `get_registered_checkpointer() -> BaseCheckpointSaver[Any] | None` (mirrors `src/db/runtime.py`). The module docstring states why: lifespans can overlap (tests, a restart inside one process), and the older lifespan's exit must not clear the newer one's registration.
- `src/checkpoints/saver.py`: `@asynccontextmanager async def checkpointer_lifespan(app: FastAPI) -> AsyncIterator[AsyncPostgresSaver]`: build the pool; open it with the same fail-fast policy as `connect_with_retry` (`db_connect_max_attempts`, doubling backoff from `db_connect_initial_backoff_seconds`, each attempt `await pool.open(wait=True, timeout=settings.db_connect_timeout_seconds)`; on `psycopg_pool.PoolTimeout`/`psycopg.OperationalError` close the pool and retry); run `assert_saver_schema_current` on one pooled connection (fail closed: the lifespan raises); `saver = AsyncPostgresSaver(pool)`; set `app.state.checkpointer`; `register_checkpointer(saver)`; on exit unregister, delete the attribute, `await pool.close()` inside `try/except Exception: logger.exception(...)` so shutdown always completes.
- `src/checkpoints/sync.py`: `async def bring_forward(checkpointer: BaseCheckpointSaver[Any], sessionmaker: async_sessionmaker[AsyncSession], thread_id: str) -> bool`: returns `False` at once unless `isinstance(checkpointer, AsyncPostgresSaver)` (unit tests use in-memory savers); short session `legacy_ahead`; if ahead, `copy_thread(LegacyCheckpointSaver(sessionmaker), checkpointer, thread_id, to="postgres", namespaces=<legacy_namespaces>)`, log `checkpoints: brought thread forward from the old tables for execution <id>` (ids only), return `True`.
- `src/api/app_state.py`: `get_checkpointer(request: Request) -> BaseCheckpointSaver[Any]` and `get_event_bus(request: Request) -> PostgresEventStore`, each raising `RuntimeError("... not attached to app.state — did lifespan run?")` as today.
- `enqueue_execution(execution_id: str, *, kind: _TaskKind) -> None` (no `db`); `_run_in_process(execution_id, kind)` uses `get_registered_checkpointer()` and raises `RuntimeError("no checkpointer registered; is the app lifespan running?")` inside its `try` (logged, row stays `queued`, as for the sessionmaker today).
- `LangGraphExecutor` gains `class CheckpointMissing(Exception)` in `src/engine/langgraph_executor.py` and the failure code `"checkpoint_missing"`.

- [ ] **Step 1: Failing tests.**
  - `tests/integration/sqla_phase5/test_deliberate_changes.py` (new module in the oracle package, not part of the oracle; it must FAIL on the code before this task):
    - `test_resume_with_no_checkpoint_fails_closed`: pause a run, delete its checkpoint rows in every table (raw SQL), resume over HTTP → row `failed`, `failure_code == "checkpoint_missing"`, `"ok"` and `"no"` absent from `node_results`, `node_results` unchanged otherwise.
    - `test_run_paused_on_the_old_tables_resumes_after_cutover`: seed a paused run by running `approval_graph()` to the pause with `LegacyCheckpointSaver` directly (graph built with `build_graph`), with a `waiting_approval` execution row (`Db.seed_execution` + the pause variables from `tests/integration/sqla_phase4b/conftest.py:paused_variables`) whose `thread_id` matches; resume over HTTP → `completed`, `result == "approved"`; the new tables now hold the thread.
    - `test_new_runs_write_only_the_new_tables`: a fresh paused run has rows in `checkpoints` and none in `langgraph_checkpoints`/`langgraph_checkpoint_writes`, and the old tables still hold 0 rows for the thread after the resume too; also assert `isinstance(app.state.checkpointer, AsyncPostgresSaver)` and that `app.state.checkpointer.conn` is a `psycopg_pool.AsyncConnectionPool` (catches a `from_conn_string` single-connection port); repeat for a dev-mode run (real `enqueue_execution`).
    - `test_run_paused_on_the_old_tables_resumes_after_cutover` additionally asserts the OLD-table row counts for the thread are unchanged by the resume, and the new tables hold the full copied history (the same checkpoint ids as before plus the new ones).
    - `test_saver_pool_serves_concurrent_runs`: `checkpointer_pool_max_size=2`, a short `checkpointer_pool_timeout_seconds`, at least 4 DIFFERENT runs at once, each parked in a slow `http` node (an `httpx_mock` async callback sleeping about 1 s) -> all complete and none fails with `PoolTimeout` (proves no connection is held across a node).
    - `test_bring_forward_does_nothing_when_the_new_tables_are_ahead`: the new tables hold a newer head than the old ones -> `bring_forward` returns False and the resume continues from the new head (checked in `tests/unit/engine/test_resume_checkpoint_sync.py` with fakes and once end to end).
    - The oracle test `tests/integration/sqla_phase5/test_pause_resume.py::test_resume_without_a_checkpoint_restarts_the_graph_from_scratch_today` is the one oracle test this task replaces (by `test_resume_with_no_checkpoint_fails_closed`).
  - `tests/unit/engine/test_resume_checkpoint_sync.py`: with a fake checkpointer and `monkeypatch`ed `bring_forward`, `resume` calls `bring_forward` before `compiled.ainvoke`, and no session is open around it (`RecordingSessionmaker` + `assert_no_network_inside_session`); when `aget_tuple` returns `None`, `_mark_failed` is called with `failure_code="checkpoint_missing"` and `ainvoke` is never called.
  - `tests/unit/checkpoints/test_saver_lifespan.py` (also: on a normal exit the saver pool's `close` is awaited, and `unregister_checkpointer` of an already-replaced registration leaves the newer one in place): with a fake pool class monkeypatched into `build_saver_pool`: retries then succeeds; gives up after `db_connect_max_attempts` and raises; a schema-version mismatch raises and closes the pool; on exit the pool is closed even if `close` of something else raised; `app.state.checkpointer` and the registry are cleared.
  - `tests/integration/db/repos/test_checkpoints.py`: `delete_threads` clears all five tables for the given threads only, in the caller's transaction (roll back → rows still there); `test_delete_threads_matches_adelete_thread`: after it, `AsyncPostgresSaver.aget_tuple` is `None` and `alist` is empty; `test_delete_threads_covers_every_saver_table`: parse `BasePostgresSaver.MIGRATIONS` with `re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \(([^;]*?)\);", ...)`, keep the tables whose body contains `thread_id`, and assert that set equals the saver tables `delete_threads` deletes from (exposed as module constant `SAVER_THREAD_TABLES`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3: Lifespan.** `src/main.py`:

```python
    async with sqlalchemy_lifespan(app), checkpointer_lifespan(app):
        ...  # body unchanged
        try:
            yield
        finally:
            app.state.draining = True
            await stop_sweeper(sweeper_task)
            # Moved from prisma_lifespan: the cached Cloud Tasks client's grpc transport.
            try:
                await close_cloud_tasks_client()
            except Exception:
                logger.exception("lifespan: close_cloud_tasks_client() failed during shutdown")
```

(Task 8 adds `notifications_lifespan(app)` to the same `async with`.)

- [ ] **Step 4: Routes and fallback.** Remove every `db` parameter and `db=db` argument listed in the call-site table; import `get_checkpointer`/`get_event_bus` from `src.api.app_state`; delete `from prisma import Prisma` lines; update `_run_in_process` and `enqueue_execution` as in Interfaces and their docstrings (drop the `TODO(phase 5)` text). In `tests/integration/conftest.py`, `_fake_enqueue` becomes `async def _fake_enqueue(execution_id: str, *, kind: str) -> None`.
- [ ] **Step 5: Executor.** In `resume()`, inside the `try`, right after `ensure_execution_running(...)` and before `_emit("workflow_started", ...)`:

```python
            await bring_forward(self.checkpointer, self.sessionmaker, execution.thread_id)
            lg_probe = {"configurable": {"thread_id": execution.thread_id, "checkpoint_ns": ""}}
            if await self.checkpointer.aget_tuple(lg_probe) is None:  # pyright: ignore[reportArgumentType]
                raise CheckpointMissing(execution.id)
```

and a new `except CheckpointMissing as exc:` before `except Exception`, which logs `Execution %s has no checkpoint to resume` (id only) and calls `_mark_failed(execution_id, exc, failure_code="checkpoint_missing")` then emits `workflow_completed` with `{"status": "failed"}` if it won. Replace the docstring's `TODO(phase 5)` sentence with: "Loads the checkpoint from the official Postgres saver, first copying it from the old tables if a pre-cutover pause left it there (src/checkpoints/sync.py, until phase 7)." Add `"checkpoint_missing"` to the `failure_code` set in `src/telemetry/attributes.py` and a row to the failure-code table in `docs/api-reference.md`: "`checkpoint_missing` | A resume found no saved state for the run; it was not restarted."
- [ ] **Step 6: Deletes.** `checkpoints_repo.delete_threads` also deletes from `checkpoint_writes`, `checkpoint_blobs`, `checkpoints` (the `SAVER_THREAD_TABLES` constant, with the saver models), same `in_array` predicate, same transaction; update its docstring (D7). `executions_repo` is unchanged (it already calls `delete_threads`).
- [ ] **Step 7: Delete `src/storage/`** (`__init__.py`, `db.py`, `checkpointer.py`) and `tests/unit/storage/`, and delete `tests/integration/db/checkpoints/test_legacy_matches_prisma.py` (its golden fixture stays and is still read by `test_legacy_saver.py`); in `test_copy_round_trip.py::test_postgres_to_legacy_then_resume`, replace `PrismaCheckpointSaver(app.state.db)` with `LegacyCheckpointSaver(sessionmaker_)` (the byte-compatibility proven in Task 4 makes this the same test).
- [ ] **Step 8: 4a adapter.** In `tests/integration/sqla_phase4a/conftest.py`, `Phase4aAdapter.__init__` (adapter area): delete `self._db: Any = app.state.db` and any use of `self._db`.
- [ ] **Step 9: Unit tests.** Fix every test that imports `src.storage` or passes `db=` to `enqueue_execution`: `grep -rln "src.storage\b\|src\.storage\.\|enqueue_execution(.*db=" tests`. Known: `tests/unit/execution/test_cloud_tasks.py` (drop `db=MagicMock()`; the fallback test registers a fake checkpointer with `register_checkpointer` and asserts it reaches `claim_and_run_execution`; a new test: no checkpointer registered → logged, row untouched), `tests/unit/api/test_internal_poll_file_triggers.py`, `tests/unit/api/test_internal_fail_closed.py`, `tests/unit/api/test_executions*.py`, `tests/unit/api/test_approval_email.py`, `tests/unit/api/test_run_external.py` (dependency overrides of `get_db` removed; `app.state.db = MagicMock()` lines whose comment says "only handed to enqueue_execution (phase 5)" removed; other `app.state.db` assignments are harmless and go in phase 6). Integration tests that used `app.state.db` for Prisma (`grep -rln "app.state.db\b" tests/integration`) switch to the sessionmaker or are listed for phase 6 if they test the Prisma-era migration CLI.
- [ ] **Step 10: Verify:** `tests/integration/sqla_phase5` (oracle unchanged) + `test_deliberate_changes.py`; `tests/integration/sqla_phase4a`, `sqla_phase4b` (unchanged apart from Step 8); `tests/integration/db`; `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_durable_execution.py`; full unit suite; `tests/regression`; pyright both ways; ruff check and format check. Then the done-criterion greps from "Every remaining … use" except the asyncpg ones (Task 8).
- [ ] **Step 11: Commit:** `feat(checkpoints): official Postgres checkpointer in the app; Prisma client plumbing removed`.

**Review gate (high risk):** frontier-model reviewer: lifespan ordering and shutdown, fail-closed paths, no session held across `bring_forward`'s saver calls, every `db=` removed, Codex-shared files touched only where the coordination section says.

---

### Task 8: LISTEN/NOTIFY on psycopg

**Files:** Create `src/engine/notifications.py`, `tests/unit/engine/test_notifications.py`, `tests/integration/test_notifications_live.py`. Modify `src/engine/events_notify.py`, `src/db/repos/events.py` (`notify`), `src/db/url.py` (`direct_endpoint_url`), `src/config.py` (`database_direct_url`), `src/checkpoints/saver.py` (`saver_conninfo` direct branch), `src/api/events_ws.py`, `src/main.py`, `tests/unit/engine/test_events_notify.py`, `tests/unit/api/test_events_ws.py`, and the oracle lines in Step 5.

**Interfaces:**
- `src/db/repos/events.py`: `async def notify(session: AsyncSession, channel: str, payload: str) -> None` → `SELECT pg_notify(:channel, :payload)`.
- `src/engine/events_notify.py`: keeps `NOTIFY_CHANNEL` and `async def notify_execution_event(execution_id: str, *, seq: int) -> None` (signature unchanged): `get_registered_sessionmaker()` (`RuntimeError` when `None`), one short session, `events_repo.notify(...)`, commit. `close_notify_connection` and `_notify_connection` are deleted. Module docstring rewritten: NOTIFY goes through the SQLAlchemy pool (works on Neon's pooler; delivered at commit); the payload rules (pointer only, under 8000 bytes, not durable) are unchanged.
- `src/db/url.py`: `def direct_endpoint_url(libpq_url: str) -> str`: if the host's first DNS label ends with `-pooler`, drop that suffix (`ep-x-pooler.region.aws.neon.tech` → `ep-x.region.aws.neon.tech`); otherwise return unchanged.
- `src/config.py`: `database_direct_url: str | None = Field(default=None, description="Direct (non-pooler) Postgres URL for LISTEN; derived from DATABASE_URL when unset.")`.
- `src/engine/notifications.py`:

```python
class ExecutionEventListener:
    """One LISTEN connection per process, open only while someone watches.

    The connection is on the direct endpoint (LISTEN does not survive Neon's transaction-mode
    pooler). It opens on the first subscribe and closes `idle_close_seconds` after the last
    unsubscribe, so an idle instance never keeps Neon compute awake. A notification wakes the
    subscribers of that execution only. On every (re)connect all subscribers are woken, so they
    re-poll the durable event store and nothing published during a gap is missed (the WebSocket
    loop also polls every 15 s regardless).
    """

    def __init__(self, conninfo: str, *, idle_close_seconds: float = 30.0,
                 backoff_initial: float = 0.5, backoff_max: float = 30.0) -> None
    @contextlib.contextmanager
    def subscribe(self, execution_id: str) -> Iterator[asyncio.Event]
    async def aclose(self) -> None
```

Internals: `_subs: dict[str, set[asyncio.Event]]`; `subscribe` registers the event, starts the background `_run` task if not running, cancels a pending idle-close timer, and on exit removes the event and, if none remain, schedules the idle close. `_run`: loop { `conn = await psycopg.AsyncConnection.connect(conninfo, autocommit=True, prepare_threshold=None)`; `await conn.execute(f"LISTEN {NOTIFY_CHANNEL}")` (constant identifier, never user input); wake all; reset backoff; `async for n in conn.notifies(): self._dispatch(n.payload)` } on `Exception`: log `notifications: listener connection lost (%s); retrying in %.1fs` with the class name only, close the connection, sleep backoff (doubling to `backoff_max`); on `CancelledError` close and re-raise. `_dispatch(payload)`: `execution_id = payload.rpartition(":")[0]`; set every event in `_subs.get(execution_id, ())`. `aclose` cancels the task and the timer and awaits them.

- Lifespan: `notifications_lifespan(app)` in `src/engine/notifications.py` creates the listener with `settings.database_direct_url or direct_endpoint_url(to_libpq_url(settings.database_url))`, sets `app.state.event_listener`, and calls `aclose()` on exit (guarded, logged). It does not connect at startup.
- `src/api/events_ws.py` step 6 becomes:

```python
        listener: ExecutionEventListener | None = getattr(ws.app.state, "event_listener", None)
        with listener.subscribe(execution_id) if listener else contextlib.nullcontext(asyncio.Event()) as notified:
            try:
                while True:
                    ...  # loop body unchanged: wait_for(notified.wait(), 15.0), clear, list_since, keepalive
            except WebSocketDisconnect:
                return
            finally:
                with contextlib.suppress(Exception):
                    await ws.close(code=status.WS_1000_NORMAL_CLOSURE)
```

Without a listener (unit tests), the loop still delivers through the 15 s poll. Delete `import asyncpg`, the per-client connection and its `add_listener`/`remove_listener` cleanup; keep every comment that explains why the loop polls on timeout and why keepalive failure closes.

- `saver_conninfo`'s direct branch now returns `settings.database_direct_url` converted with `to_libpq_url`, else `direct_endpoint_url(to_libpq_url(settings.database_url))`.

- [ ] **Step 1: Failing unit tests** `test_notifications.py` with a fake `AsyncConnection` factory (monkeypatched `psycopg.AsyncConnection.connect`) whose `notifies()` yields queued `Notify`-like objects: dispatch wakes only the matching execution; no connection before the first subscribe; reconnect after the fake raises, with all subscribers woken; idle close after the last unsubscribe (use a 0.05 s idle timeout), a re-subscribe inside the window cancels the close; `aclose` stops everything; logs never contain the conninfo. `test_url.py`: `direct_endpoint_url` for a pooler host, a non-Neon host, and a host with `-pooler` elsewhere in the name (`my-pooler-db.example.com`'s first label is `my-pooler-db`: unchanged). `test_events_notify.py`: `notify_execution_event` opens one session, executes `pg_notify` with `("composer_execution_events", "<id>:<seq>")`, commits; raises `RuntimeError` with no sessionmaker. `test_events_ws.py`: replace the asyncpg mocks with a fake listener on `app.state.event_listener` whose event the test sets; keep every existing behavioural assertion (snapshot, replay, terminal close, keepalive failure closes).
- [ ] **Step 2: Failing live test** `tests/integration/test_notifications_live.py` (marker `integration`): a real listener on `TEST_DATABASE_URL` (no `-pooler`, so the derived URL is the same), `subscribe("e1")`, then `notify_execution_event("e1", seq=1)` through the app's sessionmaker → the event is set within 2 s; `notify("e2")` does not set it.
  Also in this module: with `idle_close_seconds=0.2`, the count of `LISTEN` backends (`SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND query ILIKE 'LISTEN%'`) is 0 before the first subscribe, 1 while subscribed, and 0 again shortly after the last unsubscribe. The listener connection sets `application_name` (e.g. `composer-listen`) so operators can find it.
- [ ] **Step 3:** Run → FAIL. **Step 4:** Implement; wire `notifications_lifespan` into `src/main.py`'s `async with` after `checkpointer_lifespan`.
- [ ] **Step 5: Oracle lines that reached into asyncpg internals** (the only oracle edits in this phase besides Task 7 Step 8): delete the autouse `_fresh_notify_connection` fixtures in `tests/integration/sqla_phase4a/conftest.py` and `tests/integration/sqla_phase4b/conftest.py` (they reset `events_notify._notify_connection`, which no longer exists), and the `close_notify_connection()` import and call in `WsEnv.cleanup` in `tests/integration/sqla_phase4a/test_events_ws.py`. No assertion changes.
- [ ] **Step 6:** Add to `tests/unit/db/test_prisma_import_boundary.py` a test `test_src_does_not_import_asyncpg` that walks all of `src` with the same AST helper and fails on `asyncpg` (D1: the package itself leaves in phase 6).
- [ ] **Step 7: Verify:** `tests/integration/sqla_phase5/test_events_live.py` (oracle; the 4 s bound and the no-notify-no-delivery test prove NOTIFY, not the poll), `tests/integration/sqla_phase4a/test_events_ws.py`, `test_executor.py::test_event_row_is_committed_before_its_notify_is_sent` (unchanged), `tests/integration/test_events_ws_hardening.py`, the new tests, full unit suite, pyright both ways, ruff. Then `grep -rnE "^\s*(from|import) asyncpg\b" src` prints nothing.
- [ ] **Step 8: Commit:** `feat(events): LISTEN/NOTIFY on psycopg with one lazy listener per process`.

---

**Review gate (Task 8):** the listener's `aclose()` must close its psycopg connection directly, not just cancel the idle timer and rely on `idle_close_seconds`: the oracle's `test_no_listen_connection_is_open_while_no_socket_is_watching` assumes an app's lifespan exit closes its LISTEN connection within a few seconds.

### Task 9: Lock, docs, live checks, PR

- [ ] **Step 1: Done-criterion greps** (from "Every remaining … use"); all print nothing, and `src/storage` does not exist.
- [ ] **Step 2: Boundary test.** In `tests/unit/db/test_prisma_import_boundary.py` replace `MIGRATED_PATHS` with `["src"]` (every file), update the module docstring ("After phase 5 nothing in `src` imports Prisma, `src.storage` or asyncpg; phase 6 removes the packages") and keep the detector tests.
- [ ] **Step 3: Docs.**
  - `CHANGELOG.md` `[Unreleased]` first entry: the official checkpointer (Alembic 0004; separate psycopg pool; old tables kept until phase 7); copy tool and deploy guard; copy-on-resume; `checkpoint_missing`; deletes cover both table sets in one transaction; LISTEN on one lazy direct connection per process, NOTIFY through the pool; deliberate changes (D9 newest-by-id, fail closed on a missing checkpoint, WebSocket wakes only for its own execution); the finding "LISTEN through the pooler" (with Task 9 Step 7's result).
  - `docs/architecture.md` data-access section: a paragraph "Checkpoints and notifications (phase 5)" (pool separation and how connections are held during a run, Alembic-owned saver tables and the startup version check, legacy saver/copy/guard until phase 7, listener design); edit the phase 4b paragraph's last sentence ("After phase 4 only the checkpointer … remain on Prisma") to "After phase 5 nothing in `src` uses Prisma; phase 6 removes the client, schema and Node.js".
  - `docs/operations/production-deployment.md`: new section "Checkpointer cutover (phase 5 → phase 7)": what the guard checks; the runbook (`composer checkpoints guard`, `copy --to postgres --all-waiting`, cancel); the rollback runbook (`copy --to legacy --all-waiting` before moving traffic back; roll-forward is automatic via copy-on-resume); the `checkpointer_use_direct_endpoint` escape hatch; `DATABASE_DIRECT_URL` optional; connection budget table from D4. `docs/operations/gcp-cloud-run-setup.md`: the guard step in the deploy description.
  - `docs/decisions.md`: a new ADR "Official LangGraph Postgres checkpointer and psycopg notifications" superseding ADR-0001 for the checkpointer (the phase 0 ADR recorded the intent; this one records D3, D4, D7, D9 as built). Use the next free number after ADR-0035.
  - `CLAUDE.md` (edit authorized by this task only) stack table: "Postgres driver: psycopg 3 (asyncpg leaves the image in phase 6)"; and a line under Development commands: "Never run `AsyncPostgresSaver.setup()`; the saver's tables are Alembic revision 0004."
  - Spec §3.3 phase 5 row → "(done, PR #<n>)"; spec §2.2 row for `prisma migrate deploy` → "`alembic upgrade head` (the saver's tables are an Alembic revision, not `setup()`; see the phase 5 plan D3)"; spec §2.3 note that `langgraph-checkpoint` was already 4.3.2.
  - The 4b plan's "After 4b" list: tick items with a link here. This plan: checkboxes and an Outcome section (what changed vs plan, oracle edits with reasons, Task 1 Step 7 observations, verification counts, PR number).
- [ ] **Step 4: Run everything CI runs, locally** against the throwaway Postgres 15: `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests && uv run pytest && uv run pytest -m "integration and not live"`; plus `uv run alembic heads` (one head) and a fresh-database build (`prisma migrate deploy` + `alembic upgrade head` on an empty database, then the parity test). Frontend and E2E smoke are unaffected but run once locally if the E2E stack is available (`docs/operations` describes it); otherwise note it for CI.
- [ ] **Step 5: Commit** `docs(db): record phase 5 outcome`; push the branch; open ONE PR; wait for the checks.
- [ ] **Step 6: Pre-merge production check (needs Balaji's go-ahead: reads production).** Run `composer checkpoints guard` against production read-only with the direct URL (the same secret and `sed` the deploy uses), from the PR branch. Expected: `active runs: queued=0 running=0 waiting_approval=<n>` and `ok`, or a list to copy. Record the output (counts only) in the PR.
- [ ] **Step 7: Live Neon checks (if a Neon test branch is available; Neon credentials were rejected for local work earlier, so this may move to the first deploy).** On a Neon branch with the pooler URL as `DATABASE_URL`: run the phase 5 oracle (`TEST_DATABASE_URL=<pooler URL>`). It proves (a) the saver's pipelined writes work through the pooler with `prepare_threshold=None` (else set `CHECKPOINTER_USE_DIRECT_ENDPOINT=true` and record it) and (b) `test_live_event_arrives_within_seconds_of_its_notify` passes with LISTEN on the derived direct URL. The oracle's live deadline is 4 s: on a cold Neon direct endpoint (compute waking) the first run may exceed it, so treat a first failure as a possible cold start and re-run before concluding anything. Also, for the record, run the same live test at `77e5e80` against the pooler URL to learn whether today's asyncpg LISTEN through the pooler works (the D4 finding).
- [ ] **Step 8: Merge only with Balaji's go-ahead** (merging deploys to production). After the deploy: the spec's manual check — one real run that pauses at an approval and is resumed (once in-app; once via the emailed link on a second run), both reaching `completed`; watch a run page and confirm events arrive live (not in 15 s steps); check logs for `checkpoints:` and `notifications:` lines; then 24 h of clean backend logs before phase 6 starts (spec §5.1). Delete the container (`docker stop composer-p5-pg && docker rm composer-p5-pg`).

---

## After phase 5: what phase 6 removes

- The `prisma` dependency, `prisma/` (schema and the 20 migrations), `uv run prisma generate` / `migrate deploy` / `migrate status` in `Dockerfile`, `.github/workflows/ci.yml` and `deploy-gcp.yml`, Node.js in the backend image.
- `asyncpg` and `asyncpg-stubs`; `tests/_rate_limit_reset.py` moves to psycopg or SQLAlchemy.
- Remaining Prisma-shaped test mocks (`app.state.db = MagicMock()` and similar); comments that explain Prisma history where they no longer help.
- The squashed Alembic base for fresh databases, which must include the saver tables and the ten `checkpoint_migrations` rows from `0004`, and must be generated from the live schema, never from `schema.prisma`.
- `MIGRATED_PATHS` and the `prisma`/`src.storage` detector become a plain "no such import anywhere" check.

**Phase 7 (about two weeks after phase 5 is stable)** additionally drops `langgraph_checkpoints`, `langgraph_checkpoint_writes` and `_prisma_migrations`, and deletes `src/checkpoints/legacy.py`, `copy.py`, `versions.py`, `sync.py` (guard and `bring_forward`), the `composer checkpoints` CLI, the guard step in `deploy-gcp.yml`, the legacy half of `checkpoints_repo`, the `LangGraphCheckpoint*` models, and the golden fixture.

## Follow-ups (not in this phase)

- Fold `pg_notify` into `events_repo.append`'s transaction (one round trip per event fewer; D8).
- Checkpoint retention: nothing prunes checkpoints of finished runs (old or new tables). Consider `adelete_thread` or `aprune` when a run reaches a terminal status, or a sweeper job with a retention window.
- Measure saver pool checkouts under load (pool stats via `pool.get_stats()` into telemetry) and revisit `checkpointer_pool_max_size`.
- If Task 9 Step 7 shows today's asyncpg LISTEN through the pooler never delivered, note in the incident log that production live updates were poll-driven until phase 5.

## Self-review notes (writing-plans checklist)

- **Spec coverage:** §3.4 "what users see" (oracle Tasks 1, unchanged API), deploy guard (Task 6), two-way copy tool via `BaseCheckpointSaver` (Task 5), deletes (Task 7 / D7, deliberately in-transaction instead of `adelete_thread`, equivalence tested), Neon pooler `prepare_threshold=None` (Task 5 `SAVER_CONNECTION_KWARGS`, Task 9 Step 7), rollback (D6, Task 5 tests, runbook in Task 9); §5 phase 5 extras: round trips both directions with resume (Task 5 Step 2), guard refuses with a paused run (Task 6 Step 1 b), post-deploy manual check (Task 9 Step 8); §2.2 LISTEN/NOTIFY on psycopg (Task 8); §2.3 deps (Task 2); §4.5 lifespan (Task 7 Step 3, Task 8); risks table rows (guard, copy tool, old tables kept, pool sizing D4, pooler test). Divergences from the spec are explicit decisions: D3 (Alembic, not `setup()`), D4 (separate pools), D7 (in-transaction delete), D1 (asyncpg leaves in phase 6, as the spec says; `langgraph-checkpoint` already at 4.3.2).
- **Names used across tasks:** `LegacyCheckpointSaver`, `copy_thread(..., to=, namespaces=)`, `Target`, `convert_checkpoint`, `build_saver_pool`, `open_one_shot_saver`, `saver_conninfo`, `checkpointer_lifespan`, `register_checkpointer`/`get_registered_checkpointer`, `bring_forward`, `find_unsynced`, `UnsyncedRun`, `unsynced_active_runs`, `legacy_ahead`, `SAVER_THREAD_TABLES`, `assert_saver_schema_current`, `SAVER_MIGRATION_COUNT`, `to_libpq_url`, `direct_endpoint_url`, `ExecutionEventListener`, `notifications_lifespan`, `CheckpointMissing`, `"checkpoint_missing"`.
