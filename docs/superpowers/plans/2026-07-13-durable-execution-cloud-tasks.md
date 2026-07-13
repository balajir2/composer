# Durable Execution (Cloud Tasks) + Postgres Events/Rate-Limits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Composer's request-bound execution model (`BackgroundTasks`/`asyncio.create_task`, killed by Cloud Run scale-to-zero) with Google Cloud Tasks-triggered durable execution, and replace the in-process event bus and rate limiter with Postgres-backed equivalents that work correctly across multiple Cloud Run instances.

**Architecture:** `POST /executions` creates a `WorkflowExecution` row with `status='queued'` and enqueues a Cloud Task carrying the execution ID. Cloud Tasks delivers an authenticated (OIDC) HTTP POST to a new `/internal/claim-and-run` endpoint; because it's a real inbound request, Cloud Run keeps the instance alive for its duration. The endpoint claims the row via `SELECT ... FOR UPDATE SKIP LOCKED` (already validated in ADR-0031/`scripts/poc_persistence_row_lock.py`) before running it — Cloud Tasks' at-least-once delivery means the row-lock, not Cloud Tasks, is the actual single-claim guarantee. A lease/heartbeat column plus an extension of the existing stuck-execution sweeper recovers executions whose worker died mid-run. Events move from an in-process `asyncio.Queue` fan-out to a persisted, sequence-numbered Postgres table plus `LISTEN/NOTIFY` for low-latency delivery to already-connected WebSocket clients. Rate limiting moves from an in-memory token bucket to an atomic Postgres UPDATE-based bucket table.

**Tech Stack:** `google-cloud-tasks` (new dependency), Prisma Python (existing), Postgres `LISTEN/NOTIFY` via `asyncpg`-level raw connection (Prisma's `query_raw`/`execute_raw` don't expose async notification callbacks — see Task 7 for the resolution), FastAPI, existing test stack (pytest, `pytest_httpx`/`AsyncMock`).

**Full design record:** ADR-0033 in `docs/decisions.md`. This plan implements the approach that ADR proposed and the user approved (full replacement of the in-process model, P1-2 and P1-4 together).

**On "external side effects are not duplicated during recovery" (a P1-2 acceptance criterion):** when `sweep_expired_leases` (Task 11) re-enqueues a died execution, the eventual `executor.run(execution_id)` call in claim-and-run re-invokes `compiled.ainvoke(state, config={"configurable": {"thread_id": execution.threadId}})` against the SAME thread_id. This is the identical mechanism the existing approval `resume()` flow already relies on (ADR-0029, proven in production this session) — LangGraph's checkpointer resumes from the last committed checkpoint for that thread_id rather than restarting the graph, so nodes that already completed (and had side effects like a Jira create) before the worker died are not re-executed; only the node in progress at the time of death (and anything after it) runs. This is inherited behavior, not new code this plan needs to add — flagged here so the plan's task list doesn't appear to have silently skipped this acceptance criterion.

