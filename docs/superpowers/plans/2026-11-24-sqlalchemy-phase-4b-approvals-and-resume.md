# SQLAlchemy Migration Phase 4b — Approvals and Resume — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the last execution-table Prisma queries (the in-app approval resume `POST /executions/{id}/resume`, the emailed-link `GET /approvals/email/{token}` and `POST /approvals/email/{token}/confirm`, and every write to `approvals`) to SQLAlchemy repository functions, so that after this phase no code in `src` queries `workflow_executions`, `approvals` or `execution_events` through Prisma.

**Architecture:** Same as phases 1–4a: repository modules in `src/db/repos/`, frozen-dataclass records, callers own transactions, the short-session rule. Each resume path becomes **one short transaction**: read, the guarded `waiting_approval → running` transition (with the `_resume_decision` stamp folded into the same `UPDATE`), the `approvals` insert, one commit; the session is closed before `enqueue_execution`. Prisma remains connected only for the checkpointer and the dev-mode enqueue fallback (phase 5).

**Tech Stack:** SQLAlchemy 2 async + psycopg 3, PostgreSQL 15, FastAPI, python-jose (HS256 approval tokens, unchanged), pytest + pytest-asyncio + pytest-httpx, pyright strict, ruff.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) (phase 4 row of §3.3, §4.1 guarded updates, "Known schema divergence"). Builds directly on the [phase 4a plan](2026-11-24-sqlalchemy-phase-4a-executions-and-events.md): read its "4a / 4b boundary", "Guarded updates", Decisions D1/D5/D9 and its "Phase 4b scope" section first. Structure follows [phase 3](2026-11-22-sqlalchemy-phase-3-workflows.md).

**Starting point:** a branch `feat/sqlalchemy-phase-4b` cut from `main` **after the phase 4a PR has merged and its 24 h of clean production logs have passed** (4a plan Task 7, spec §5.1). Every file:line below is quoted from `feat/sqlalchemy-phase-4a` at `3f6348b` (where these lines have not moved yet); 4a's Task 5 rewrites the rest of `src/api/executions.py`, so **re-locate each site by grep before editing** (`grep -n "db\.\(workflowexecution\|approval\)\." src/api/executions.py src/api/approval_email.py`).

## Global Constraints

All Global Constraints from phases 1–4a apply unchanged:

- Repos are the only query code; repos are imported as modules (`from src.db.repos import approvals as approvals_repo`, `from src.db.repos import executions as executions_repo`).
- Repos return frozen-dataclass records, never ORM objects; they never commit. Callers commit.
- Writes take timestamps from `utcnow()` (`src/db/types.py`), passed by the caller as `now`.
- Inserts that can collide use `add_or_already_exists` / `add_or_referenced_row_missing` (`src/db/errors.py`), never a bare `session.add` + flush.
- **Short-session rule.** NEVER hold a session across `enqueue_execution` (Cloud Tasks gRPC, or the dev-mode fallback task), email sending, any other network I/O, `asyncio.sleep`, or a graph run. Open `async with sessionmaker() as s:` for the DB step, commit, and let the block close **before** the slow step. Every function that mixes DB work and slow work gets a unit ordering test using `RecordingSessionmaker` + `assert_no_network_inside_session` (`tests/unit/db_fakes.py`).
- Routes get their sessionmaker from `require_sessionmaker(request)` (`src/db/runtime.py`): 503 `database unavailable` when none is attached (fail closed).
- **Never log secrets or execution payloads.** Approval email tokens are bearer credentials: never log them, never put them in exception messages, never add them to telemetry attributes. Exception class names and ids only.
- CI-parity pyright before every commit: `uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests`. Also `uv run ruff check src tests` and `uv run ruff format --check src tests`.
- No edits to `prisma/`, `pyproject.toml`, `uv.lock`, `.github/**`, `src/db/migrations/**` (no schema change: the `approvals` table, its `ApprovalDecision` enum type and the `Approval` model in `src/db/models/executions.py` already exist from phase 0).
- Commits authored by Balaji Rajan `<balajirajan@gmail.com>` (verify with `git log -1 --format='%an <%ae>'`), ending with one `Co-Authored-By:` line naming the model that actually did the work, e.g. `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Additionally, for this phase:

- **Guarded transitions are one statement.** G3 and G4 become ONE repo function, `executions_repo.resume_if_waiting`, issuing ONE `UPDATE workflow_executions SET … WHERE id = :id AND <guard> RETURNING id`. Never read-then-write, never `session.get()` + attribute assignment, never ORM unit-of-work flush.
- **One transaction per resume.** The guarded transition and the `approvals` insert run in the same session and are committed by one `commit()`; if either fails, both roll back. `enqueue_execution` runs only after that commit returned.
- **Do not edit the 4a oracle** (`tests/integration/sqla_phase4a/**`), including its ADAPTER AREA. 4b's oracle imports helpers from it; it never changes it.
- **`enqueue_execution(…, db=db)` keeps its `db` argument** (phase 5: it only feeds the dev-mode fallback's `PrismaCheckpointSaver`). So `resume_execution` and `confirm_approval_email` keep a `db = Depends(get_db)` parameter whose only use is that argument, annotated `Any` and marked `# TODO(phase 5): only for enqueue_execution's dev-mode checkpointer`.

## Decisions to confirm with Balaji (recommendations in bold)

| # | Topic | Today | Recommendation |
|---|---|---|---|
| DB1 | **Atomic resume.** | Three autocommitted Prisma statements, then enqueue: (1) G3/G4 `waiting_approval → running`, (2) `approval.create`, (3) an unguarded `variables` overwrite with `{**<variables read before step 1>, "_resume_decision": d}`. A crash or error between (1) and (3) leaves a `running`, lease-free row with no decision: no task was enqueued, so the orphan sweeper fails it after the grace period; if a stale resume-kind task is redelivered in that window, its claim takes a lease and answers 422 (4a D9). A crash between (1) and (2) loses the audit row. | **Fold the stamp into the guarded UPDATE** (`variables = variables \|\| jsonb_build_object('_resume_decision', :d)`) and run the approvals insert in the same transaction, one commit, then enqueue. Visible results on the success path are identical (pinned by the oracle); the crash windows disappear. One side effect worth naming: the merge is against the row's current `variables` instead of the pre-transition snapshot (same value today, since nothing writes `variables` of a `waiting_approval` row except these paths and the expiry sweep, which loses to the status guard). |
| DB2 | **Emailed-link confirm: pause-instance check is not in the guard.** | `_validate_token_and_execution` checks the token's `node_id` and `pending_since` against the row in a separate read; G4's guard is only `status = 'waiting_approval'`. If, between that read and G4, the pause is resolved in-app, the run continues and pauses again (a `while`-loop approval node re-pausing), a stale link resolves the **new** pause instance. Narrow window, but it is exactly the case `pending_since` was added to prevent. | **Add the instance check to G4's guard**: `AND variables->>'_pending_approval_node' = :node AND variables->>'_pending_approval_since' = :since` (both values are strings from the signed token). In-app resume (G3) keeps today's status-only guard: its request carries no pause-instance identifier, so a guard there would only narrow a window the UI's own staleness already dwarfs (follow-up: send `pendingSince` from the run page). If Balaji declines: drop `expected_pending` from the G4 call; everything else in this plan stands. |
| DB3 | **Approval token exposure in URLs.** | Tokens are stateless HS256 JWTs (`src/security/jwt.py`, `type="approval_email"`, `exp = iat + approval_link_ttl_hours`), never stored, so there is nothing to hash. Signature check is python-jose's HMAC verify (`hmac.compare_digest`); no secret is compared in Composer code. Single use is enforced by state (the status guard + pause-instance binding), not by a token record. The token travels in the backend URL **path** (`/approvals/email/{token}`, `/confirm`) and, after the GET's 303, in the frontend **query string** (`/approval-confirm?token=…&decision=…`). Composer's JSON access log drops the path (`src/telemetry/logging.py`) and `redact()` scrubs any URL containing `approval`; the frontend sends `Referrer-Policy: strict-origin-when-cross-origin`. Remaining exposure: Cloud Run / load-balancer request logs (full path) and the reviewer's browser history. | **No change in 4b** (pin: no Composer log record contains the token). Follow-up, needs a frontend change: put the token in the URL fragment (`/approval-confirm#token=…`) or exchange it for a short-lived opaque confirm id on GET; consider a dedicated signing key for approval links so rotating it does not log everyone out. |
| DB4 | **Enqueue failure after a resume.** | `enqueue_execution` raising propagates (500); the row is already `running` with the decision and the approval row; nothing re-enqueues it, so the orphan sweeper fails it (`orphaned_running`) after `orphaned_running_grace_seconds`. | **Keep, pinned** (oracle `test_enqueue_failure_leaves_running_row_for_the_orphan_sweeper`). Follow-up: on enqueue failure, re-enqueue with backoff or fail the row at once with a clear message (as `POST /executions` does with G1). |
| DB5 | **Resume response body.** | 200 with the row read **before** the transition, `status` replaced by `"running"`: `variables` lacks `_resume_decision`, `error`/`completedAt` as before. | **Keep, pinned** (`_to_execution_read(dataclasses.replace(record, status="running"))`), like 4a D9's cancel response. |
| DB6 | **Malformed `decision` claim in a validly signed token.** | `ApprovalEmailTokenPayload.decision` is an unvalidated `str`. A token with another value (only mintable with the JWT secret) would pass G4, then fail the `approval.create` enum cast → 500 with the row stuck `running`. | **Map it to `invalid` before any write** (`ApprovalDecision(claims.decision)` raising `ValueError` → `_redirect_result("invalid")`). Unreachable without the secret; listed for completeness. Covered by a Task 3 unit test, not the oracle. |

Kept as-is and pinned (no decision needed): GET never mutates; both routes rate-limit before any DB access (`resume` per user at `rate_limit_resume_per_minute`, `approval_email` per client IP at `rate_limit_approval_email_per_minute`, one bucket shared by GET and POST); the resume routes emit no execution event (the next event is the resume claim's `workflow_started`); `approvals.approver_email` is the token's claim, `note` is NULL for email decisions, `approver_user_id` is NULL for email decisions; new `approvals.id` values are UUIDv7 instead of Prisma's cuid (as for every table in phases 1–4a; the oracle asserts only "non-empty string").

---

## 4b call sites: every remaining execution/approval Prisma query after 4a

After 4a, `grep -rnE "\b(db|self\.db)\.(workflowexecution|executionevent|ratelimitbucket|approval)\." src` lists exactly these eight lines (4a plan, "Counts"). Each is assigned:

| # | File:line (`3f6348b`) | Prisma call | Becomes | Task |
|---|---|---|---|---|
| 1 | `src/api/executions.py:577` | `workflowexecution.find_unique` (resume read + authz) | `executions_repo.get` | 3 |
| 2 | `src/api/executions.py:624` | `workflowexecution.update_many` waiting_approval→running (**G3**) | `executions_repo.resume_if_waiting(…, expected_pending=None)` | 3 |
| 3 | `src/api/executions.py:649` | `approval.create` (in-app) | `approvals_repo.create(…, via_email_link=False)` | 3 |
| 4 | `src/api/executions.py:659` | `workflowexecution.update` unguarded `variables` stamp | folded into #2 (DB1) | 3 |
| 5 | `src/api/approval_email.py:78` | `workflowexecution.find_unique` (shared GET/POST validation) | `executions_repo.get` | 3 |
| 6 | `src/api/approval_email.py:177` | `workflowexecution.update_many` waiting_approval→running (**G4**) | `executions_repo.resume_if_waiting(…, expected_pending=PendingInstance(…))` (DB2) | 3 |
| 7 | `src/api/approval_email.py:197` | `approval.create` (email) | `approvals_repo.create(…, via_email_link=True)` | 3 |
| 8 | `src/api/approval_email.py:207` | `workflowexecution.update` unguarded `variables` stamp | folded into #6 (DB1) | 3 |

Prisma handles that remain on these routes (not queries): `resume_execution(db=Depends(get_db))` and `confirm_approval_email(db=Depends(get_db))` keep `db` only for `enqueue_execution(…, db=db)` (phase 5); `resolve_approval_email` (GET) drops its `db` parameter entirely; `from prisma import Json, Prisma` leaves `src/api/approval_email.py` (annotate `db: Any`, as `src/api/internal.py` already does). `ExecutionRead` loses `from_attributes=True` (Task 3) once no Prisma row is validated into it.

Not 4b (checked): `src/maintenance/execution_sweeper.py` `sweep_expired_approvals` (G11) moved in 4a; `LangGraphExecutor._mark_waiting_approval` (G8) moved in 4a (D1); `src/executors/user_approval.py` and `src/executors/arcade.py` touch no table (they only `interrupt()`); `src/engine/approval_email.py` only mints tokens and calls Resend (it is called by the executor outside any session, 4a Task 4 ordering test); `src/security/jwt.py` touches no table; `approvals` deletes happen only through the `ON DELETE CASCADE` FK from `workflow_executions` (4a `delete_terminal`, user/workflow deletes).

## Guarded transitions in 4b

| # | Site | Guard (exact) | SET (exact) | Repo function | Races the oracle / repo tests pin |
|---|---|---|---|---|---|
| G3 | in-app resume | `id = :id AND status = 'waiting_approval'` | `status='running'`, `running_since=:now`, `lease_owner=NULL`, `lease_expires_at=NULL`, `variables = variables \|\| jsonb_build_object('_resume_decision', :decision)` (DB1); nothing else (`completed_at`, `error`, `failure_code`, `delivery_attempts`, `current_node_id` untouched) | `resume_if_waiting(session, id, decision=…, now=…, expected_pending=None) -> bool` | 5 concurrent resumes → one 200, four 409, one approval row, one enqueue; resume vs cancel; resume vs approval-expiry sweep; resume vs email confirm |
| G4 | email confirm | G3's guard `AND variables->>'_pending_approval_node' = :node AND variables->>'_pending_approval_since' = :since` (DB2) | same as G3 | `resume_if_waiting(…, expected_pending=PendingInstance(node_id, since))` | 5 concurrent confirms of one token → one `approved` redirect, four `invalid`, one approval row, one enqueue; approve-token vs reject-token; confirm vs in-app resume; confirm vs cancel; confirm vs expiry sweep; stale-instance confirm after a re-pause (Task 3 regression test) |
| — | approval insert | none (plain insert, same transaction as G3/G4, only after it returned `True`) | all columns explicit | `approvals_repo.create` | exactly one row per winning resume; zero rows for every loser and every rolled-back attempt |

"Single-use token consumption" is G4 itself: there is no token table; once G4 has moved the row out of `waiting_approval` (or a newer pause has a new `pending_since`), every copy of that token, and its opposite-decision sibling, resolves to `invalid`.

Interplay with 4a's transitions (unchanged in 4b, listed so the race tests are read correctly): `cancel_if_active` (G2) accepts `waiting_approval` **and** `running`, so a cancel that lands after a winning resume also succeeds (the row ends `canceled`, the later claim says `already_claimed`); `fail_expired_approval_if_unchanged` (G11) needs `status='waiting_approval'` and a byte-equal `variables` snapshot, so it and G3/G4 are mutually exclusive; the resume-kind `claim` (G5) needs `status='running'` with no lease, which is exactly what G3/G4 commit.

---

## File structure

```
src/db/repos/
  approvals.py      NEW  ApprovalRecord; create
  executions.py     MOD  + PendingInstance; resume_if_waiting           (4a's module, 4a's names kept)
src/api/executions.py      MOD  resume_execution on repos; ExecutionRead drops from_attributes
src/api/approval_email.py  MOD  GET + POST on repos; Prisma import removed
tests/integration/sqla_phase4b/   NEW  behaviour oracle (written first, run on the post-4a code)
  __init__.py, conftest.py, test_resume_route.py, test_approval_email.py, test_resume_races.py
tests/integration/db/repos/
  test_approvals.py               NEW
  test_executions_resume.py       NEW  resume_if_waiting unit + race tests
tests/integration/sqla_phase4b/test_deliberate_changes.py   NEW (Task 3)  DB1/DB2 regression tests
tests/unit/api/test_executions_resume.py     MOD  Prisma fakes → patched repos
tests/unit/api/test_approval_email.py        MOD  Prisma fakes → patched repos
```

---

### Task 1: Behaviour oracle on the post-4a code

**Files:** Create `tests/integration/sqla_phase4b/__init__.py` (empty), `conftest.py`, `test_resume_route.py`, `test_approval_email.py`, `test_resume_races.py`.

**Interfaces:**
- Consumes (from `tests/integration/sqla_phase4a/conftest.py`, unchanged): fixtures `app`, `phase4a_env`, `sessionmaker_`, `make_user`, `member`, `second_member`, `admin`, `db`, `exec_api`, `adapter`; helpers `Db`, `Member`, `ExecApi`, `EnqueueRecorder`, `install_fake_executor(monkeypatch, sm) -> FakeExecutorLog`, `EXECUTION_KEYS`, `HTTPX_MARK`, `ago`, `jbody`, `not_found`. Tests import them from `tests.integration.sqla_phase4a.conftest` and the 4b helpers from `tests.integration.sqla_phase4b.conftest`. `adapter.sweep_approvals(timeout_hours=…, now=…)` is the 4a-ported sweeper and does not change in 4b.
- Produces: nothing for `src`. The oracle's only seams are HTTP routes, `src.api.executions.enqueue_execution` / `src.api.approval_email.enqueue_execution` (monkeypatched) and `Phase4aAdapter.sweep_approvals`; **no adapter of its own**, because everything 4b changes is reached over HTTP.

`conftest.py`:

```python
"""Fixtures for the SQLAlchemy phase 4b behaviour oracle (in-app resume, emailed-link approval).

Written against the post-4a code, where these routes still use Prisma. These tests must keep
passing UNCHANGED after the routes move to SQLAlchemy. There is no adapter: every behaviour 4b
changes is reached over HTTP. The approval-expiry sweep is called through 4a's Phase4aAdapter
(already on SQLAlchemy; 4b does not touch it).

Seams: ``src.api.executions.enqueue_execution`` and ``src.api.approval_email.enqueue_execution``
are monkeypatched with ``install_resume_enqueue`` (records calls, optionally raises); otherwise the
autouse driver from tests/integration/conftest.py runs claim-and-run inline over ASGI.
"""

import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import select

from src.config import get_settings
from src.db.models import Approval
from src.security.jwt import create_approval_email_token
from tests._rate_limit_reset import reset_rate_limit_buckets
from tests.integration.sqla_phase4a.conftest import (
    Db,
    EnqueueRecorder,
    Member,
    adapter,
    admin,
    app,
    db,
    exec_api,
    make_user,
    member,
    phase4a_env,
    second_member,
    sessionmaker_,
)

# Fixtures re-exported from the 4a oracle (listing them here marks the imports as used).
__all__ = [
    "adapter", "admin", "app", "db", "exec_api", "make_user", "member",
    "phase4a_env", "second_member", "sessionmaker_",
]

APPROVER = "approver@example.com"


@pytest.fixture(autouse=True)
def _network_lock(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnusedFunction]
    del httpx_mock


@pytest_asyncio.fixture(autouse=True)
async def _fresh_notify_connection() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    import src.engine.events_notify as events_notify

    events_notify._notify_connection = None  # pyright: ignore[reportPrivateUsage]
    yield
    conn: Any = events_notify._notify_connection  # pyright: ignore[reportPrivateUsage]
    events_notify._notify_connection = None  # pyright: ignore[reportPrivateUsage]
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close()


@pytest_asyncio.fixture(autouse=True)
async def _clean_buckets_after() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    yield
    await reset_rate_limit_buckets()


def iso_now() -> str:
    return datetime.now(UTC).isoformat()


def paused_variables(since: str, node_id: str = "ua", **extra: Any) -> dict[str, Any]:
    """The markers LangGraphExecutor._mark_waiting_approval writes, plus pre-pause state."""
    return {
        "input": "payload",
        "k": [1, {"n": 2}],
        "_pending_approval_node": node_id,
        "_pending_approval_prompt": "Please approve",
        "_pending_approval_since": since,
        **extra,
    }


async def seed_paused(
    db: Db, owner: Member, *, since: str | None = None, node_id: str = "ua", **columns: Any
) -> tuple[str, str]:
    """A waiting_approval row on its own workflow. Returns (execution_id, pending_since)."""
    since = since or iso_now()
    wid = await db.seed_workflow(owner)
    columns.setdefault("variables", paused_variables(since, node_id))
    columns.setdefault("delivery_attempts", 1)
    eid = await db.seed_execution(wid, owner, status="waiting_approval", **columns)
    return eid, since


def email_token(
    execution_id: str, decision: str, since: str, *, node_id: str = "ua", approver: str = APPROVER
) -> str:
    return create_approval_email_token(execution_id, node_id, decision, approver, since)


def result_location(status_param: str) -> str:
    return f"{get_settings().frontend_url}/approval-result?{urlencode({'status': status_param})}"


def confirm_location(token: str, decision: str) -> str:
    query = urlencode({"token": token, "decision": decision})
    return f"{get_settings().frontend_url}/approval-confirm?{query}"


def not_waiting(status: str) -> str:
    return f"Execution is {status!r}, not 'waiting_approval'."


def lost_race(execution_id: str) -> str:
    return (
        f"Execution {execution_id!r} is no longer waiting_approval "
        "(already resolved by a concurrent request)."
    )


async def resume(
    client: AsyncClient, who: Member, execution_id: str, decision: str, note: str | None = None
) -> Response:
    body: dict[str, Any] = {"decision": decision}
    if note is not None:
        body["note"] = note
    return await client.post(f"/executions/{execution_id}/resume", json=body, headers=who.headers)


async def approvals_of(db: Db, execution_id: str) -> list[dict[str, Any]]:
    """Every column of every approvals row of this execution, oldest first."""
    async with db.sm() as s:
        rows = (
            await s.scalars(
                select(Approval)
                .where(Approval.execution_id == execution_id)
                .order_by(Approval.created_at)
            )
        ).all()
    return [Db.snapshot(r) for r in rows]


def install_resume_enqueue(
    monkeypatch: pytest.MonkeyPatch, rec: EnqueueRecorder
) -> EnqueueRecorder:
    monkeypatch.setattr("src.api.executions.enqueue_execution", rec)
    monkeypatch.setattr("src.api.approval_email.enqueue_execution", rec)
    return rec
```

Lessons from phases 1–4a apply: snapshot a bystander row (all columns) in every write test; pin exact statuses, details, redirect URLs and key sets; never depend on pre-existing rows; every concurrent "exactly one" claim uses `asyncio.gather` over ≥5 attempts and is repeated 3 times in a loop inside the test (fresh row each time); assert on the specific rows, never on sweep counts (other tests' rows may be scanned).

Groups (module → what it pins; every test `pytestmark = [pytest.mark.integration, HTTPX_MARK]`):

**`test_resume_route.py`** (in-app `POST /executions/{id}/resume`, `install_resume_enqueue` unless stated):

1. `test_resume_approved_and_rejected_success` (parametrized `approved`/`rejected`, note `"looks fine"` and `None`): seed a paused row with a stale lease (`lease_owner="old-worker"`, `lease_expires_at=ago(minutes=1)`), a bystander paused row; record `t0 = datetime.now(UTC)`. Response 200, `set(body) == EXECUTION_KEYS`, `body["status"] == "running"`, `body["variables"] == paused_variables(since)` (no `_resume_decision`, DB5), `body["completedAt"] is None`, every other key equals `GET /executions/{id}` taken before the resume except `status`. `rec.calls == [(eid, "resume")]`. Row: `status="running"`, `t0 <= running_since <= now`, `lease_owner is None`, `lease_expires_at is None`, `completed_at is None`, `error is None`, `failure_code is None`, `delivery_attempts == 1`, `variables == {**paused_variables(since), "_resume_decision": decision}`; every other column equals the before-snapshot. `approvals_of(eid)` is one row with exactly: `execution_id=eid`, `node_id="ua"`, `approver_user_id=member.id`, `approver_email=None`, `via_email_link=False`, `decision=decision`, `note=<note>`, `t0 <= created_at <= now`, `id` a non-empty `str`. Event count for `eid` unchanged (the route emits none). Bystander snapshot unchanged.
2. `test_resume_404s`: missing id → 404 `not_found(id)`; another member's paused row → 404 `not_found(id)` and row/approvals unchanged; admin resumes another member's row → 200 and the approval's `approver_user_id == admin.id`.
3. `test_resume_409_for_every_other_status` (parametrized `queued`, `running`, `completed`, `failed`, `canceled`; row seeded with `paused_variables`): 409 `not_waiting(status)`, row byte-identical, no approval row, no enqueue.
4. `test_resume_500_without_pending_node`: `waiting_approval` row with `variables={"k": 1}` → 500 `{"detail": "Execution is waiting_approval but has no _pending_approval_node."}`, row byte-identical, no approval, no enqueue.
5. `test_resume_422_bad_body`: `{"decision": "maybe"}` and `{}` → 422, row unchanged, no approval, no enqueue.
6. `test_resume_rate_limited`: `monkeypatch.setenv("RATE_LIMIT_RESUME_PER_MINUTE", "1")`, `get_settings.cache_clear()`; first resume on paused row A → 200; second on paused row B → 429 `{"detail": "Rate limit exceeded. Retry after 60 seconds."}` with `Retry-After: 60` (pin the exact numbers observed; the bucket refills 1/min), row B unchanged, no approval for B, enqueue called once.
7. `test_enqueue_failure_leaves_running_row_for_the_orphan_sweeper` (DB4): `EnqueueRecorder(raises=RuntimeError("enqueue boom"))`; `with pytest.raises(RuntimeError, match="enqueue boom")` around the request (ASGITransport re-raises app exceptions). Row: `running`, `_resume_decision` stamped, lease NULL, one approval row. Then the 4a-ported orphan sweep, `adapter.sweep_orphaned(grace_seconds=1, now=datetime.now(UTC) + timedelta(seconds=5))`, fails it: row `failed`, `failure_code="orphaned_running"`.
8. `test_resumed_row_is_claimable_by_a_resume_task` (parametrized decision): `install_fake_executor(monkeypatch, sessionmaker_)` and `install_resume_enqueue`; resume a `seed_paused` row → 200; then `exec_api.claim(eid, "resume")` → `{"status": "completed"}`; the fake's `resumes == [(eid, decision)]` and its `seen[0]` has `status="running"` and `lease_owner` set; a second `exec_api.claim(eid, "resume")` → `{"status": "already_claimed"}`. (The real-graph end-to-end hand-off is 4a oracle group 12, `test_resume_crossover.py`, which keeps running in 4b.)

**`test_approval_email.py`** (`GET /approvals/email/{token}`, `POST /approvals/email/{token}/confirm`; `client` fixture, no auth headers):

9. `test_get_valid_token_redirects_to_confirm_and_never_mutates`: GET three times → each 303 with `location == confirm_location(token, "approved")`; row snapshot and `approvals_of` unchanged; no enqueue.
10. `test_get_invalid_cases` (parametrized, each → 303 `result_location("invalid")`, nothing changes): garbage string; an access token (`member.token`, wrong `type`); a token signed with another secret (`jose.jwt.encode` of a valid payload with key `"other"`); an expired token (mint with `monkeypatch.setattr("src.security.jwt._now", lambda: int(time.time()) - ttl_hours*3600 - 10)` then restore); a token for a missing execution id; node mismatch (`node_id="other"`); stale `pending_since` (token minted with an older `since`); row in each non-waiting status (`queued`, `running`, `completed`, `failed`, `canceled`).
11. `test_confirm_success` (parametrized decision): row with stale lease; POST → 303 `result_location(decision)`; `rec.calls == [(eid, "resume")]`; row exactly as in test 1 (running, `running_since` set, lease cleared, `variables == {**paused, "_resume_decision": decision}`, other columns unchanged); one approval row with exactly `approver_user_id=None`, `approver_email=APPROVER`, `via_email_link=True`, `note=None`, `node_id="ua"`, `decision=decision`.
12. `test_confirm_invalid_cases`: the same parametrized cases as test 10 → 303 invalid, row byte-identical, no approval, no enqueue.
13. `test_confirm_is_single_use`: confirm approve → success; same token again → invalid; the **reject** token for the same pause → invalid; GET of either → invalid; still one approval row, one enqueue.
14. `test_rate_limit_shared_by_get_and_confirm`: `RATE_LIMIT_APPROVAL_EMAIL_PER_MINUTE=2`; GET, GET → 303; POST confirm → 429 with exact detail and `Retry-After` (pin observed values); row unchanged, no approval.
15. `test_confirm_enqueue_failure` (DB4, email side): as test 7 but via confirm; `pytest.raises(RuntimeError)`; row running + decision + approval row.
16. `test_token_never_logged`: `caplog.set_level(logging.DEBUG)`; GET, confirm, confirm again (invalid), GET with a garbage token; assert `token not in record.getMessage()` and `token not in str(record.args)` and (if `record.exc_text`) not in it, for every record in `caplog.records`. Also the token's signature segment (`token.rsplit(".", 1)[1]`) appears nowhere.

**`test_resume_races.py`** (`install_resume_enqueue` everywhere; each test loops 3 times over a fresh paused row):

17. `test_five_concurrent_resumes_one_winner`: `gather(*(resume(client, member, eid, "approved") for _ in range(5)))` → exactly one 200; the other four 409 with detail in `{not_waiting("running"), lost_race(eid)}`; one approval row; `rec.calls == [(eid, "resume")]`; row `running` with `_resume_decision="approved"`. (Rate limit: 15 resumes per test < 60/min; call `reset_rate_limit_buckets()` at the top of each loop iteration anyway.)
18. `test_five_concurrent_confirms_of_one_token_one_winner`: five POSTs of the same approve token → exactly one `result_location("approved")`, four `result_location("invalid")`; one approval row (`via_email_link=True`); one enqueue.
19. `test_approve_and_reject_tokens_race`: three approve-token POSTs and three reject-token POSTs gathered → exactly one non-invalid redirect; the approval row's `decision` and the row's `_resume_decision` both equal the winner's decision.
20. `test_in_app_resume_vs_email_confirm`: gather(in-app resume `rejected`, confirm approve token) ×(1 each, then 3 each) → exactly one success among them; one approval row whose `via_email_link` matches the winner; `_resume_decision` matches the winner.
21. `test_resume_vs_cancel` (orders: resume-then-cancel, cancel-then-resume, gathered): cancel (`exec_api.cancel`) is always 200; resume is 200 or 409 `not_waiting("canceled")`/`lost_race(eid)`; final row `status="canceled"`, `error="Execution canceled by user request."`, lease NULL; `len(approvals_of) == (1 if resume was 200 else 0)`; `exec_api.claim(eid, "resume")` afterwards → `{"status": "already_claimed"}` and the row stays `canceled`. (Do not assert `variables` here: today an unguarded stamp may land after the cancel.)
22. `test_confirm_vs_cancel`: as 21 with the confirm (loser → invalid redirect).
23. `test_resume_vs_approval_expiry_sweep`: paused row with `since = iso(now - 2h)`; serial order A: sweep (`adapter.sweep_approvals(timeout_hours=1, now=datetime.now(UTC))`) then resume → row `failed` with error `"Execution expired waiting for approval after 1h with no decision. Auto-failed by the approval-timeout sweeper."`, resume 409 `not_waiting("failed")`, no approval; order B: resume then sweep → row `running`, sweep did not touch it; gathered ×3 → exactly one of {resume 200, row failed with that error}; approvals count 1 iff resume won.
24. `test_confirm_vs_approval_expiry_sweep`: as 23 with the confirm.

- [ ] **Step 1:** Write the conftest and the three modules exactly as above.
- [ ] **Step 2:** Prepare the throwaway Postgres (`docker run -d --rm --name composer-p4b-pg -e POSTGRES_PASSWORD=pg -p 55434:5432 postgres:15`; `TEST_DATABASE_URL=postgresql://postgres:pg@localhost:55434/postgres`; `DATABASE_URL=$TEST_DATABASE_URL uv run prisma migrate deploy && DATABASE_URL=$TEST_DATABASE_URL uv run alembic upgrade head`).
- [ ] **Step 3:** Run twice on unchanged code: `uv run pytest tests/integration/sqla_phase4b -m integration -v` (twice). Expected: all pass. Where today differs from the expectation above, change the **test** to match today and list each case in the commit message. Then check no test rows remain: `SELECT count(*) FROM approvals a LEFT JOIN workflow_executions e ON e.id = a.execution_id WHERE e.id IS NULL` = 0 and no `p4a-%@example.com` users remain.
- [ ] **Step 4:** Also run the 4a oracle (`uv run pytest tests/integration/sqla_phase4a -m integration`) to prove the re-exported fixtures did not disturb it.
- [ ] **Step 5:** `uv run ruff check tests/integration/sqla_phase4b && uv run ruff format --check tests/integration/sqla_phase4b && uv run pyright --pythonplatform Linux --pythonversion 3.12 tests/integration/sqla_phase4b`.
- [ ] **Step 6:** Commit: `test(db): pin phase 4b approval and resume behaviour on the Prisma code`.
- [ ] **Review gate:** Opus oracle review: "would a plausible wrong port pass?" Specifically: a port that commits the approval row in a separate transaction; a port that forgets `running_since` or the lease clear; a port whose confirm skips the pending-instance check; a port that stamps `_resume_decision` by overwriting `variables` with a stale snapshot; a port that enqueues inside the session (caught by Task 3's ordering test, not the oracle). Fix, re-review, before Task 2.

---

### Task 2: Approvals repository and `resume_if_waiting` (no callers yet)

**Files:**
- Create: `src/db/repos/approvals.py`
- Modify: `src/db/repos/executions.py` (append; do not change 4a's functions)
- Test: `tests/integration/db/repos/test_approvals.py`, `tests/integration/db/repos/test_executions_resume.py`

**Interfaces:**
- Consumes (4a, `src/db/repos/executions.py`): `ExecutionRecord`, `get`, `cancel_if_active`, `fail_expired_approval_if_unchanged`, `claim`; `src.db.models.WorkflowExecution`, `Approval`, `ApprovalDecision`; `src.db.errors.add_or_referenced_row_missing`, `ReferencedRowMissing`.
- Produces:

```python
# src/db/repos/approvals.py
@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    execution_id: str
    node_id: str
    approver_user_id: str | None
    approver_email: str | None
    via_email_link: bool
    decision: ApprovalDecision
    note: str | None
    created_at: datetime

async def create(
    session: AsyncSession,
    *,
    execution_id: str,
    node_id: str,
    decision: ApprovalDecision,
    approver_user_id: str | None,
    approver_email: str | None,
    via_email_link: bool,
    note: str | None,
) -> ApprovalRecord          # raises ReferencedRowMissing if the execution is gone

# src/db/repos/executions.py (additions)
@dataclass(frozen=True)
class PendingInstance:
    node_id: str
    since: str

async def resume_if_waiting(
    session: AsyncSession,
    execution_id: str,
    *,
    decision: str,
    now: datetime,
    expected_pending: PendingInstance | None = None,
) -> bool                     # G3 (expected_pending None) / G4
```

(Deviation from the 4a plan's sketch `resume_if_waiting(…) -> ClaimedExecution | None`: no caller needs the variables back, so it returns `bool` like every other guarded function, and it gains `expected_pending` for DB2.)

Implementation:

```python
# src/db/repos/approvals.py
"""approvals: one row per approval decision, written in the resume transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_referenced_row_missing
from src.db.models import Approval, ApprovalDecision


@dataclass(frozen=True)
class ApprovalRecord:
    id: str
    execution_id: str
    node_id: str
    approver_user_id: str | None
    approver_email: str | None
    via_email_link: bool
    decision: ApprovalDecision
    note: str | None
    created_at: datetime


def _record(row: Approval) -> ApprovalRecord:
    return ApprovalRecord(
        id=row.id,
        execution_id=row.execution_id,
        node_id=row.node_id,
        approver_user_id=row.approver_user_id,
        approver_email=row.approver_email,
        via_email_link=row.via_email_link,
        decision=ApprovalDecision(row.decision),
        note=row.note,
        created_at=row.created_at,
    )


async def create(
    session: AsyncSession,
    *,
    execution_id: str,
    node_id: str,
    decision: ApprovalDecision,
    approver_user_id: str | None,
    approver_email: str | None,
    via_email_link: bool,
    note: str | None,
) -> ApprovalRecord:
    """Insert one decision. Callers run it in the same transaction as the guarded resume."""
    row = Approval(
        execution_id=execution_id,
        node_id=node_id,
        decision=decision,
        approver_user_id=approver_user_id,
        approver_email=approver_email,
        via_email_link=via_email_link,
        note=note,
    )
    await add_or_referenced_row_missing(session, row)
    return _record(row)


__all__ = ["ApprovalRecord", "create"]
```

```python
# src/db/repos/executions.py — append (imports, if 4a did not already add them:
# `from sqlalchemy import cast, literal`, `from sqlalchemy.dialects.postgresql import JSONB`)

@dataclass(frozen=True)
class PendingInstance:
    """The approval pause a token was issued for (``_pending_approval_node``/``_since``)."""

    node_id: str
    since: str


async def resume_if_waiting(
    session: AsyncSession,
    execution_id: str,
    *,
    decision: str,
    now: datetime,
    expected_pending: PendingInstance | None = None,
) -> bool:
    """G3/G4: waiting_approval -> running, stamping ``_resume_decision`` in the same statement.

    With ``expected_pending`` the row must still be paused at that exact pause instance.
    """
    guard = [
        WorkflowExecution.id == execution_id,
        WorkflowExecution.status == "waiting_approval",
    ]
    if expected_pending is not None:
        guard += [
            WorkflowExecution.variables["_pending_approval_node"].astext
            == expected_pending.node_id,
            WorkflowExecution.variables["_pending_approval_since"].astext
            == expected_pending.since,
        ]
    won = await session.scalar(
        sa_update(WorkflowExecution)
        .where(*guard)
        .values(
            status="running",
            running_since=now,
            lease_owner=None,
            lease_expires_at=None,
            # jsonb || jsonb merges the key into the CURRENT row value. The right side is a
            # typed jsonb bind: an untyped str bind inside jsonb_build_object(VARIADIC "any")
            # fails with "could not determine data type of parameter".
            variables=WorkflowExecution.variables.op("||")(
                cast(literal({"_resume_decision": decision}, JSONB), JSONB)
            ),
        )
        .returning(WorkflowExecution.id)
        .execution_options(synchronize_session=False)
    )
    return won is not None
```

(`WorkflowExecution.variables` is `JSONB`, so `["key"].astext` renders `variables ->> 'key'`. If pyright strict rejects `.op("||")`'s return type for `.values`, wrap it with `type_coerce(…, JSONB)`; do not fall back to text SQL. The repo test `test_resume_overwrites_an_existing_decision_key` and the plain win test prove the merge keeps every other key and the stored value is a JSON object.)

- [ ] **Step 1: Repo tests first** (`@pytest.mark.integration`, real Postgres, seeding through SQLAlchemy models directly as `tests/integration/db/repos/test_executions.py` from 4a does):
  - `test_approvals.py`: `create` returns a full record (UUIDv7 `id`, `created_at` set and equal to the stored value, `decision` an `ApprovalDecision`), persists every column exactly (read back with `select(Approval)`); both `via_email_link` values; `note=None`; a missing `execution_id` raises `ReferencedRowMissing("approvals_execution_id_fkey")` and the session stays usable (a following `select(1)` works); rows cascade away when the execution is deleted.
  - `test_executions_resume.py`:
    - `test_resume_wins_only_from_waiting_approval`: for each status in `('queued','running','waiting_approval','completed','failed','canceled')` seed a row with `variables=paused`, a stale lease and `delivery_attempts=1`; call `resume_if_waiting(decision="approved", now=T)`; only `waiting_approval` returns `True`; losers are byte-identical (all-column snapshot); the winner differs from its snapshot in exactly `status`, `running_since == T`, `lease_owner`, `lease_expires_at`, `variables == {**paused, "_resume_decision": "approved"}`.
    - `test_resume_overwrites_an_existing_decision_key`: `variables` already holding `_resume_decision="rejected"` → `"approved"` after.
    - `test_resume_with_expected_pending`: matching instance → `True`; wrong `node_id` → `False`; wrong `since` → `False`; row without `_pending_approval_since` → `False`; each loser byte-identical.
    - `test_resume_on_missing_row`: `False`.
  - Race tests (same file, separate sessions from one sessionmaker, `asyncio.gather`, each repeated 10 times on a fresh row):
    - 5 × `resume_if_waiting` → exactly one `True`.
    - 3 × `resume_if_waiting(decision="approved", expected_pending=p)` + 3 × `(decision="rejected", expected_pending=p)` → exactly one `True`, and the stored `_resume_decision` is that winner's.
    - `resume_if_waiting` vs `fail_expired_approval_if_unchanged(variables_snapshot=paused, …)` → exactly one `True`; final status `running` or `failed` accordingly.
    - `resume_if_waiting` vs `cancel_if_active` gathered → `cancel_if_active` is always `True`; final status `canceled`; `resume_if_waiting` may be either (both orders legal).
    - `resume_if_waiting` then `claim(kind="resume")` in a separate session → `ClaimedExecution` whose `variables["_resume_decision"]` is the decision (the window DB1 closes: the decision is visible to the claim the instant the transition is).
    - Atomicity: in one session `resume_if_waiting` → `True`, then `approvals.create`, then `rollback()`; a separate session sees the row still `waiting_approval` with the original `variables` and no approval row.
- [ ] **Step 2:** Run them: `uv run pytest tests/integration/db/repos/test_approvals.py tests/integration/db/repos/test_executions_resume.py -m integration -v` → FAIL (`ImportError`/`AttributeError`).
- [ ] **Step 3:** Implement as above.
- [ ] **Step 4:** Run again → PASS (race tests ×3 runs). Also 4a's `tests/integration/db/repos/test_executions.py` and `test_executions_races.py` still pass. CI-parity pyright + ruff.
- [ ] **Step 5:** Commit: `feat(db): approvals repository and atomic approval resume (phase 4b)`.

---

### Task 3: Resume and emailed-link routes on SQLAlchemy

**Files:**
- Modify: `src/api/executions.py` (`resume_execution`; `ExecutionRead.model_config`), `src/api/approval_email.py` (whole module)
- Modify tests: `tests/unit/api/test_executions_resume.py`, `tests/unit/api/test_approval_email.py`
- Create: `tests/integration/sqla_phase4b/test_deliberate_changes.py`

Imports to add in `src/api/executions.py`: `import dataclasses`, `from src.db.models import ApprovalDecision`, `from src.db.repos import approvals as approvals_repo`, `from src.db.runtime import require_sessionmaker`, `from src.db.types import utcnow` (whichever 4a has not already added). In `src/api/approval_email.py`: the same plus `from src.db.repos import executions as executions_repo`, `from src.db.repos.executions import ExecutionRecord, PendingInstance`; keep `from src.storage.db import get_db`.

**Interfaces:**
- Consumes: Task 2's `approvals_repo.create`, `executions_repo.resume_if_waiting`, `PendingInstance`; 4a's `executions_repo.get`, `ExecutionRecord`, `_to_execution_read` (in `src/api/executions.py`); `require_sessionmaker`; `utcnow`.
- Produces: unchanged HTTP contracts.

`resume_execution` (replace the body after `enforce(…)`; keep the signature except `db: Any = Depends(get_db)` with the phase 5 TODO, and add `request: Request`):

```python
    sessionmaker = require_sessionmaker(request)
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, execution_id)
        if execution is None or (role != "admin" and execution.user_id != user_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Execution {execution_id!r} not found.",
            )
        if execution.status != "waiting_approval":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Execution is {execution.status!r}, not 'waiting_approval'.",
            )
        variables: Any = execution.variables
        pending_node_id = (
            variables.get("_pending_approval_node") if isinstance(variables, dict) else None
        )
        if pending_node_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Execution is waiting_approval but has no _pending_approval_node.",
            )
        # G3: one guarded UPDATE flips the status AND stamps the decision (phase 4b, DB1).
        won = await executions_repo.resume_if_waiting(
            session, execution_id, decision=payload.decision.value, now=utcnow()
        )
        if not won:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Execution {execution_id!r} is no longer waiting_approval "
                "(already resolved by a concurrent request).",
            )
        await approvals_repo.create(
            session,
            execution_id=execution_id,
            node_id=str(pending_node_id),
            decision=ApprovalDecision(payload.decision.value),
            approver_user_id=user_id,
            approver_email=None,
            via_email_link=False,
            note=payload.note,
        )
        await session.commit()
    # The session is closed: enqueue is network I/O (short-session rule).
    await enqueue_execution(execution_id, kind="resume", db=db)
    return _to_execution_read(dataclasses.replace(execution, status="running"))  # DB5
```

Note `node_id=str(pending_node_id)`: today Prisma would reject a non-string node id; node ids are always strings, so `str()` only satisfies the type checker. Keep the existing explanatory comments about P1-1 and P1-2 (updated to say "one statement" instead of "update_many then update").

`src/api/approval_email.py` (module docstring: replace "atomic conditional `update_many`" with "one guarded `UPDATE` that also checks the pause instance (`executions_repo.resume_if_waiting`)"; drop `from prisma import Json, Prisma`):

```python
def _verify(token: str) -> ApprovalEmailTokenPayload | None:
    try:
        return verify_approval_email_token(token)
    except TokenVerificationError:
        return None


def _matches_pause(execution: ExecutionRecord | None, claims: ApprovalEmailTokenPayload) -> bool:
    """Read-only check shared by GET and POST: the row is still paused at the token's instance."""
    if execution is None or execution.status != "waiting_approval":
        return False
    variables: Any = execution.variables
    if not isinstance(variables, dict):
        return False
    return (
        variables.get("_pending_approval_node") == claims.node_id
        and variables.get("_pending_approval_since") == claims.pending_since
    )


@router.get("/approvals/email/{token}")
async def resolve_approval_email(
    token: str,
    request: Request,
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(limiter, route_key="approval_email", client_key=ip,
                  config=per_minute_config(get_settings().rate_limit_approval_email_per_minute))
    sessionmaker = require_sessionmaker(request)
    claims = _verify(token)
    if claims is None:
        return _redirect_result("invalid")
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, claims.sub)
    if not _matches_pause(execution, claims):
        return _redirect_result("invalid")
    return _redirect_confirm(token, claims.decision)


@router.post("/approvals/email/{token}/confirm")
async def confirm_approval_email(
    token: str,
    request: Request,
    db: Any = Depends(get_db),  # TODO(phase 5): only for enqueue_execution's dev-mode checkpointer
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> RedirectResponse:  # pyright: ignore[reportUnusedFunction]
    ip = request.client.host if request.client else "unknown"
    await enforce(limiter, route_key="approval_email", client_key=ip,
                  config=per_minute_config(get_settings().rate_limit_approval_email_per_minute))
    sessionmaker = require_sessionmaker(request)
    claims = _verify(token)
    if claims is None:
        return _redirect_result("invalid")
    try:
        decision = ApprovalDecision(claims.decision)  # DB6
    except ValueError:
        return _redirect_result("invalid")
    async with sessionmaker() as session:
        execution = await executions_repo.get(session, claims.sub)
        if not _matches_pause(execution, claims):
            return _redirect_result("invalid")
        # G4: status AND pause instance re-checked in the same statement that resumes (DB2).
        won = await executions_repo.resume_if_waiting(
            session,
            claims.sub,
            decision=decision.value,
            now=utcnow(),
            expected_pending=PendingInstance(node_id=claims.node_id, since=claims.pending_since),
        )
        if not won:
            return _redirect_result("invalid")
        await approvals_repo.create(
            session,
            execution_id=claims.sub,
            node_id=claims.node_id,
            decision=decision,
            approver_user_id=None,
            approver_email=claims.approver_email,
            via_email_link=True,
            note=None,
        )
        await session.commit()
    await enqueue_execution(claims.sub, kind="resume", db=db)
    return _redirect_result(decision.value)
```

Order of checks matches today (rate limit, then token, then row), except that `require_sessionmaker` now runs before the token check, so a database-less app answers 503 instead of today's `RuntimeError` from `get_db` (fail closed; GET only reached `get_db` as a dependency before the body ran, so this is the same point in the request).

`ExecutionRead`: change `model_config = ConfigDict(populate_by_name=True, from_attributes=True)` to `ConfigDict(populate_by_name=True)` after confirming `grep -n "ExecutionRead.model_validate" src` is empty (every construction goes through `_to_execution_read`). Remove 4a's "Keep `ExecutionRead` accepting Prisma rows until 4b" comment if 4a left one.

- [ ] **Step 1: Unit ordering tests first** (`tests/unit/api/test_executions_resume.py`, `tests/unit/api/test_approval_email.py`): app with `app.state.db_sessionmaker = RecordingSessionmaker(events)`; patch `executions_repo.get` (returns an `ExecutionRecord` built with 4a's `make_execution` fake if 4a added one to `tests/unit/db_fakes.py`, else a local builder), `executions_repo.resume_if_waiting` (appends `"db:resume"`, returns `True`), `approvals_repo.create` (appends `"db:approval"`), `enqueue_execution` (appends `"net:enqueue"`), and `session.commit` (appends `"db:commit"`). Assert for resume and confirm:
  `events == ["session:enter", "db:resume", "db:approval", "db:commit", "session:exit", "net:enqueue"]` and `assert_no_network_inside_session(events)`. GET: `["session:enter", "session:exit"]`, no `db:resume`, no enqueue.
- [ ] **Step 2: Convert the remaining unit tests** of both files from Prisma `SimpleNamespace` rows / `update_many` mocks to the patched repos, keeping every assertion's intent: happy paths (approval `create` kwargs exact), 404/409/500/422, admin, `resume_if_waiting` returning `False` → 409 / invalid, malformed/expired/node-mismatch/stale-since/not-found tokens → invalid with **no** `resume_if_waiting` call, the enqueue-instead-of-background-task tests, `test_resume_does_not_emit_approval_resumed_event`, rate limits. Delete the two `…clears_stale_lease_in_same_update_many` tests and replace them with one assertion each that `resume_if_waiting` was awaited with `now=<frozen utcnow>` (patch `src.api.executions.utcnow` / `src.api.approval_email.utcnow`); the lease clear itself is a repo concern, covered in Task 2. If a GET-route test (`test_get_execution_non_owner_returns_404`) still lives in `test_executions_resume.py` and 4a has not converted it, move it to `tests/unit/api/test_executions.py` in 4a's style. Add: DB6 (token whose `decision` claim is `"maybe"`, minted with `jose.jwt.encode` and the test secret) → invalid, no repo write; no sessionmaker on `app.state` → 503 `database unavailable` for resume, GET and confirm.
- [ ] **Step 3: Regression tests for the deliberate changes** (`tests/integration/sqla_phase4b/test_deliberate_changes.py`, a NEW module in the oracle package so it gets the package's fixtures; its docstring says it is not part of the oracle: it pins DB1/DB2 and fails on the pre-port code; `pytestmark = [pytest.mark.integration, HTTPX_MARK]`; real Postgres):
  - `test_resume_rolls_back_when_the_approval_insert_fails` (DB1): monkeypatch `src.api.executions.approvals_repo.create` to raise `RuntimeError("insert failed")`; `pytest.raises(RuntimeError)` around the resume; row is still `waiting_approval` with its original `variables`, lease columns unchanged, no approval row, no enqueue. Same for the email confirm.
  - `test_confirm_with_a_stale_instance_after_repause_is_invalid` (DB2): capture `real_get = executions_repo.get`, then `monkeypatch.setattr(executions_repo, "get", wrapper)` where `wrapper(session, execution_id)` awaits `real_get`, then (first call only) re-pauses the row through a **separate** session (`db.set_execution(eid, variables=paused_variables(<new since>))`) and returns the **old** record; POST confirm with the token for the old instance → `result_location("invalid")`; row still `waiting_approval` with the **new** `since` and no `_resume_decision`, no approval row, no enqueue. (Without DB2 the old token resumes the new pause.)
  - The "decision visible the instant the row is running" half of DB1 is a repo test (Task 2, `resume_if_waiting` then `claim`), because the route offers no seam between the old code's two statements.
- [ ] **Step 4: Port** both routes as above.
- [ ] **Step 5: Verify:** the 4b oracle (`tests/integration/sqla_phase4b`: Task 1's conftest and three modules **unchanged**), the 4a oracle (`tests/integration/sqla_phase4a`, unchanged; group 12 crossover now runs SQLAlchemy end to end), `tests/integration/sqla_phase4b/test_deliberate_changes.py`, `tests/integration/test_user_approval_approved.py`, `test_user_approval_rejected.py`, `test_durable_execution.py`, `tests/regression` (patches `src.api.approval_email.enqueue_execution`; must still import), full unit suite, CI-parity pyright, ruff check + format check.
- [ ] **Step 6:** Commit: `feat(db): approval resume and emailed-link confirm on SQLAlchemy (phase 4b)`.

---

### Task 4: Lock, docs, CI, PR

- [ ] **Done-criterion greps** (each must print nothing):
  ```bash
  grep -rnE "\b(db|self\.db)\.(workflowexecution|executionevent|ratelimitbucket|approval)\." src
  grep -rn "\.approval\." src
  grep -rn "query_raw\|execute_raw\|\.tx(" src
  grep -rn "ExecutionRead.model_validate" src
  grep -rn "from prisma" src/api/approval_email.py src/api/executions.py
  ```
  and `grep -rnE "\bdb\.[a-z]+\.(find|create|update|delete|upsert|count|group)" src --include=*.py | grep -v "^src/db/"` prints only `src/storage/checkpointer.py` lines (phase 5).
- [ ] **`MIGRATED_PATHS`** (`tests/unit/db/test_prisma_import_boundary.py`): `src/api/executions.py` and `src/api/approval_email.py` still import `src.storage.db.get_db` (for `enqueue_execution(db=)`), so they **stay excluded**; update the module comment: "Phase 4b: executions.py and approval_email.py no longer query Prisma; they import `src.storage.db.get_db` only to hand `enqueue_execution` the client its dev-mode fallback checkpointer needs (phase 5)." Add `src/db/repos/approvals.py` implicitly (already under `src/db`).
- [ ] **CHANGELOG** (`[Unreleased]`, first entry): approvals and the approval-resume paths (in-app and emailed link) on SQLAlchemy; behaviour proven by `tests/integration/sqla_phase4b`; deliberate changes: DB1 (resume is one transaction; the decision is stamped in the guarded statement; closes the "running with no decision" and "lost audit row" windows), DB2 (emailed links re-check the pause instance in the guarded statement; found in `src/api/approval_email.py` — the stale-link window `pending_since` was meant to close), DB6 (malformed decision claim → invalid), 503 when the database is unavailable; kept-as-is (DB3, DB4, DB5); follow-ups.
- [ ] **Docs:** `docs/architecture.md` data-access section: add G3/G4 to the guarded-transition list and one line on "resume = one transaction, enqueue after commit"; spec §3.3 phase 4 row → "(done: 4a PR #<n>, 4b PR pending)"; this plan's checkboxes and an Outcome section (what changed vs plan, PR number, any oracle edits listed with reasons). Also tick the 4a plan's "Phase 4b scope" bullets as done with a link here.
- [ ] **Run everything CI runs** against the throwaway Postgres 15 from Task 1: `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests && uv run pyright --pythonplatform Linux --pythonversion 3.12 src tests && uv run pytest && uv run pytest -m "integration and not live"`. Delete the container when the phase is done (`docker stop composer-p4b-pg`).
- [ ] Commit (`docs(db): record phase 4b outcome`); push; open the PR; wait for all six checks; **merge only with Balaji's go-ahead** (merging deploys to production). After the deploy: one real run that pauses at an approval, resumed once in-app and once via the emailed link (a second run), both reaching `completed`; then 24 h of clean backend logs before phase 5 starts (spec §5.1).

---

## After 4b: what is still on Prisma (phase 5's starting list)

- **Checkpointer:** `src/storage/checkpointer.py` (`PrismaCheckpointSaver`: every read/write of `langgraph_checkpoints` / `langgraph_checkpoint_writes`), constructed in `src/storage/db.py:prisma_lifespan` (`app.state.checkpointer`) and in `src/execution/cloud_tasks.py:_run_in_process` (dev-mode fallback). Phase 5 replaces it with the psycopg `AsyncPostgresSaver` (spec §3.3), with the paused-run deploy guard (spec "Paused-run deploy guard").
- **Prisma client plumbing that exists only for the above:** `src/storage/db.py` (`prisma_lifespan`, `get_db`, `get_checkpointer`, `get_event_bus` if 4a left it there), `src/main.py` (`async with prisma_lifespan(app) as db`), and `db = Depends(get_db)` parameters whose sole use is `enqueue_execution(…, db=db)`: `src/api/executions.py` (create, resume), `src/api/approval_email.py` (confirm), `src/api/run.py`, `src/api/internal.py` (also `get_checkpointer`). When phase 5 makes the fallback build its checkpointer from the registered sessionmaker / psycopg pool, `enqueue_execution` drops `db` and all of these parameters go.
- **LISTEN/NOTIFY on asyncpg** (`src/engine/events_notify.py`, `src/api/events_ws.py`): phase 5 per 4a D7 and its design note.
- **Deletes of checkpoint rows** go through 4a's `src/db/repos/checkpoints.py:delete_threads` (already SQLAlchemy); phase 5 swaps it for the saver's `adelete_thread` plus the old-table clear.
- **Phase 6:** `prisma/` directory, the `prisma` dependency, `src/storage/db.py` itself, `MIGRATED_PATHS` → whole of `src`.

## Telemetry roadmap Phase 5 hook points (after 4b)

`docs/superpowers/plans/2026-11-20-telemetry-roadmap.md` Task 5.1 adds an `execution_attempts` history "in claim, pause, resume, terminal transition, and sweeper services". After 4b every one of those is a single guarded repo function in `src/db/repos/executions.py`, each returning whether it won, each run inside a caller-owned transaction; the attempt write belongs **in the same transaction, only when the function returned a winner**, so "at most one winning attempt" follows from the guard:

| Lifecycle point | Repo function (module `src/db/repos/executions.py` unless noted) | Caller that owns the transaction |
|---|---|---|
| Claim (initial run and resume) — opens an attempt | `claim(session, id, *, kind, worker_id, now, lease_expires_at) -> ClaimedExecution \| None` | `src/api/internal.py:claim_and_run_execution` |
| Pause — closes the attempt (result `waiting_approval`) | `mark_waiting_approval_if_running(session, id, *, variables) -> bool` | `LangGraphExecutor._mark_waiting_approval` |
| Resume trigger — records the actor/decision; the next attempt opens at the resume-kind claim | `resume_if_waiting(session, id, *, decision, now, expected_pending=None) -> bool` + `approvals.create(…)` (`src/db/repos/approvals.py`; its row id is the natural "actor/reason reference") | `src/api/executions.py:resume_execution`, `src/api/approval_email.py:confirm_approval_email` |
| Terminal: completed / failed / canceled / enqueue-failed | `mark_completed_if_running`, `mark_failed_if_running`, `cancel_if_active`, `mark_enqueue_failed_if_queued` | executor `_mark_*`; claim crash handler; cancel route; create route |
| Sweeper terminals | `fail_orphaned_if_unchanged`, `fail_expired_approval_if_unchanged`, `fail_expired_lease_if_unchanged` | `src/maintenance/execution_sweeper.py` (one short session per row) |

Telemetry Task 7.2's "protect recovery/cancel/resume with one conditional state transition" should add its recovery transition as another function of the same shape next to `resume_if_waiting`.

## Follow-ups (not in this phase)

- DB3: move the approval token out of the frontend query string (fragment or opaque confirm id); optional dedicated signing key for approval links.
- DB4: handle an enqueue failure after a resume (re-enqueue with backoff, or fail at once) instead of waiting for the orphan sweeper.
- DB2 for in-app resume: send the displayed `pendingSince` from the run page and guard G3 on it.
- 4a D9 leftover: a resume-kind claim without `_resume_decision` should fail the row at once; after DB1 the only remaining cause is a row resumed by something other than these two routes (none exist).
- Return the post-transition row from resume (RETURNING) instead of the pre-transition snapshot (DB5), together with 4a's cancel follow-up.