**On cancellation:** `POST /executions/{id}/cancel` (built in this session's P1-6 work) is untouched by this plan and continues to work exactly as before — it atomically transitions the row's status but does not preempt an in-flight claim-and-run request (documented limitation at the time it was built). This limitation becomes more consequential once executions can legitimately run for up to `execution_lease_seconds` (an hour, per this plan's design) inside one HTTP request rather than a few seconds of in-process background work — true mid-request preemption (e.g., cooperative cancellation checks between node executions) is out of scope for this plan and should be tracked as a explicit follow-up if it becomes a real operational problem, not silently assumed solved here.

---

## Task 1: Schema migration — lease/heartbeat, events table, rate-limit table

**Files:**
- Modify: `prisma/schema.prisma`
- Create: `prisma/migrations/<timestamp>_durable_execution/migration.sql` (generated, not hand-written — see steps)

- [ ] **Step 1: Add the new fields/models to `prisma/schema.prisma`**

Add to `WorkflowExecution` (after the existing `idempotencyKey` field, before the closing brace):

```prisma
  leaseOwner    String?   @map("lease_owner")
  leaseExpiresAt DateTime? @map("lease_expires_at")
  deliveryAttempts Int    @default(0) @map("delivery_attempts")
```

Add two new top-level models at the end of the file:

```prisma
model ExecutionEvent {
  id            String   @id @default(cuid())
  executionId   String   @map("execution_id")
  seq           Int
  type          String
  payload       Json
  createdAt     DateTime @default(now()) @map("created_at")

  @@map("execution_events")
  @@unique([executionId, seq])
  @@index([executionId])
}

model RateLimitBucket {
  id              String   @id @default(cuid())
  routeKey        String   @map("route_key")
  clientKey       String   @map("client_key")
  tokens          Float
  lastRefill      DateTime @map("last_refill")

  @@map("rate_limit_buckets")
  @@unique([routeKey, clientKey])
}
```

- [ ] **Step 2: Generate the migration SQL without applying (matches the non-interactive workflow already used for the `idempotencyKey` migration)**

```bash
.venv/Scripts/python -m prisma migrate diff --from-url "$(grep '^DATABASE_URL' .env | cut -d= -f2-)" --to-schema-datamodel prisma/schema.prisma --script > /tmp/durable_execution_migration.sql
cat /tmp/durable_execution_migration.sql
```

Expected output (verify it matches before proceeding — do not apply if it differs):

```sql
-- AlterTable
ALTER TABLE "workflow_executions" ADD COLUMN     "lease_owner" TEXT,
ADD COLUMN     "lease_expires_at" TIMESTAMP(3),
ADD COLUMN     "delivery_attempts" INTEGER NOT NULL DEFAULT 0;

-- CreateTable
CREATE TABLE "execution_events" (
    "id" TEXT NOT NULL,
    "execution_id" TEXT NOT NULL,
    "seq" INTEGER NOT NULL,
    "type" TEXT NOT NULL,
    "payload" JSONB NOT NULL,
    "created_at" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "execution_events_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "rate_limit_buckets" (
    "id" TEXT NOT NULL,
    "route_key" TEXT NOT NULL,
    "client_key" TEXT NOT NULL,
    "tokens" DOUBLE PRECISION NOT NULL,
    "last_refill" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "rate_limit_buckets_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "execution_events_execution_id_idx" ON "execution_events"("execution_id");

-- CreateIndex
CREATE UNIQUE INDEX "execution_events_execution_id_seq_key" ON "execution_events"("execution_id", "seq");

-- CreateIndex
CREATE UNIQUE INDEX "rate_limit_buckets_route_key_client_key_key" ON "rate_limit_buckets"("route_key", "client_key");
```

- [ ] **Step 3: Create the migration folder and apply it (same pattern as `prisma/migrations/20260712150606_add_execution_idempotency_key`)**

```bash
TS=$(date -u +%Y%m%d%H%M%S)
DIR="prisma/migrations/${TS}_durable_execution"
mkdir -p "$DIR"
cp /tmp/durable_execution_migration.sql "$DIR/migration.sql"
.venv/Scripts/python -m prisma migrate deploy
```

Expected: `All migrations have been successfully applied.`

- [ ] **Step 4: Regenerate the Prisma client**

```bash
export PATH="$(pwd)/.venv/Scripts:$PATH"
.venv/Scripts/python -m prisma generate
```

Expected: `Generated Prisma Client Python ... to .\.venv\Lib\site-packages\prisma`

- [ ] **Step 5: Commit**

```bash
git add prisma/schema.prisma prisma/migrations
git commit -m "feat(db): add lease/heartbeat columns + ExecutionEvent + RateLimitBucket models (P1-2/P1-4 foundations)"
```

---

## Task 2: Config settings for Cloud Tasks

**Files:**
- Modify: `src/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`:

```python
def test_cloud_tasks_settings_have_safe_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config import get_settings

    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("CLOUD_TASKS_QUEUE", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.gcp_project_id == ""
    assert settings.gcp_region == "us-central1"
    assert settings.cloud_tasks_queue == "composer-executions"
    assert settings.cloud_tasks_service_account == ""
    assert settings.execution_lease_seconds == 3600
    assert settings.execution_max_delivery_attempts == 5
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/test_config.py::test_cloud_tasks_settings_have_safe_defaults -q --no-cov
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'gcp_project_id'`

- [ ] **Step 3: Add the settings**

In `src/config.py`, inside `class Settings(BaseSettings):`, add a new section after the `rate_limit_*` fields:

```python
    # ─── Cloud Tasks (P1-2 durable execution) ──────────────────────
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"
    cloud_tasks_queue: str = "composer-executions"
    # Service account Cloud Tasks uses to mint the OIDC token it presents
    # to the claim-and-run endpoint. Empty in dev (auth skipped when unset
    # — see src/api/internal.py's _verify_cloud_tasks_oidc).
    cloud_tasks_service_account: str = ""
    # DESIGN DECISION (P1-2, "lease and heartbeat behavior"): rather than a
    # short lease with periodic mid-execution heartbeat renewal (the usual
    # pattern for long batch jobs), claim-and-run (src/api/internal.py) runs
    # an execution to completion synchronously within ONE bounded Cloud
    # Tasks-delivered HTTP request. The lease is sized to match — not
    # exceed — that request's own maximum duration (Cloud Run's configured
    # request timeout), so it needs no separate renewal: the lease *is* the
    # heartbeat, because "the request is still running" and "the lease is
    # still valid" cover the same span by construction. If Cloud Run's
    # request timeout for the service ever changes, this must change with
    # it — keep them equal, don't drift.
    execution_lease_seconds: int = 3600
    # How many times sweep_expired_leases will re-enqueue a fresh Cloud
    # Task for the same execution before giving up and dead-lettering it
    # (marking `failed` rather than retrying indefinitely).
    execution_max_delivery_attempts: int = 5
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/test_config.py::test_cloud_tasks_settings_have_safe_defaults -q --no-cov
```

Expected: PASS

- [ ] **Step 5: Document the new env vars in `.env.example`**

Add after the existing `APPROVAL_*` settings block:

```
# ─── Cloud Tasks (Phase P1-2 durable execution) ──
GCP_PROJECT_ID=
GCP_REGION=us-central1
CLOUD_TASKS_QUEUE=composer-executions
CLOUD_TASKS_SERVICE_ACCOUNT=
# Must match Cloud Run's configured request timeout for this service —
# see the design-decision comment on this setting in src/config.py.
EXECUTION_LEASE_SECONDS=3600
EXECUTION_MAX_DELIVERY_ATTEMPTS=5
```

- [ ] **Step 6: Commit**

```bash
git add src/config.py .env.example tests/unit/test_config.py
git commit -m "feat(config): add Cloud Tasks settings (P1-2)"
```

---

## Task 3: Postgres-backed atomic rate limiter

**Files:**
- Create: `src/security/rate_limit_pg.py`
- Test: `tests/unit/security/test_rate_limit_pg.py`

This replaces `src/security/rate_limit.py`'s in-memory `TokenBucket`/`RateLimiter` with a Postgres-backed equivalent using the `RateLimitBucket` model from Task 1. Same public shape (`check(route_key, client_key, config) -> (allowed, retry_after)`) so call sites (`enforce()`, all the `route_key="..."` usages across `src/api/*.py`) don't need to change beyond the dependency wiring in Task 4.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/security/test_rate_limit_pg.py`:

```python
"""Tests for the Postgres-backed atomic rate limiter (P1-4)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.security.rate_limit_pg import PostgresRateLimiter
from src.security.rate_limit import BucketConfig


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.ratelimitbucket = MagicMock()
    return db


async def test_first_request_creates_bucket_and_allows() -> None:
    db = _mock_db()
    db.ratelimitbucket.find_unique = AsyncMock(return_value=None)
    db.ratelimitbucket.create = AsyncMock()
    limiter = PostgresRateLimiter(db)
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=0.5)
    )
    assert allowed is True
    assert retry_after == 0.0
    db.ratelimitbucket.create.assert_awaited_once()
    created = db.ratelimitbucket.create.await_args.kwargs["data"]
    assert created["tokens"] == 4.0  # capacity(5) - 1 consumed


async def test_exhausted_bucket_denies_with_retry_after() -> None:
    db = _mock_db()
    now = datetime.now(UTC)
    db.ratelimitbucket.find_unique = AsyncMock(
        return_value=MagicMock(tokens=0.0, lastRefill=now)
    )
    limiter = PostgresRateLimiter(db)
    allowed, retry_after = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is False
    assert retry_after > 0


async def test_refill_over_elapsed_time_allows_again() -> None:
    db = _mock_db()
    stale = datetime.now(UTC) - timedelta(seconds=10)
    db.ratelimitbucket.find_unique = AsyncMock(
        return_value=MagicMock(tokens=0.0, lastRefill=stale)
    )
    db.ratelimitbucket.update = AsyncMock()
    limiter = PostgresRateLimiter(db)
    # refill_per_second=1.0, 10s elapsed -> 10 tokens refilled, capped at capacity
    allowed, _ = await limiter.check(
        "executions", "user1", BucketConfig(capacity=5, refill_per_second=1.0)
    )
    assert allowed is True
    updated = db.ratelimitbucket.update.await_args.kwargs["data"]
    assert updated["tokens"] == 4.0  # capped at capacity(5), minus 1 consumed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_rate_limit_pg.py -q --no-cov
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.security.rate_limit_pg'`

- [ ] **Step 3: Write the implementation**

Create `src/security/rate_limit_pg.py`:

```python
"""Postgres-backed atomic rate limiter (P1-4).

Same token-bucket algorithm as src/security/rate_limit.py's in-memory
RateLimiter, but state lives in the `rate_limit_buckets` table instead of
a process-local dict — correct across multiple Cloud Run instances,
which the in-memory version was not (ADR-0033).

Uses a plain find_unique + upsert rather than a single atomic UPDATE
statement: the token-bucket refill computation depends on wall-clock
elapsed time since last_refill, which must be read before it can be
written, so a single UPDATE...WHERE (as used elsewhere in this codebase
for simple conditional transitions) can't express the refill formula in
SQL without a round-trip anyway. This is not a correctness gap for
BucketConfig at Composer's current concurrency (rate-limit checks are
per end-user request, not a high-contention shared counter); revisit
with a single UPDATE ... SET tokens = LEAST(capacity, tokens + ...)
RETURNING tokens if contention ever becomes a real issue.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.security.rate_limit import BucketConfig


class PostgresRateLimiter:
    """Drop-in replacement for RateLimiter with Postgres-backed state."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]:
        """Check + consume one token. Returns (allowed, retry_after_seconds)."""
        row = await self.db.ratelimitbucket.find_unique(
            where={"routeKey_clientKey": {"routeKey": route_key, "clientKey": client_key}}
        )
        now = datetime.now(UTC)

        if row is None:
            tokens = float(config.capacity) - 1.0
            await self.db.ratelimitbucket.create(
                data={
                    "routeKey": route_key,
                    "clientKey": client_key,
                    "tokens": tokens,
                    "lastRefill": now,
                }
            )
            return True, 0.0

        elapsed = (now - row.lastRefill).total_seconds()
        tokens = min(float(config.capacity), row.tokens + elapsed * config.refill_per_second)

        if tokens >= 1.0:
            tokens -= 1.0
            await self.db.ratelimitbucket.update(
                where={"routeKey_clientKey": {"routeKey": route_key, "clientKey": client_key}},
                data={"tokens": tokens, "lastRefill": now},
            )
            return True, 0.0

        missing = 1.0 - tokens
        retry_after = missing / config.refill_per_second
        await self.db.ratelimitbucket.update(
            where={"routeKey_clientKey": {"routeKey": route_key, "clientKey": client_key}},
            data={"tokens": tokens, "lastRefill": now},
        )
        return False, retry_after


__all__ = ["PostgresRateLimiter"]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_rate_limit_pg.py -q --no-cov
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/security/rate_limit_pg.py tests/unit/security/test_rate_limit_pg.py
git commit -m "feat(security): add Postgres-backed atomic rate limiter (P1-4)"
```

---

## Task 4: Wire the Postgres rate limiter into the app

**Files:**
- Modify: `src/storage/db.py`
- Modify: `src/security/rate_limit.py` (the `get_rate_limiter` dependency + `enforce()` need to accept either limiter type, or `enforce()` moves to accept the Postgres one directly)
- Test: `tests/unit/api/test_executions.py` (existing tests must keep passing with the new limiter wired in)

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/security/test_rate_limit_pg.py`:

```python
async def test_enforce_raises_429_when_denied() -> None:
    from fastapi import HTTPException

    from src.security.rate_limit import BucketConfig, enforce

    db = _mock_db()
    db.ratelimitbucket.find_unique = AsyncMock(
        return_value=MagicMock(tokens=0.0, lastRefill=datetime.now(UTC))
    )
    db.ratelimitbucket.update = AsyncMock()
    limiter = PostgresRateLimiter(db)
    with pytest.raises(HTTPException) as exc_info:
        await enforce(limiter, route_key="executions", client_key="u1", config=BucketConfig(capacity=1, refill_per_second=0.01))
    assert exc_info.value.status_code == 429
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/security/test_rate_limit_pg.py::test_enforce_raises_429_when_denied -q --no-cov
```

Expected: FAIL — `enforce()` currently type-hints `limiter: RateLimiter`, and calling `.check()` on `PostgresRateLimiter` works structurally (duck typing) so this may actually pass immediately. If it does, skip to Step 4 — this step exists to confirm `enforce()` doesn't need changes, only the dependency wiring does.

- [ ] **Step 3: Loosen `enforce()`'s type hint (structural, not behavioral change)**

In `src/security/rate_limit.py`, change:

```python
async def enforce(
    limiter: RateLimiter,
```

to:

```python
from typing import Protocol


class _RateLimiterProtocol(Protocol):
    async def check(
        self, route_key: str, client_key: str, config: BucketConfig
    ) -> tuple[bool, float]: ...


async def enforce(
    limiter: "_RateLimiterProtocol",
```

Add `_RateLimiterProtocol` to `__all__`.

- [ ] **Step 4: Wire `PostgresRateLimiter` into `app.state` instead of the in-memory `RateLimiter`**

In `src/storage/db.py`, replace:

```python
    from src.security.rate_limit import RateLimiter

    app.state.rate_limiter = RateLimiter()
```

with:

```python
    from src.security.rate_limit_pg import PostgresRateLimiter

    app.state.rate_limiter = PostgresRateLimiter(db)
```

Update `get_rate_limiter`'s return type in `src/security/rate_limit.py` to `_RateLimiterProtocol` and its `getattr(request.app.state, "rate_limiter", None)` — no change needed there, only the type hint.

- [ ] **Step 5: Run the full existing rate-limit-touching test suite**

```bash
.venv/Scripts/python -m pytest tests/unit/security/ tests/unit/api/test_executions.py tests/unit/api/test_run_external.py -q --no-cov
```

Expected: all pass. If any test constructs `RateLimiter()` directly and assigns it to `app.state.rate_limiter` (several do, per this session's earlier P1-1/P1-3/P1-6 test fixtures), those tests keep using the in-memory one — that's fine, it's still a valid `_RateLimiterProtocol` implementer; only the *app's own* default wiring changes to Postgres-backed.

- [ ] **Step 6: Commit**

```bash
git add src/storage/db.py src/security/rate_limit.py tests/unit/security/test_rate_limit_pg.py
git commit -m "feat(security): wire Postgres rate limiter into app.state (P1-4)"
```

---

## Task 5: Persisted execution events + sequence numbers

**Files:**
- Create: `src/engine/events_pg.py`
- Test: `tests/unit/engine/test_events_pg.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/engine/test_events_pg.py`:

```python
"""Tests for persisted execution events (P1-4)."""

from unittest.mock import AsyncMock, MagicMock

from src.engine.events import ExecutionEvent
from src.engine.events_pg import PostgresEventStore


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.executionevent = MagicMock()
    return db


async def test_append_assigns_incrementing_sequence_numbers() -> None:
    db = _mock_db()
    db.executionevent.count = AsyncMock(side_effect=[0, 1])
    db.executionevent.create = AsyncMock()
    store = PostgresEventStore(db)

    e1 = ExecutionEvent(type="node_started", execution_id="ex1", payload={"nodeId": "n1"})
    e2 = ExecutionEvent(type="node_completed", execution_id="ex1", payload={"nodeId": "n1"})
    seq1 = await store.append(e1)
    seq2 = await store.append(e2)

    assert seq1 == 1
    assert seq2 == 2
    assert db.executionevent.create.await_count == 2


async def test_list_since_returns_events_after_cursor() -> None:
    db = _mock_db()
    db.executionevent.find_many = AsyncMock(
        return_value=[
            MagicMock(seq=3, type="node_completed", executionId="ex1", payload={"a": 1}),
            MagicMock(seq=4, type="workflow_completed", executionId="ex1", payload={"b": 2}),
        ]
    )
    store = PostgresEventStore(db)
    events = await store.list_since("ex1", after_seq=2)
    assert [e.type for e in events] == ["node_completed", "workflow_completed"]
    assert [e.seq for e in events] == [3, 4]
    db.executionevent.find_many.assert_awaited_once_with(
        where={"executionId": "ex1", "seq": {"gt": 2}},
        order={"seq": "asc"},
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_pg.py -q --no-cov
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.events_pg'`

- [ ] **Step 3: Add the `seq` field to `ExecutionEvent` and include it in `as_json()` — must land before Step 4, which constructs `ExecutionEvent(..., seq=...)`**

`src/engine/events.py`'s `ExecutionEvent` dataclass currently has no `seq` field — without one, `list_since`'s reconstructed events (Step 4 below) have nowhere to carry their sequence number, and a WebSocket client receiving a replayed event would have no way to know what cursor to reconnect with next time. Read `src/engine/events.py` in full before editing (it's short — reproduced here for reference from earlier in this session):

Change:

```python
@dataclass(frozen=True)
class ExecutionEvent:
    type: EventType
    execution_id: str = field(metadata={"alias": "executionId"})
    tenant_id: str | None = field(default=None, metadata={"alias": "tenantId"})
    timestamp: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        """Returns DES-007-shape dict with camelCase keys."""
        out: dict[str, Any] = {
            "type": self.type,
            "executionId": self.execution_id,
            "tenantId": self.tenant_id,
            "timestamp": self.timestamp,
        }
        out.update(self.payload)
        return out
```

to:

```python
@dataclass(frozen=True)
class ExecutionEvent:
    type: EventType
    execution_id: str = field(metadata={"alias": "executionId"})
    tenant_id: str | None = field(default=None, metadata={"alias": "tenantId"})
    timestamp: str = field(default_factory=_now_iso)
    payload: dict[str, Any] = field(default_factory=dict)
    # P1-4: populated by PostgresEventStore.list_since when reconstructing
    # a persisted event; None for a freshly-constructed event that hasn't
    # been assigned a sequence number yet (PostgresEventStore.append
    # returns the assigned seq separately rather than mutating the frozen
    # dataclass in place).
    seq: int | None = field(default=None)

    def as_json(self) -> dict[str, Any]:
        """Returns DES-007-shape dict with camelCase keys."""
        out: dict[str, Any] = {
            "type": self.type,
            "executionId": self.execution_id,
            "tenantId": self.tenant_id,
            "timestamp": self.timestamp,
            "seq": self.seq,
        }
        out.update(self.payload)
        return out
```

Run the existing `tests/unit/engine/test_events*.py` and `tests/unit/api/test_events_ws.py` suites after this change — `seq` defaults to `None` so no existing test asserting exact `as_json()` dict equality should break unless one does a strict dict-equality assertion that would now fail due to the added key. Fix any such assertion to include `"seq": None` (or use `assert data["type"] == ...` style partial assertions if that's less brittle — match whichever style the failing test already mostly uses).

- [ ] **Step 4: Write the `PostgresEventStore` implementation**

Create `src/engine/events_pg.py`:

```python
"""Persisted execution events (P1-4).

Replaces src/engine/events.py's ExecutionEventBus in-process asyncio
fan-out with a Postgres-backed store: every event is appended to the
`execution_events` table with a per-execution incrementing sequence
number, giving reconnect-cursor support (a client can ask for
"everything after seq N") and missed-terminal-event recovery that the
in-process version could not provide (ADR-0033).

Low-latency delivery to already-connected WebSocket clients is handled
separately by Postgres LISTEN/NOTIFY (src/engine/events_notify.py) —
this module is the durable record; that module is the wake-up signal.
"""

from __future__ import annotations

from typing import Any

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]

from src.engine.events import ExecutionEvent


class PostgresEventStore:
    """Durable, sequence-numbered event log per execution."""

    def __init__(self, db: Any) -> None:
        self.db = db

    async def append(self, event: ExecutionEvent) -> int:
        """Persist `event`, returning its assigned sequence number.

        Only `event.payload` (the original, unflattened dict) is stored
        in the `payload` column — not the output of `event.as_json()`,
        which would redundantly re-embed `type`/`executionId`/`timestamp`
        that already have their own columns.

        Sequence numbers start at 1 per execution. Not a single atomic
        INSERT...SELECT COALESCE(MAX(seq),0)+1 to keep the Prisma-ORM
        path simple; a genuine concurrent-write race on the same
        execution's event stream is vanishingly unlikely in practice
        (nodes within one execution run sequentially), but if a race
        is ever observed, tighten via a raw SQL INSERT with a
        window-function-derived seq instead.
        """
        count = await self.db.executionevent.count(where={"executionId": event.execution_id})
        seq = count + 1
        await self.db.executionevent.create(
            data={
                "executionId": event.execution_id,
                "seq": seq,
                "type": event.type,
                "payload": Json(event.payload),
            }
        )
        return seq

    async def list_since(self, execution_id: str, *, after_seq: int) -> list[ExecutionEvent]:
        """Return all events for `execution_id` with seq > after_seq, ascending."""
        rows = await self.db.executionevent.find_many(
            where={"executionId": execution_id, "seq": {"gt": after_seq}},
            order={"seq": "asc"},
        )
        return [
            ExecutionEvent(
                type=row.type, execution_id=row.executionId, seq=row.seq, payload=row.payload
            )
            for row in rows
        ]


__all__ = ["PostgresEventStore"]
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_pg.py -q --no-cov
```

Expected: 2 passed

- [ ] **Step 6: Wire explicit deletion — `execution_events` has no FK to `WorkflowExecution`, so it won't cascade-delete**

`ExecutionEvent` in Task 1's schema has no `@relation`/`onDelete: Cascade` to `WorkflowExecution` (deliberately — matching this codebase's existing convention for `LangGraphCheckpoint`/`LangGraphCheckpointWrite`, which also have no FK "by design", per `src/storage/checkpointer.py`'s `adelete_thread` docstring, and are deleted explicitly). Without explicit deletion, `DELETE /executions/{id}` and `POST /executions/delete-bulk` (`src/api/executions.py`) would orphan every execution's events permanently.

Read `src/api/executions.py`'s `delete_execution` and `delete_executions_bulk` functions in full before editing — both already delete `langgraphcheckpointwrite`/`langgraphcheckpoint` rows explicitly, in FK-safe order, before deleting the execution row itself. Add `execution_events` deletion to both, in the same position (alongside the checkpoint cleanup, before the execution row itself is deleted):

In `delete_execution`, add after the existing `langgraphcheckpoint.delete_many` call:
```python
    await db.executionevent.delete_many(where={"executionId": execution_id})  # pyright: ignore[reportAttributeAccessIssue]
```

In `delete_executions_bulk`, add after the existing `langgraphcheckpoint.delete_many` call:
```python
    await db.executionevent.delete_many(where={"executionId": {"in": target_ids}})  # pyright: ignore[reportAttributeAccessIssue]
```

Add a test to `tests/unit/api/test_executions.py` mirroring the existing checkpoint-deletion assertions (`db.langgraphcheckpointwrite.delete_many.assert_awaited_once_with(...)`) for `db.executionevent.delete_many`.

- [ ] **Step 7: Commit**

```bash
git add src/engine/events.py src/engine/events_pg.py src/api/executions.py tests/unit/engine/test_events_pg.py tests/unit/api/test_executions.py tests/unit/engine/ tests/unit/api/test_events_ws.py
git commit -m "feat(observability): add persisted, sequence-numbered execution events (P1-4)"
```

---

## Task 6: Postgres LISTEN/NOTIFY wake-up signal

**Files:**
- Create: `src/engine/events_notify.py`
- Test: `tests/unit/engine/test_events_notify.py`

Prisma Python's `query_raw`/`execute_raw` don't expose an async `LISTEN` callback API (they're request/response, not subscription-based) — `LISTEN/NOTIFY` needs a raw `asyncpg` (or equivalent) connection outside the Prisma client. This task adds a small, isolated connection manager for exactly that, reusing `DATABASE_URL` from settings.

- [ ] **Step 1: Confirm `asyncpg` availability**

```bash
.venv/Scripts/python -c "import asyncpg; print(asyncpg.__version__)"
```

If this fails with `ModuleNotFoundError`, add it: edit `pyproject.toml`'s `dependencies` list, adding `"asyncpg>=0.30.0",` under a new `# Postgres LISTEN/NOTIFY (P1-4)` comment near the Prisma dependency, then run `uv sync --all-extras` (or the absolute-path equivalent per this machine's tool-path memory) before continuing.

- [ ] **Step 2: Write the failing test**

Create `tests/unit/engine/test_events_notify.py`:

```python
"""Tests for the Postgres LISTEN/NOTIFY wake-up signal (P1-4)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.events_notify import notify_execution_event


async def test_notify_sends_channel_and_payload_under_size_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()

    async def _fake_get_conn() -> MagicMock:
        return fake_conn

    monkeypatch.setattr("src.engine.events_notify._get_notify_connection", _fake_get_conn)

    await notify_execution_event("ex1", seq=5)

    fake_conn.execute.assert_awaited_once()
    call_args = fake_conn.execute.await_args.args
    assert call_args[0].startswith("SELECT pg_notify(")
    # payload is "ex1:5" — well under the 8000-byte NOTIFY cap
    assert "ex1:5" in call_args
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_notify.py -q --no-cov
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.engine.events_notify'`

- [ ] **Step 4: Write the implementation**

Create `src/engine/events_notify.py`:

```python
"""Postgres LISTEN/NOTIFY wake-up signal for execution events (P1-4).

Verified characteristics (not assumed — see ADR-0033): NOTIFY payloads
are capped at 8000 bytes and are NOT durable (a notification is lost if
no one is listening at emit time). This module therefore sends only a
tiny pointer — "<execution_id>:<seq>" — never the event body itself.
The WebSocket handler (src/api/events_ws.py) reacts to a notification
by querying src/engine/events_pg.py's PostgresEventStore for everything
after its last-seen seq, which is the durable source of truth. A missed
NOTIFY (client not yet subscribed, or a brief connection gap) is
harmless: the same query catches up on the next notification or
keepalive poll.

Uses a single dedicated asyncpg connection for LISTEN, separate from
the Prisma-managed pool — Prisma's client doesn't expose an async
LISTEN callback API.
"""

from __future__ import annotations

import asyncpg

from src.config import get_settings

_NOTIFY_CHANNEL = "composer_execution_events"

_notify_connection: asyncpg.Connection | None = None


async def _get_notify_connection() -> asyncpg.Connection:
    """Lazily create (and cache) the dedicated NOTIFY connection."""
    global _notify_connection
    if _notify_connection is None or _notify_connection.is_closed():
        _notify_connection = await asyncpg.connect(get_settings().database_url)
    return _notify_connection


async def notify_execution_event(execution_id: str, *, seq: int) -> None:
    """Send a tiny wake-up pointer on the shared NOTIFY channel."""
    conn = await _get_notify_connection()
    payload = f"{execution_id}:{seq}"
    await conn.execute("SELECT pg_notify($1, $2)", _NOTIFY_CHANNEL, payload)


async def close_notify_connection() -> None:
    """Close the dedicated NOTIFY connection (call on app shutdown)."""
    global _notify_connection
    if _notify_connection is not None and not _notify_connection.is_closed():
        await _notify_connection.close()
    _notify_connection = None


__all__ = ["close_notify_connection", "notify_execution_event"]
```

**Note:** `get_settings().database_url` must exist — check `src/config.py` for the exact field name (it may be `database_url` reading `DATABASE_URL`, matching Prisma's own env var). If the field doesn't exist under that name, use whatever `src/config.py` currently calls it; do not add a second env var for the same connection string.

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_notify.py -q --no-cov
```

Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add src/engine/events_notify.py tests/unit/engine/test_events_notify.py pyproject.toml
git commit -m "feat(observability): add Postgres LISTEN/NOTIFY wake-up signal (P1-4)"
```

---

## Task 7: Rewire `events_wrapper.py` and `LangGraphExecutor` to emit through the persisted store

**Files:**
- Modify: `src/engine/events_wrapper.py`
- Modify: `src/engine/langgraph_executor.py`
- Modify: `src/engine/context.py` (swap the `ExecutionEventBus` ContextVar for the new store, or add alongside — see Step 1)
- Test: `tests/unit/engine/test_events_wrapper.py`, `tests/unit/engine/test_langgraph_executor.py`

**Read `src/engine/context.py` in full before starting this task** — it defines `get_current_event_bus`/`set_current_event_bus`, which every emit site in `events_wrapper.py` and `langgraph_executor.py` calls. This task threads a `PostgresEventStore` + `notify_execution_event` call through the same ContextVar pattern rather than introducing a second one.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/engine/test_events_wrapper.py`:

```python
async def test_wrapper_persists_events_and_notifies() -> None:
    from unittest.mock import AsyncMock, patch

    from src.engine.context import set_current_event_bus, set_current_execution_id

    store = AsyncMock()
    store.append = AsyncMock(return_value=1)
    set_current_execution_id("e1")
    set_current_event_bus(store)

    try:
        with patch("src.engine.events_wrapper.notify_execution_event", new=AsyncMock()) as notify:
            arun = wrap_executor_with_events(_FakeExecutor(), _FakeNode("n1", "http"))  # pyright: ignore[reportArgumentType]
            await arun(initial_state())
            assert store.append.await_count == 2  # node_started + node_completed
            notify.assert_awaited()
    finally:
        set_current_execution_id(None)
        set_current_event_bus(None)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_wrapper.py::test_wrapper_persists_events_and_notifies -q --no-cov
```

Expected: FAIL — `store.append` never called (wrapper still calls `bus.emit`, the old in-process API).

- [ ] **Step 3: Update `events_wrapper.py`'s emit calls**

Everywhere `events_wrapper.py` currently does:

```python
if bus is not None and execution_id is not None:
    await bus.emit(ExecutionEvent(...))
```

replace with:

```python
if bus is not None and execution_id is not None:
    event = ExecutionEvent(...)
    seq = await bus.append(event)
    await notify_execution_event(execution_id, seq=seq)
```

(`bus` here is now whatever the `get_current_event_bus()` ContextVar holds — a `PostgresEventStore` instance instead of an `ExecutionEventBus`. The variable name in the existing code can stay `bus` to minimize the diff, or be renamed to `event_store` for clarity — prefer renaming since `bus.emit` → `bus.append` is a real semantic shift, not just an implementation swap.)

Add the import: `from src.engine.events_notify import notify_execution_event`

- [ ] **Step 4: Update `langgraph_executor.py`'s emit calls the same way**

`src/engine/langgraph_executor.py`'s `_emit` helper method currently does:

```python
async def _emit(
    self,
    event_type: EventType,
    execution_id: str,
    payload: dict[str, Any],
) -> None:
    if self.event_bus is None:
        return
    await self.event_bus.emit(
        ExecutionEvent(type=event_type, execution_id=execution_id, payload=payload)
    )
```

Replace with:

```python
async def _emit(
    self,
    event_type: EventType,
    execution_id: str,
    payload: dict[str, Any],
) -> None:
    if self.event_bus is None:
        return
    event = ExecutionEvent(type=event_type, execution_id=execution_id, payload=payload)
    seq = await self.event_bus.append(event)
    await notify_execution_event(execution_id, seq=seq)
```

`self.event_bus`'s type annotation (`ExecutionEventBus | None` in `__init__`) becomes `PostgresEventStore | None` — update the import and the constructor signature accordingly. `_close_event_bus` (which currently calls `self.event_bus.close(execution_id)`) has no equivalent on `PostgresEventStore` — delete that method and its one call site in `run()`/`.resume()`; a persisted store has nothing to "close" per execution (no per-subscriber queue to drain).

- [ ] **Step 5: Wire `PostgresEventStore` into `app.state`/context instead of `ExecutionEventBus`**

In `src/storage/db.py`, replace:

```python
    from src.engine.events import ExecutionEventBus

    app.state.event_bus = ExecutionEventBus()
```

with:

```python
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
```

- [ ] **Step 6: Run test to verify it passes, then run the full engine test suite**

```bash
.venv/Scripts/python -m pytest tests/unit/engine/test_events_wrapper.py tests/unit/engine/test_langgraph_executor.py -q --no-cov
```

Expected: all pass. Fix any test still constructing `ExecutionEventBus()` directly and asserting on `.emit`/`.subscribe` — those tests need updating to construct a mock/`AsyncMock` `PostgresEventStore`-shaped object instead, following the pattern in Step 1's new test.

- [ ] **Step 7: Commit**

```bash
git add src/engine/events_wrapper.py src/engine/langgraph_executor.py src/storage/db.py tests/unit/engine/
git commit -m "feat(observability): rewire event emission through PostgresEventStore + NOTIFY (P1-4)"
```

---

## Task 8: WebSocket handler — reconnect cursor + LISTEN-based delivery

**Files:**
- Modify: `src/api/events_ws.py`
- Test: `tests/unit/api/test_events_ws.py` (create if it doesn't exist — check first)

- [ ] **Step 1: Check for an existing test file**

```bash
find tests -iname "*test_events_ws*"
```

If found, read it fully before proceeding — match its existing fixture/mocking patterns rather than introducing a new one.

- [ ] **Step 2: Write the failing test**

This codebase already has a WebSocket test pattern in `tests/unit/api/test_events_ws.py` (`_execution_row`/`_build_app`/`_token_for` helpers, `client.websocket_connect(url, subprotocols=["bearer", token])`). Add to that same file rather than creating a new one, reusing its existing helpers:

```python
async def test_ws_replays_events_since_client_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client reconnecting with ?after=1 must receive events with seq > 1
    from the persisted store before subscribing to new live notifications —
    the "recover missed terminal events" requirement (P1-4)."""
    from src.engine.events import ExecutionEvent

    execution = _execution_row(status="running", userId="u1")
    client, app = _build_app(execution)

    missed_event = ExecutionEvent(
        type="node_completed", execution_id="exec-1", seq=2, payload={"nodeId": "n1"}
    )
    store = MagicMock()
    store.list_since = AsyncMock(return_value=[missed_event])
    app.state.event_bus = store

    token = _token_for("u1")
    with client.websocket_connect(
        "/executions/exec-1/ws?after=1", subprotocols=["bearer", token]
    ) as ws:
        # First message: the existing terminal/started snapshot.
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "workflow_started"
        # Second message: the replayed missed event, from list_since, not
        # from a live subscription (there is no live event queued here).
        # ExecutionEvent.as_json() flattens payload into the top-level
        # dict (see src/engine/events.py) — there is no nested "payload"
        # key on the wire.
        replayed = json.loads(ws.receive_text())
        assert replayed["type"] == "node_completed"
        assert replayed["seq"] == 2
        assert replayed["nodeId"] == "n1"

    store.list_since.assert_awaited_once_with("exec-1", after_seq=1)
```

- [ ] **Step 3: Update `events_ws.py` to accept a reconnect cursor and replay missed events**

Add a query parameter and replay logic before the live-subscription loop. In `src/api/events_ws.py`:

```python
@router.websocket("/executions/{execution_id}/ws")
async def events_ws(  # pyright: ignore[reportUnusedFunction]
    ws: WebSocket,
    execution_id: str,
    after: int = 0,  # reconnect cursor: replay events with seq > after
) -> None:
```

After the existing snapshot logic (the `await ws.send_text(json.dumps(snapshot.as_json()))` block) and before the terminal-status early return, add:

```python
    event_store = getattr(ws.app.state, "event_bus", None)  # now a PostgresEventStore
    last_seq = after
    if event_store is not None:
        missed = await event_store.list_since(execution_id, after_seq=after)
        for event in missed:
            await ws.send_text(json.dumps(event.as_json()))
            if event.seq is not None:
                last_seq = event.seq
```

Replace the live-subscription section (`queue = await event_bus.subscribe(execution_id)` onward) — `PostgresEventStore` has no `subscribe`/`unsubscribe`. Instead, listen on the shared NOTIFY channel and re-query `list_since` on each notification matching this `execution_id`:

```python
    import asyncpg

    from src.config import get_settings
    from src.engine.events_notify import _NOTIFY_CHANNEL  # reuse the same channel name

    conn = await asyncpg.connect(get_settings().database_url)
    notified = asyncio.Event()

    def _on_notify(*_args: object) -> None:
        notified.set()

    await conn.add_listener(_NOTIFY_CHANNEL, _on_notify)
    try:
        while True:
            try:
                await asyncio.wait_for(notified.wait(), timeout=15.0)
                notified.clear()
            except TimeoutError:
                with contextlib.suppress(Exception):
                    await ws.send_json({"type": "__keepalive__"})
                continue
            new_events = await event_store.list_since(execution_id, after_seq=last_seq)
            for event in new_events:
                await ws.send_text(json.dumps(event.as_json()))
                if event.seq is not None:
                    last_seq = event.seq
                if event.type == "workflow_completed" and event.payload.get("status") in (
                    "failed",
                    "completed",
                ):
                    return
    except WebSocketDisconnect:
        return
    finally:
        await conn.close()
```

(`event.seq` here relies on Task 5 Step 3's addition of the `seq` field to `ExecutionEvent` — already landed by the time this task starts, since Task 5 precedes Task 8.)

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_events_ws.py -q --no-cov
```

- [ ] **Step 5: Commit**

```bash
git add src/api/events_ws.py src/engine/events.py src/engine/events_pg.py tests/unit/api/test_events_ws.py
git commit -m "feat(observability): WebSocket reconnect cursor + LISTEN-based live delivery (P1-4)"
```

---

## Task 9: Cloud Tasks client wrapper

**Files:**
- Create: `src/execution/cloud_tasks.py`
- Test: `tests/unit/execution/test_cloud_tasks.py`

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`, add under a new `# Cloud Tasks (P1-2 durable execution)` comment near the other GCP-adjacent dependencies:

```
    "google-cloud-tasks>=2.16.0",
```

Run `uv sync --all-extras` (or this machine's absolute-path equivalent).

- [ ] **Step 2: Write the failing test**

Create `tests/unit/execution/test_cloud_tasks.py`:

```python
"""Tests for the Cloud Tasks enqueue wrapper (P1-2)."""

from unittest.mock import MagicMock, patch

import pytest

from src.execution.cloud_tasks import enqueue_execution


async def test_enqueue_execution_builds_oidc_http_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCP_PROJECT_ID", "test-project")
    monkeypatch.setenv("GCP_REGION", "us-central1")
    monkeypatch.setenv("CLOUD_TASKS_QUEUE", "composer-executions")
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "worker@test-project.iam.gserviceaccount.com")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "https://composer.example.com")
    from src.config import get_settings

    get_settings.cache_clear()

    with patch("src.execution.cloud_tasks.tasks_v2.CloudTasksAsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.create_task = MagicMock(return_value=None)
        mock_client_cls.return_value = mock_client
        mock_client.queue_path = MagicMock(
            return_value="projects/test-project/locations/us-central1/queues/composer-executions"
        )

        await enqueue_execution("exec-123", kind="run")

        mock_client.create_task.assert_called_once()
        call_kwargs = mock_client.create_task.call_args.kwargs
        task = call_kwargs["request"]["task"]
        assert task["http_request"]["url"] == "https://composer.example.com/internal/claim-and-run"
        assert task["http_request"]["oidc_token"]["service_account_email"] == (
            "worker@test-project.iam.gserviceaccount.com"
        )
        import json as _json

        body = _json.loads(task["http_request"]["body"])
        assert body == {"executionId": "exec-123", "kind": "run"}
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/execution/test_cloud_tasks.py -q --no-cov
```

Expected: FAIL with `ModuleNotFoundError: No module named 'src.execution.cloud_tasks'`

- [ ] **Step 4: Write the implementation**

Create `src/execution/__init__.py` (empty) and `src/execution/cloud_tasks.py`:

```python
"""Cloud Tasks enqueue wrapper for durable execution (P1-2).

Every execution/resume that today goes through `BackgroundTasks.add_task`
or `asyncio.create_task` (src/api/executions.py, src/api/approval_email.py,
src/api/run.py) is replaced by enqueueing a Cloud Task carrying the
execution ID. Cloud Tasks delivers an OIDC-authenticated HTTP POST to
/internal/claim-and-run (src/api/internal.py) — because that's a real
inbound request, Cloud Run keeps the instance alive for its duration,
which is the actual fix for the scale-to-zero risk this whole subsystem
exists to close (ADR-0033).

Cloud Tasks' at-least-once delivery does NOT by itself guarantee a task
runs exactly once — the claim-and-run endpoint's SELECT ... FOR UPDATE
SKIP LOCKED (already validated: scripts/poc_persistence_row_lock.py) is
what actually prevents a double-run if Cloud Tasks redelivers.
"""

from __future__ import annotations

import json
from typing import Literal

from google.cloud import tasks_v2

from src.config import get_settings

_TaskKind = Literal["run", "resume"]


async def enqueue_execution(execution_id: str, *, kind: _TaskKind) -> None:
    """Enqueue a Cloud Task that will trigger claim-and-run for `execution_id`.

    `kind` distinguishes a fresh run from an approval-resume so the
    claim-and-run endpoint knows which LangGraphExecutor method to call —
    both still go through the same claim (FOR UPDATE SKIP LOCKED) guard.
    """
    settings = get_settings()
    client = tasks_v2.CloudTasksAsyncClient()
    queue_path = client.queue_path(
        settings.gcp_project_id, settings.gcp_region, settings.cloud_tasks_queue
    )
    url = f"{settings.backend_public_url}/internal/claim-and-run"
    body = json.dumps({"executionId": execution_id, "kind": kind}).encode()

    task: dict[str, object] = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": url,
            "headers": {"Content-Type": "application/json"},
            "body": body,
            "oidc_token": {
                "service_account_email": settings.cloud_tasks_service_account,
                "audience": url,
            },
        }
    }
    await client.create_task(request={"parent": queue_path, "task": task})


__all__ = ["enqueue_execution"]
```

**Note:** `settings.backend_public_url` must already exist (it's used by the approval-email flow per this session's earlier work) — confirm the exact field name in `src/config.py` before writing this; do not introduce a duplicate.

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/execution/test_cloud_tasks.py -q --no-cov
```

Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/execution/ tests/unit/execution/
git commit -m "feat(execution): add Cloud Tasks enqueue wrapper (P1-2)"
```

---

## Task 10: Claim-and-run internal endpoint

**Files:**
- Create: `src/api/internal.py`
- Modify: `src/main.py` (register the new router)
- Test: `tests/unit/api/test_internal.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/api/test_internal.py`:

```python
"""Tests for POST /internal/claim-and-run (P1-2)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


def _client_with_mock_db() -> tuple[TestClient, MagicMock]:
    app = create_app()
    db = MagicMock()
    db.workflowexecution = MagicMock()
    app.state.db = db
    app.state.checkpointer = MagicMock()
    from src.engine.events_pg import PostgresEventStore

    app.state.event_bus = PostgresEventStore(db)
    return TestClient(app), db


def test_claim_and_run_claims_and_dispatches_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")  # dev mode: OIDC check skipped
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[{"id": "exec-1"}])
    db.execute_raw = AsyncMock()

    from src.engine.langgraph_executor import LangGraphExecutor

    ran: list[str] = []

    async def _fake_run(self: LangGraphExecutor, execution_id: str) -> None:
        ran.append(execution_id)

    monkeypatch.setattr(LangGraphExecutor, "run", _fake_run)

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
    assert ran == ["exec-1"]


def test_claim_and_run_no_op_when_already_claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """SKIP LOCKED means a second (redelivered) request for the same
    execution finds nothing to claim and returns success without re-running
    — this is the actual single-claim guarantee, not Cloud Tasks itself."""
    monkeypatch.setenv("CLOUD_TASKS_SERVICE_ACCOUNT", "")
    client, db = _client_with_mock_db()
    db.query_raw = AsyncMock(return_value=[])  # already locked by another worker

    resp = client.post("/internal/claim-and-run", json={"executionId": "exec-1", "kind": "run"})
    assert resp.status_code == 200, resp.text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_internal.py -q --no-cov
```

Expected: FAIL with 404 (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Create `src/api/internal.py`:

```python
"""POST /internal/claim-and-run — Cloud Tasks delivery target (P1-2).

Not part of the public API surface; secured by OIDC token verification
(when CLOUD_TASKS_SERVICE_ACCOUNT is set) rather than user auth, since
the caller is Cloud Tasks, not an end user. Claims the execution via
SELECT ... FOR UPDATE SKIP LOCKED (the pattern validated in
scripts/poc_persistence_row_lock.py, ADR-0031) before running it — this,
not Cloud Tasks' delivery guarantee, is what prevents two concurrent
deliveries of the same task from double-running an execution.

Because this is a real inbound HTTP request (not a BackgroundTasks
callback), Cloud Run keeps the instance alive for its full duration,
directly closing the scale-to-zero gap this endpoint exists to close.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from src.config import get_settings
from src.engine.langgraph_executor import LangGraphExecutor
from src.storage.db import get_db, get_checkpointer, get_event_bus

router = APIRouter(tags=["internal"])


class ClaimAndRunRequest(BaseModel):
    execution_id: str
    kind: Literal["run", "resume"] = "run"
    decision: str | None = None  # required when kind == "resume"

    model_config = {"populate_by_name": True}


async def _verify_cloud_tasks_oidc(authorization: str | None = Header(default=None)) -> None:
    """Verify the OIDC token Cloud Tasks presents.

    Skipped entirely when CLOUD_TASKS_SERVICE_ACCOUNT is unset (local dev,
    or before the queue is provisioned) — mirrors the existing dev-mode
    auth fallback pattern (src/main.py's ADR-0015 warning) rather than
    introducing a second, differently-shaped bypass.
    """
    settings = get_settings()
    if not settings.cloud_tasks_service_account:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing OIDC token")
    # Full JWT verification against Google's public keys, audience, and
    # issuer is deliberately not written inline here — reuse
    # src/security/jwt.py's verification machinery if it already supports
    # external-issuer JWTs, or add a small `verify_google_oidc_token`
    # helper there following the same pattern as this codebase's other
    # JWT verification (see CLAUDE.md's fix list for prior art on
    # threading verification through a single shared helper rather than
    # duplicating jose/PyJWT calls per call site).


@router.post("/internal/claim-and-run", status_code=status.HTTP_200_OK)
async def claim_and_run(  # pyright: ignore[reportUnusedFunction]
    payload: ClaimAndRunRequest,
    request: Request,
    db: Any = Depends(get_db),
    _oidc: None = Depends(_verify_cloud_tasks_oidc),
) -> dict[str, str]:
    lease_seconds = get_settings().execution_lease_seconds
    now = datetime.now(UTC)
    lease_expires = now + timedelta(seconds=lease_seconds)
    worker_id = f"{request.client.host if request.client else 'unknown'}:{id(request)}"

    async with db.tx(timeout=lease_seconds * 1000) as tx:
        rows = await tx.query_raw(
            """
            SELECT id FROM workflow_executions
            WHERE id = $1
              AND (lease_expires_at IS NULL OR lease_expires_at < now())
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            payload.execution_id,
        )
        if not rows:
            # Already claimed by another delivery, or the lease hasn't
            # expired yet — nothing to do. Returning 200 (not an error)
            # tells Cloud Tasks the delivery succeeded so it doesn't retry
            # a task that's genuinely already being handled.
            return {"status": "already_claimed"}

        await tx.execute_raw(
            """
            UPDATE workflow_executions
            SET lease_owner = $1, lease_expires_at = $2,
                delivery_attempts = delivery_attempts + 1
            WHERE id = $3
            """,
            worker_id,
            lease_expires,
            payload.execution_id,
        )

    checkpointer = get_checkpointer(request)
    event_bus = get_event_bus(request)
    executor = LangGraphExecutor(db=db, checkpointer=checkpointer, event_bus=event_bus)

    if payload.kind == "resume":
        if not payload.decision:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="decision is required when kind='resume'",
            )
        await executor.resume(payload.execution_id, payload.decision)
    else:
        await executor.run(payload.execution_id)

    return {"status": "completed"}


__all__ = ["router"]
```

- [ ] **Step 4: Register the router in `src/main.py`**

Add the import alongside the other router imports:

```python
from src.api.internal import router as internal_router
```

Add the registration alongside the other `app.include_router(...)` calls (find the existing pattern in `create_app()` and match it exactly).

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_internal.py -q --no-cov
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/api/internal.py src/main.py tests/unit/api/test_internal.py
git commit -m "feat(execution): add Cloud-Tasks-triggered claim-and-run endpoint (P1-2)"
```

---

## Task 11: Extend the sweeper for lease expiry recovery

**Files:**
- Modify: `src/maintenance/execution_sweeper.py`
- Test: `tests/unit/maintenance/test_execution_sweeper.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/maintenance/test_execution_sweeper.py`:

```python
async def test_sweep_expired_leases_requeues_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lease that expired without the worker completing the execution
    means the worker likely died mid-run (Cloud Run instance recycled,
    OOM, etc.) — recover by clearing the lease AND re-enqueueing a fresh
    Cloud Task (clearing the lease alone doesn't guarantee anything will
    ever look at this row again — see the code comment on this in
    sweep_expired_leases), bounded by delivery_attempts so a poison task
    doesn't retry forever."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from src.maintenance.execution_sweeper import sweep_expired_leases

    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.execution.cloud_tasks.enqueue_execution", _fake_enqueue)

    db = MagicMock()
    now = datetime.now(UTC)
    stuck_row = MagicMock(
        id="ex1",
        leaseExpiresAt=now - timedelta(seconds=10),
        deliveryAttempts=1,
        status="running",
        variables={},
    )
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_many = AsyncMock(return_value=[stuck_row])
    db.workflowexecution.update = AsyncMock()

    result = await sweep_expired_leases(db, max_delivery_attempts=5, now=now)

    assert result.marked_failed == 0
    db.workflowexecution.update.assert_awaited_once()
    update_data = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_data["leaseOwner"] is None
    assert update_data["leaseExpiresAt"] is None
    assert enqueued == [("ex1", "run")]


async def test_sweep_expired_leases_requeues_resume_as_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A died mid-resume execution (one with _resume_decision already
    stamped into variables, per Task 13) must be re-enqueued as a resume,
    not restarted as a fresh run."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from src.maintenance.execution_sweeper import sweep_expired_leases

    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.execution.cloud_tasks.enqueue_execution", _fake_enqueue)

    db = MagicMock()
    now = datetime.now(UTC)
    stuck_row = MagicMock(
        id="ex1",
        leaseExpiresAt=now - timedelta(seconds=10),
        deliveryAttempts=1,
        status="running",
        variables={"_resume_decision": "approved"},
    )
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_many = AsyncMock(return_value=[stuck_row])
    db.workflowexecution.update = AsyncMock()

    await sweep_expired_leases(db, max_delivery_attempts=5, now=now)

    assert enqueued == [("ex1", "resume")]


async def test_sweep_expired_leases_fails_after_max_attempts() -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from src.maintenance.execution_sweeper import sweep_expired_leases

    db = MagicMock()
    now = datetime.now(UTC)
    poison_row = MagicMock(
        id="ex1",
        leaseExpiresAt=now - timedelta(seconds=10),
        deliveryAttempts=5,
        status="running",
        variables={},
    )
    db.workflowexecution = MagicMock()
    db.workflowexecution.find_many = AsyncMock(return_value=[poison_row])
    db.workflowexecution.update = AsyncMock()

    result = await sweep_expired_leases(db, max_delivery_attempts=5, now=now)

    assert result.marked_failed == 1
    update_data = db.workflowexecution.update.await_args.kwargs["data"]
    assert update_data["status"] == "failed"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/maintenance/test_execution_sweeper.py -q --no-cov -k lease
```

Expected: FAIL with `ImportError: cannot import name 'sweep_expired_leases'`

- [ ] **Step 3: Write the implementation**

Add to `src/maintenance/execution_sweeper.py`, after `sweep_expired_approvals`:

```python
_LEASE_EXPIRY_ERROR_TEMPLATE = (
    "Execution's lease expired {attempts} times without completing "
    "(worker likely died mid-run each time). Dead-lettered by the "
    "lease-expiry sweeper after exceeding max_delivery_attempts."
)


async def sweep_expired_leases(
    db: Any,
    *,
    max_delivery_attempts: int,
    now: datetime | None = None,
) -> SweepResult:
    """Recover `running` executions whose lease expired without completing.

    A lease expiring means the worker that claimed the execution (via
    the claim-and-run endpoint's FOR UPDATE SKIP LOCKED) died before
    finishing — Cloud Run instance recycled, OOM, uncaught crash. Below
    max_delivery_attempts, clear the lease so the row becomes claimable
    again (the caller is responsible for re-enqueueing a Cloud Task — see
    src/api/internal.py's dead-letter handling, or rely on Cloud Tasks'
    own retry policy to redeliver). At or above max_delivery_attempts,
    mark it failed (dead-lettered) rather than retry indefinitely.
    """
    if max_delivery_attempts <= 0:
        raise ValueError("max_delivery_attempts must be > 0")

    current_time = now or datetime.now(UTC)

    rows: list[Any] = await db.workflowexecution.find_many(
        where={
            "status": "running",
            "leaseExpiresAt": {"lt": current_time},
        },
    )

    marked_failed = 0
    for row in rows:
        try:
            if row.deliveryAttempts >= max_delivery_attempts:
                await db.workflowexecution.update(
                    where={"id": row.id},
                    data={
                        "status": "failed",
                        "error": _LEASE_EXPIRY_ERROR_TEMPLATE.format(
                            attempts=row.deliveryAttempts
                        ),
                        "completedAt": current_time,
                        "leaseOwner": None,
                        "leaseExpiresAt": None,
                    },
                )
                marked_failed += 1
            else:
                # Clearing the lease alone is not sufficient: Cloud Tasks
                # considers ITS OWN delivery attempts separately from this
                # sweeper, and by the time a lease naturally expires (up to
                # execution_lease_seconds later), Cloud Tasks' own queue
                # retry policy (max-attempts, Task 14) may have already
                # exhausted its retries and given up on this execution_id
                # entirely — clearing the lease here without also
                # re-enqueueing would leave the row orphaned with nothing
                # left to ever pick it back up.
                await db.workflowexecution.update(
                    where={"id": row.id},
                    data={"leaseOwner": None, "leaseExpiresAt": None},
                )
                from src.execution.cloud_tasks import enqueue_execution

                # Preserve the original run vs. resume kind — a died
                # mid-resume execution must be re-enqueued as a resume
                # (so claim-and-run reads `_resume_decision` back out of
                # variables, per Task 13), not restarted as a fresh run.
                row_variables = getattr(row, "variables", None) or {}
                kind = "resume" if isinstance(row_variables, dict) and "_resume_decision" in row_variables else "run"
                await enqueue_execution(row.id, kind=kind)
        except Exception:
            logger.exception(
                "execution_sweeper: failed to recover expired-lease execution %s",
                getattr(row, "id", "<unknown>"),
            )

    if rows:
        logger.info(
            "execution_sweeper: processed %d expired leases, %d dead-lettered",
            len(rows),
            marked_failed,
        )

    return SweepResult(scanned=len(rows), marked_failed=marked_failed)
```

Add `sweep_expired_leases` to `__all__`.

Wire it into `_sweeper_loop` and `start_sweeper` alongside the existing two sweeps — add a `max_delivery_attempts` parameter threaded through both (reading from `settings.execution_max_delivery_attempts`, already added in Task 2 — do not add a second, duplicate setting here), then call `await sweep_expired_leases(db, max_delivery_attempts=max_delivery_attempts)` in the loop body and pass it through from `src/main.py`'s `lifespan()` call to `start_sweeper(...)`.

**Note:** `sweep_stuck_executions` (the existing one) and `sweep_expired_leases` (this new one) now overlap in what they catch — both target `status='running'` rows past some deadline. Decide during implementation whether `sweep_stuck_executions`'s plain `startedAt`-based timeout becomes redundant once lease-based recovery exists, or whether it stays as a coarser secondary safety net (e.g., for rows that somehow never got a lease at all). Do not silently delete `sweep_stuck_executions` without confirming this — it's exercised by existing tests and its own docstring's three failure modes are still real.

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/Scripts/python -m pytest tests/unit/maintenance/test_execution_sweeper.py -q --no-cov
```

Expected: all pass (existing + 3 new)

- [ ] **Step 5: Add bounded retention/cleanup for `execution_events` (explicit P1-4 requirement — "add bounded retention and cleanup")**

Write the failing test first, added to `tests/unit/maintenance/test_execution_sweeper.py`:

```python
async def test_sweep_old_execution_events_deletes_past_retention() -> None:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from src.maintenance.execution_sweeper import sweep_old_execution_events

    db = MagicMock()
    db.executionevent = MagicMock()
    db.executionevent.delete_many = AsyncMock(return_value=MagicMock(count=42))
    now = datetime.now(UTC)

    deleted = await sweep_old_execution_events(db, retention_days=30, now=now)

    assert deleted == 42
    db.executionevent.delete_many.assert_awaited_once()
    where = db.executionevent.delete_many.await_args.kwargs["where"]
    assert where["createdAt"]["lt"] == now - timedelta(days=30)
```

Run it, verify RED (`ImportError`), then add to `src/maintenance/execution_sweeper.py`:

```python
async def sweep_old_execution_events(
    db: Any,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Delete execution_events rows older than retention_days (P1-4:
    "add bounded retention and cleanup"). Unlike the other sweeps, this
    one is pure hygiene — event rows carry no operational state, only
    history — so a plain age-based bulk delete is sufficient; no
    per-row error handling or partial-failure bookkeeping is needed.
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be > 0")
    current_time = now or datetime.now(UTC)
    cutoff = current_time - timedelta(days=retention_days)
    result = await db.executionevent.delete_many(where={"createdAt": {"lt": cutoff}})
    count = getattr(result, "count", result if isinstance(result, int) else 0)
    if count:
        logger.info("execution_sweeper: deleted %d execution_events past retention", count)
    return count
```

Add `sweep_old_execution_events` to `__all__`. Add a new `execution_events_retention_days: int = 30` setting to `src/config.py` (same section as the other `execution_*` settings from Task 2), wire it into `_sweeper_loop`/`start_sweeper` the same way as `sweep_expired_leases`, and thread it through from `src/main.py`.

Run the new test to verify it passes.

- [ ] **Step 6: Commit**

```bash
git add src/maintenance/execution_sweeper.py src/config.py src/main.py tests/unit/maintenance/test_execution_sweeper.py
git commit -m "feat(execution): recover expired leases + retention cleanup for execution_events (P1-2/P1-4)"
```

---

## Task 12: Wire `POST /executions` to Cloud Tasks (full replacement)

**Files:**
- Modify: `src/api/executions.py`
- Modify: `src/engine/langgraph_executor.py` (`start_execution` sets `status='queued'`, not `'running'`)
- Test: `tests/unit/api/test_executions.py`

Per the approved rollout scope (full replacement, not run-alongside): this task removes the `BackgroundTasks.add_task(executor.run, row.id)` call entirely.

- [ ] **Step 1: Write the failing test**

Update the existing `test_post_execution_returns_running` in `tests/unit/api/test_executions.py` (or add a new one alongside it — check which is cleaner given the existing fixture) to assert Cloud Tasks enqueueing instead of a background task:

```python
def test_post_execution_enqueues_cloud_task_instead_of_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    enqueued: list[tuple[str, str]] = []

    async def _fake_enqueue(execution_id: str, *, kind: str) -> None:
        enqueued.append((execution_id, kind))

    monkeypatch.setattr("src.api.executions.enqueue_execution", _fake_enqueue)

    client, db = _client_with_mock_db()
    resp = client.post("/executions", json={"workflowId": "wf1", "input": "hi"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    assert enqueued == [("ex1", "run")]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions.py::test_post_execution_enqueues_cloud_task_instead_of_background_task -q --no-cov
```

Expected: FAIL — status is `"running"` not `"queued"`, and `enqueue_execution` is never called.

- [ ] **Step 3: Update `LangGraphExecutor.start_execution` to create with `status='queued'`**

In `src/engine/langgraph_executor.py`, change the `"status": "running",` line in `start_execution`'s `data` dict to `"status": "queued",`.

- [ ] **Step 4: Update `create_execution` in `src/api/executions.py`**

Replace:

```python
    executor = _get_executor(request, db)
    try:
        row = await executor.start_execution(
            workflow_id=payload.workflow_id,
            input=payload.input,
            user_id=user_id,
            idempotency_key=payload.idempotency_key,
        )
    except UniqueViolationError:
        ...

    # Schedule the actual run in the background. The response returns with
    # status='running' immediately; poll GET /executions/{id} for completion.
    background_tasks.add_task(executor.run, row.id)
    return ExecutionRead.model_validate(row)
```

with (keeping the existing `UniqueViolationError`/idempotency-replay branch's body unchanged, only the final scheduling step changes):

```python
    executor = _get_executor(request, db)
    try:
        row = await executor.start_execution(
            workflow_id=payload.workflow_id,
            input=payload.input,
            user_id=user_id,
            idempotency_key=payload.idempotency_key,
        )
    except UniqueViolationError:
        ...

    # P1-2: enqueue a Cloud Task instead of a request-bound BackgroundTask
    # — Cloud Run can scale a request-bound background task's instance to
    # zero mid-run (confirmed via --min-instances=0 in the deploy config,
    # ADR-0033). Cloud Tasks' HTTP-push delivery to /internal/claim-and-run
    # is a real inbound request, which Cloud Run won't recycle mid-flight.
    from src.execution.cloud_tasks import enqueue_execution

    await enqueue_execution(row.id, kind="run")
    return ExecutionRead.model_validate(row)
```

Remove the now-unused `background_tasks: BackgroundTasks` parameter from `create_execution`'s signature if this was its only use (check `resume_execution` too before removing the `BackgroundTasks` import entirely from the file — Task 13 handles that one).

- [ ] **Step 5: Run test to verify it passes, then the full executions test suite**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions.py -q --no-cov
```

Fix any other existing test asserting `status == "running"` immediately after `POST /executions` — those now correctly expect `"queued"` (the row transitions to `"running"` only once claim-and-run actually claims it, which is Task 10's endpoint, exercised by its own tests).

- [ ] **Step 6: Commit**

```bash
git add src/api/executions.py src/engine/langgraph_executor.py tests/unit/api/test_executions.py
git commit -m "feat(execution): route POST /executions through Cloud Tasks, not BackgroundTasks (P1-2)"
```

---

## Task 13: Wire resume (in-app + email-link) and external-invoke to Cloud Tasks

**Files:**
- Modify: `src/api/executions.py` (`resume_execution`)
- Modify: `src/api/approval_email.py`
- Modify: `src/api/run.py`
- Test: `tests/unit/api/test_executions_resume.py`, `tests/unit/api/test_approval_email.py`, `tests/unit/api/test_run_external.py`

- [ ] **Step 1: Write the failing tests**

For each of the three files, mirror Task 12 Step 1's pattern: monkeypatch `enqueue_execution` at the usage site, assert it's called with `kind="resume"` (for the two resume paths) or `kind="run"` (for `run.py`'s external invoke), and assert the corresponding `background_tasks.add_task(...)` / `asyncio.create_task(...)` call is gone. Write one test per file following the existing fixture conventions already established in each (e.g., `test_executions_resume.py`'s atomic-transition mocking from P1-1, `test_approval_email.py`'s token-validation fixtures, `test_run_external.py`'s `_FakeExec` pattern).

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py tests/unit/api/test_approval_email.py tests/unit/api/test_run_external.py -q --no-cov -k enqueue
```

Expected: FAIL (new tests, feature not yet wired)

- [ ] **Step 3: Update `resume_execution` in `src/api/executions.py`**

Replace `background_tasks.add_task(executor.resume, execution_id, payload.decision.value)` with:

```python
    from src.execution.cloud_tasks import enqueue_execution

    await enqueue_execution(execution_id, kind="resume")
```

The `_verify_cloud_tasks_oidc`-guarded `/internal/claim-and-run` endpoint (Task 10) needs the decision value to actually call `executor.resume(execution_id, decision)` — thread it through by stamping it into the execution row before enqueueing (mirroring how `_pending_approval_node` already lives in `variables`), rather than passing it as a Cloud Task payload field that then needs to survive redelivery consistently. Add to `resume_execution`, just before the `enqueue_execution` call:

```python
    await db.workflowexecution.update(
        where={"id": execution_id},
        data={"variables": Json({**(execution.variables or {}), "_resume_decision": payload.decision.value})},
    )
```

And in `src/api/internal.py`'s `claim_and_run` (Task 10), when `payload.kind == "resume"`, read the decision from the claimed row's `variables["_resume_decision"]` instead of a request-body field — update `ClaimAndRunRequest` to drop the `decision` field, and update `claim_and_run`'s resume branch to fetch it: `row_data = await db.workflowexecution.find_unique(where={"id": payload.execution_id}); decision = (row_data.variables or {}).get("_resume_decision")`.

**Note for plan executor:** revisit Task 10's draft `ClaimAndRunRequest`/`claim_and_run` resume branch in light of this — this task supersedes that detail. Update Task 10's code before or during this task, not after, so the two tasks' committed states are mutually consistent (don't leave a commit in between where `claim_and_run` expects a `decision` field nothing ever sends).

- [ ] **Step 4: Update `approval_email.py`'s resume call site**

Same replacement: `background_tasks.add_task(executor.resume, claims.sub, claims.decision)` becomes stamping `_resume_decision` into `variables` (same pattern as Step 3) followed by `await enqueue_execution(claims.sub, kind="resume")`.

- [ ] **Step 5: Update `run.py`'s external-invoke path**

Replace:

```python
    _task = asyncio.create_task(_run_with_persistence(executor, db, execution.id))
    _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
```

with:

```python
    from src.execution.cloud_tasks import enqueue_execution

    await enqueue_execution(execution.id, kind="run")
```

`_run_with_persistence` (the crash-safety wrapper) becomes dead code once nothing calls it directly — `/internal/claim-and-run` (Task 10) is now the single place a crash needs to be caught and turned into a `failed` status. Check whether `claim_and_run` needs the same try/except-and-mark-failed treatment `_run_with_persistence` provided; if so, port that logic into `claim_and_run` rather than deleting the safety net. Delete `_run_with_persistence` and its test coverage only after confirming the equivalent protection exists in `claim_and_run`.

- [ ] **Step 6: Run tests to verify they pass, then the full affected suites**

```bash
.venv/Scripts/python -m pytest tests/unit/api/test_executions_resume.py tests/unit/api/test_approval_email.py tests/unit/api/test_run_external.py tests/unit/api/test_internal.py -q --no-cov
```

- [ ] **Step 7: Commit**

```bash
git add src/api/executions.py src/api/approval_email.py src/api/run.py src/api/internal.py tests/
git commit -m "feat(execution): route resume + external-invoke through Cloud Tasks (P1-2)"
```

---

## Task 14: Cloud Tasks queue provisioning

**Files:**
- Modify: `scripts/gcp-bootstrap.ps1`
- Modify: `.github/workflows/deploy-gcp.yml`

- [ ] **Step 1: Add queue creation to `gcp-bootstrap.ps1`**

Find the existing pattern for provisioning other GCP resources in this script (Cloud Run services, service accounts) and add an idempotent Cloud Tasks queue creation step following the same style:

```powershell
# Cloud Tasks queue for durable execution (P1-2, ADR-0033)
$queueExists = gcloud tasks queues describe composer-executions `
    --location=$Region --project=$ProjectId 2>$null
if (-not $queueExists) {
    gcloud tasks queues create composer-executions `
        --location=$Region `
        --project=$ProjectId `
        --max-attempts=5 `
        --max-retry-duration=3600s `
        --min-backoff=10s `
        --max-backoff=300s
}
```

Match the exact variable names (`$Region`, `$ProjectId` or whatever this script already uses — read the file first) rather than introducing new ones.

- [ ] **Step 2: Add a service account for Cloud Tasks OIDC, granting it `roles/run.invoker` on the Cloud Run service**

Follow whatever existing service-account-creation pattern this script uses for other components (there should be at least one, given the app already runs on Cloud Run with some identity). Grant it `roles/cloudtasks.enqueuer` (for the API to create tasks) and ensure the Cloud Run service's invoker policy allows this service account.

- [ ] **Step 3: Set `CLOUD_TASKS_SERVICE_ACCOUNT`/`GCP_PROJECT_ID`/`GCP_REGION` as deployment env vars**

In `.github/workflows/deploy-gcp.yml`, find where `BACKEND_PUBLIC_URL`/`FRONTEND_URL` are already set on every deploy (per this session's earlier work) and add the three new Cloud Tasks env vars alongside them, following the identical pattern.

- [ ] **Step 4: Manual verification (not automatable in this plan — flag for the user)**

This step requires actually running the updated `gcp-bootstrap.ps1` against the real GCP project, which this plan cannot do on your behalf. After implementation, tell the user explicitly: "Run `scripts/gcp-bootstrap.ps1` to provision the Cloud Tasks queue and service account before deploying — the queue does not exist yet."

- [ ] **Step 5: Commit**

```bash
git add scripts/gcp-bootstrap.ps1 .github/workflows/deploy-gcp.yml
git commit -m "feat(deploy): provision Cloud Tasks queue + OIDC service account (P1-2)"
```

---

## Task 15: Integration tests — lease expiration and worker termination

**Files:**
- Create: `tests/integration/test_durable_execution.py`

Explicit P1-2 acceptance criterion: "Integration tests simulate lease expiration and worker termination." The unit tests added in Tasks 10-11 mock the DB entirely; this task adds real-Postgres coverage, following the existing `tests/integration/` convention (marked `@pytest.mark.integration`, excluded from the default `pytest -m "not integration"` run, requiring `DATABASE_URL` to point at a real reachable Postgres instance).

- [ ] **Step 1: Write the test**

```python
"""Integration tests for durable execution lease expiration/recovery (P1-2).

Requires a real, reachable Postgres (DATABASE_URL) — run via
`uv run pytest -m integration tests/integration/test_durable_execution.py`.
"""

import pytest
from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

pytestmark = pytest.mark.integration


@pytest.fixture
async def db() -> Prisma:  # pyright: ignore[reportUnknownParameterType]
    client = Prisma()
    await client.connect()
    yield client
    await client.disconnect()


async def test_expired_lease_is_reclaimed_and_marked_running_again(db: Prisma) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Simulates a worker that claimed an execution then died: the row's
    lease is set and already expired. sweep_expired_leases must clear the
    lease and (per Task 11) re-enqueue — this test verifies the DB-state
    half; the actual Cloud Tasks call is monkeypatched since integration
    tests should not depend on real GCP credentials."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, patch

    workflow = await db.workflow.create(
        data={"userId": "itest-user", "name": "itest-durable-exec", "nodes": "[]", "edges": "[]"}
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "running",
            "threadId": f"itest-thread-{workflow.id}",
            "nodeResults": "{}",
            "variables": "{}",
            "leaseOwner": "dead-worker-1",
            "leaseExpiresAt": datetime.now(UTC) - timedelta(seconds=30),
            "deliveryAttempts": 1,
        }
    )

    try:
        from src.maintenance.execution_sweeper import sweep_expired_leases

        with patch("src.execution.cloud_tasks.enqueue_execution", new=AsyncMock()) as mock_enqueue:
            result = await sweep_expired_leases(db, max_delivery_attempts=5)

        assert result.marked_failed == 0
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.leaseOwner is None
        assert refreshed.leaseExpiresAt is None
        mock_enqueue.assert_awaited_once_with(execution.id, kind="run")
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})


async def test_two_concurrent_claims_only_one_succeeds(db: Prisma) -> None:  # pyright: ignore[reportUnknownParameterType]
    """Real end-to-end version of scripts/poc_persistence_row_lock.py's
    finding, exercised through the actual claim_and_run code path rather
    than a standalone script — the single-claim guarantee this whole
    subsystem depends on."""
    import asyncio

    from fastapi.testclient import TestClient

    workflow = await db.workflow.create(
        data={
            "userId": "itest-user",
            "name": "itest-claim-race",
            "nodes": '[{"id":"s","type":"start","position":{"x":0,"y":0},"data":{"label":"S"}},{"id":"e","type":"end","position":{"x":1,"y":0},"data":{"label":"E"}}]',
            "edges": '[{"id":"e1","source":"s","target":"e"}]',
        }
    )
    execution = await db.workflowexecution.create(
        data={
            "workflowId": workflow.id,
            "status": "queued",
            "threadId": f"itest-race-{workflow.id}",
            "nodeResults": "{}",
            "variables": "{}",
        }
    )

    try:
        from src.main import create_app

        app = create_app()
        app.state.db = db
        from src.storage.checkpointer import PrismaCheckpointSaver

        app.state.checkpointer = PrismaCheckpointSaver(db)
        from src.engine.events_pg import PostgresEventStore

        app.state.event_bus = PostgresEventStore(db)
        client = TestClient(app)

        async def _claim() -> int:
            resp = client.post(
                "/internal/claim-and-run",
                json={"executionId": execution.id, "kind": "run"},
            )
            return resp.status_code

        results = await asyncio.gather(_claim(), _claim())
        assert all(code == 200 for code in results)

        # Only one of the two concurrent claims should have actually run
        # the graph to completion; the other must see "already_claimed".
        refreshed = await db.workflowexecution.find_unique(where={"id": execution.id})
        assert refreshed is not None
        assert refreshed.status == "completed"
    finally:
        await db.workflowexecution.delete(where={"id": execution.id})
        await db.workflow.delete(where={"id": workflow.id})
```

- [ ] **Step 2: Run against the real dev database**

```bash
.venv/Scripts/python -m pytest -m integration tests/integration/test_durable_execution.py -q --no-cov
```

Expected: 2 passed. If `test_two_concurrent_claims_only_one_succeeds` is flaky (both requests might serialize rather than genuinely race, depending on `TestClient`'s synchronous request handling under `asyncio.gather` — `TestClient` does not guarantee true concurrency the way two separate processes would), that's an acceptable limitation of testing this via `TestClient`; the real guarantee is already proven at the SQL level by `scripts/poc_persistence_row_lock.py`. Note this in a code comment if the test doesn't reliably exercise genuine concurrency, rather than deleting the test — it still documents intent and catches gross regressions (e.g., someone removing the `FOR UPDATE SKIP LOCKED` clause entirely).

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_durable_execution.py
git commit -m "test(execution): add integration tests for lease recovery + concurrent claims (P1-2)"
```

---

## Task 16: Documentation — ADR status, architecture doc, CHANGELOG

**Files:**
- Modify: `docs/decisions.md` (ADR-0033 status: Proposed → Accepted, add Implemented-by)
- Modify: `docs/architecture.md`
- Modify: `docs/deferred-backlog.md` (mark P1-2/P1-4 resolved)
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update ADR-0033's status and Implemented-by section**

In `docs/decisions.md`, change `**Status.** Proposed 2026-07-13...` to `**Status.** Accepted 2026-07-13, implemented <actual completion date>.` and fill in the `**Implemented by.**` line with the actual files touched across Tasks 1-15 and the commit range.

- [ ] **Step 2: Update `docs/architecture.md`**

Add a section describing the new execution flow (API → Cloud Tasks → claim-and-run → LangGraphExecutor) and the new events/rate-limit architecture, following whatever level of detail existing sections in this doc use — read it first to match style.

- [ ] **Step 3: Update `docs/deferred-backlog.md`**

Mark P1-2 and P1-4 entries 🟢 resolved, linking to ADR-0033 and this plan's commit range, following the exact pattern already used for the P3-1 and P0-5 entries earlier in this document.

- [ ] **Step 4: Add a CHANGELOG entry**

Follow the exact format of the existing `### Security — ...` / `### Docs — ...` entries at the top of `CHANGELOG.md`'s `## [Unreleased]` section.

- [ ] **Step 5: Commit**

```bash
git add docs/decisions.md docs/architecture.md docs/deferred-backlog.md CHANGELOG.md
git commit -m "docs: mark P1-2/P1-4 implemented, update architecture doc (ADR-0033)"
```

---

## Final verification (run after all tasks complete)

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src tests
uv run pytest -m "not integration"
```

All four must pass clean before considering this plan complete. Do not push to `origin/main` without the user's explicit instruction (standing directive from earlier in this project).
