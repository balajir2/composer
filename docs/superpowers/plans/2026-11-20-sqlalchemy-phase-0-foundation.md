# SQLAlchemy Migration, Phase 0 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SQLAlchemy 2.x (async, psycopg 3) and Alembic alongside Prisma, with models that exactly match the existing database, without changing any application behaviour.

**Architecture:** A new `src/db/` package holds the engine, sessions, id generator, a UTC timestamp type and typed models for all 17 existing tables. Alembic (scripts in `src/db/migrations/`) starts from an empty baseline revision on top of Prisma's 20 migrations. A schema-parity test proves the models match the real tables. The app starts the SQLAlchemy engine next to Prisma, but no route uses it yet.

**Tech Stack:** SQLAlchemy 2.1, Alembic 1.20, psycopg 3.3, FastAPI, pytest + pytest-asyncio, pyright strict, ruff.

**Spec:** `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`

---

## Ground rules for this plan

- **This plan authorizes edits** to `pyproject.toml`, `uv.lock`, `alembic.ini`, `.github/workflows/ci.yml`, `.github/workflows/deploy-gcp.yml`, `.github/workflows/live-provider-smoke.yml`, `CLAUDE.md`, `docs/decisions.md` and the ops docs, but **only as described in the tasks**. Do not touch `prisma/` at all.
- Tools on this machine are not on PATH: use `"/c/Users/Balaji Rajan/.local/bin/uv.exe"` for `uv` and `.venv/Scripts/python` for Python. In CI they're plain `uv`/`python`.
- Commits: author is `Balaji Rajan <balajirajan@gmail.com>` (repo-local git config). End each message with `Co-Authored-By: <the model doing the work> <noreply@anthropic.com>`.
- Integration tests need a real Postgres: they run when `TEST_DATABASE_URL` is set, after `prisma migrate deploy` and `alembic upgrade head`. CI does this. Locally, point `TEST_DATABASE_URL` and `DATABASE_URL` at a **Neon test branch** (never production) or a Docker Postgres.
- Before every commit run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src tests`.

## Facts this plan relies on (read from production on 2026-11-20, read-only)

- All timestamp columns are `timestamp(3) without time zone`, written as UTC wall-clock time.
- Defaults the database owns: `CURRENT_TIMESTAMP` (every `created_at`, `started_at`, `assigned_at`), `false`/`true` booleans, `0` (`delivery_attempts`), `''::text` (`checkpoint_ns`), `'Bearer'::text`, `'untested'::text`, `'member'::"UserRole"`.
- Defaults Prisma fills in Python (no database default): every `id` (cuid) and every `updated_at`.
- Unique keys are **unique indexes** (not constraints), named `<table>_<cols>_key`; plain indexes are `<table>_<cols>_idx`. `users.email` and `mcp_oauth_states.state` have both.
- 4 foreign keys, all `ON UPDATE CASCADE ON DELETE CASCADE`: `api_keys.user_id → users`, `approvals.execution_id → workflow_executions`, `workflow_assignments.workflow_id → workflows`, `workflow_executions.workflow_id → workflows`.
- Postgres enum types `"UserRole"` (`admin`, `member`) and `"ApprovalDecision"` (`approved`, `rejected`).
- Production is Postgres 17; CI's service container is Postgres 15.

## File map

| File | Responsibility |
|---|---|
| `src/db/__init__.py` | Package marker |
| `src/db/ids.py` | `new_id()`: UUIDv7 as text |
| `src/db/types.py` | `UTCDateTime` column type, `utcnow()` |
| `src/db/url.py` | `to_sqlalchemy_url()`: Prisma-style URL → SQLAlchemy psycopg URL |
| `src/db/engine.py` | `build_engine()`, `connect_with_retry()` |
| `src/db/session.py` | `build_sessionmaker()`, `get_session()` FastAPI dependency |
| `src/db/lifespan.py` | `sqlalchemy_lifespan()`: start and stop the engine with the app |
| `src/db/schema_filters.py` | `include_name()`: tables Alembic must ignore |
| `src/db/models/__init__.py` | Imports every model; exports `Base` |
| `src/db/models/base.py` | `Base` and shared column helpers |
| `src/db/models/enums.py` | `UserRole`, `ApprovalDecision` and their Postgres enum types |
| `src/db/models/workflows.py` | `Workflow`, `WorkflowAssignment` |
| `src/db/models/executions.py` | `WorkflowExecution`, `Approval`, `ExecutionEvent` |
| `src/db/models/checkpoints.py` | `LangGraphCheckpoint`, `LangGraphCheckpointWrite` |
| `src/db/models/mcp.py` | `McpServer`, `McpOAuthToken`, `McpOAuthState` |
| `src/db/models/cloud_storage.py` | `CloudStorageConnection` |
| `src/db/models/users.py` | `User`, `ApiKey` |
| `src/db/models/llm.py` | `LlmApiKey`, `LlmModel` |
| `src/db/models/deployment.py` | `DeploymentSetting` |
| `src/db/models/rate_limits.py` | `RateLimitBucket` |
| `alembic.ini` | Alembic config (repo root) |
| `src/db/migrations/env.py`, `script.py.mako`, `versions/0001_baseline.py` | Alembic environment, template and baseline |
| `tests/unit/db/…` | Pure unit tests (no database) |
| `tests/integration/db/…` | Real-Postgres tests: parity, round-trip, engine, session |
| Modify `src/config.py` | Add `db_pool_size`, `db_max_overflow` |
| Modify `src/main.py` | Start `sqlalchemy_lifespan` next to `prisma_lifespan` |
| Modify CI and deploy workflows | Run `alembic upgrade head` after `prisma migrate deploy` |
| Modify `CLAUDE.md`, `docs/decisions.md`, ops docs, `CHANGELOG.md` | Record the change |

---

### Task 1: Add the dependencies

**Files:**
- Modify: `pyproject.toml` (the `dependencies` list, after the `"prisma>=0.15.0",` line)
- Modify: `uv.lock` (regenerated)

- [ ] **Step 0: Create the implementation branch**

Start from `docs/sqlalchemy-migration-design`, which holds the spec and this plan, so the PR carries them:

```bash
git fetch origin
git worktree add .worktrees/sqla-phase-0 -b feat/sqlalchemy-phase-0 docs/sqlalchemy-migration-design
cd .worktrees/sqla-phase-0
git merge --no-edit origin/main   # pick up anything merged since the design
```

- [ ] **Step 1: Add the three packages**

In `pyproject.toml`, directly after the line `    "prisma>=0.15.0",`, insert:

```toml
    # SQLAlchemy replaces Prisma area by area; see
    # docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md
    "sqlalchemy[asyncio]>=2.1.9",
    "alembic>=1.20.1",
    "psycopg[binary]>=3.3.9",
```

(`psycopg-pool` arrives in phase 5 with the official checkpointer. SQLAlchemy uses its own pool.)

- [ ] **Step 2: Lock and install**

Run: `uv lock && uv sync --all-extras`
Expected: lock output lists `sqlalchemy`, `alembic`, `psycopg`, `psycopg-binary` (and `mako`, `greenlet`) as added; sync succeeds.

- [ ] **Step 3: Verify imports**

Run: `uv run python -c "import sqlalchemy, alembic, psycopg; print(sqlalchemy.__version__, alembic.__version__, psycopg.__version__)"`
Expected: `2.1.9 1.20.1 3.3.9` (or newer patch versions).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build(deps): add SQLAlchemy, Alembic and psycopg 3

Prisma Client Python is archived upstream. These are the replacement's
foundations; nothing uses them yet."
```

---

### Task 2: UUIDv7 id generator

**Files:**
- Create: `src/db/__init__.py`
- Create: `src/db/ids.py`
- Test: `tests/unit/db/__init__.py`, `tests/unit/db/test_ids.py`

- [ ] **Step 1: Write the failing test**

Create `src/db/__init__.py` with:

```python
"""Composer's SQLAlchemy database layer (replaces Prisma area by area)."""
```

Create empty `tests/unit/db/__init__.py`, then `tests/unit/db/test_ids.py`:

```python
import time
import uuid

from src.db.ids import new_id


def test_new_id_is_a_version_7_uuid_string() -> None:
    value = new_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert parsed.variant == uuid.RFC_4122
    assert str(parsed) == value


def test_new_id_embeds_the_current_time_in_milliseconds() -> None:
    before = time.time_ns() // 1_000_000
    value = new_id()
    after = time.time_ns() // 1_000_000
    embedded_ms = uuid.UUID(value).int >> 80
    assert before <= embedded_ms <= after


def test_new_ids_are_unique() -> None:
    values = {new_id() for _ in range(10_000)}
    assert len(values) == 10_000


def test_new_ids_sort_by_creation_time_across_milliseconds() -> None:
    first = new_id()
    time.sleep(0.002)
    second = new_id()
    assert first < second
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.ids'`

- [ ] **Step 3: Implement**

Create `src/db/ids.py`:

```python
"""Primary-key generation for rows created through SQLAlchemy.

Prisma generated cuid ids in Python. New rows use UUIDv7 instead: a standard,
time-ordered format (better index locality). Both are stored as text, so old
and new ids coexist in the same columns. On Python 3.15+, replace the body
with the standard library's ``uuid.uuid7()``.
"""

import os
import time
import uuid

_RAND_BITS = 80
_TIMESTAMP_MASK = (1 << 48) - 1


def new_id() -> str:
    """Return a new UUIDv7 (RFC 9562) as its canonical 36-character string."""
    timestamp_ms = time.time_ns() // 1_000_000
    value = ((timestamp_ms & _TIMESTAMP_MASK) << _RAND_BITS) | int.from_bytes(os.urandom(10), "big")
    value = (value & ~(0xF << 76)) | (0x7 << 76)  # version 7
    value = (value & ~(0x3 << 62)) | (0x2 << 62)  # RFC 4122 variant
    return str(uuid.UUID(int=value))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_ids.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/db/__init__.py src/db/ids.py tests/unit/db/__init__.py tests/unit/db/test_ids.py
git commit -m "feat(db): generate UUIDv7 ids for rows created through SQLAlchemy"
```

---

### Task 3: `UTCDateTime` column type

**Files:**
- Create: `src/db/types.py`
- Test: `tests/unit/db/test_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_types.py`:

```python
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy.dialects import postgresql

from src.db.types import UTCDateTime, utcnow

DIALECT = postgresql.dialect()


def test_bind_converts_aware_datetime_to_naive_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    value = datetime(2026, 11, 20, 15, 30, tzinfo=ist)
    bound = UTCDateTime().process_bind_param(value, DIALECT)
    assert bound == datetime(2026, 11, 20, 10, 0)
    assert bound is not None and bound.tzinfo is None


def test_bind_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        UTCDateTime().process_bind_param(datetime(2026, 11, 20, 10, 0), DIALECT)


def test_bind_passes_none_through() -> None:
    assert UTCDateTime().process_bind_param(None, DIALECT) is None


def test_result_marks_stored_value_as_utc() -> None:
    result = UTCDateTime().process_result_value(datetime(2026, 11, 20, 10, 0), DIALECT)
    assert result == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def test_result_passes_none_through() -> None:
    assert UTCDateTime().process_result_value(None, DIALECT) is None


def test_column_is_millisecond_precision_without_time_zone() -> None:
    impl = UTCDateTime().impl
    assert isinstance(impl, postgresql.TIMESTAMP)
    assert impl.precision == 3
    assert impl.timezone is False


def test_utcnow_is_aware_utc() -> None:
    now = utcnow()
    assert now.tzinfo is UTC
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.types'`

- [ ] **Step 3: Implement**

Create `src/db/types.py`:

```python
"""Column types shared by Composer's SQLAlchemy models."""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A ``timestamp(3) without time zone`` column holding UTC wall-clock time.

    Prisma created every timestamp column this way and wrote UTC values, so
    this type keeps rows written by either ORM comparable. Python code only
    ever sees timezone-aware UTC datetimes. A naive datetime is rejected
    instead of being guessed at, because silently misreading one shifts it by
    the server's offset.
    """

    impl = TIMESTAMP
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(precision=3, timezone=False)

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_types.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/db/types.py tests/unit/db/test_types.py
git commit -m "feat(db): add UTCDateTime so both ORMs agree on stored times

Prisma wrote naive UTC into timestamp(3) columns. The new type keeps that
storage format, hands Python aware UTC values, and rejects naive input
that would otherwise be silently shifted."
```

---

### Task 4: Database URL conversion

**Files:**
- Create: `src/db/url.py`
- Test: `tests/unit/db/test_url.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_url.py`:

```python
import pytest

from src.db.url import to_sqlalchemy_url


def test_switches_scheme_to_psycopg() -> None:
    url = to_sqlalchemy_url("postgresql://user:pw@host:5432/db")
    assert url == "postgresql+psycopg://user:pw@host:5432/db"


def test_accepts_short_postgres_scheme() -> None:
    assert to_sqlalchemy_url("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"


def test_keeps_libpq_parameters() -> None:
    url = to_sqlalchemy_url("postgresql://u:p@h/db?sslmode=require&channel_binding=require")
    assert url == "postgresql+psycopg://u:p@h/db?sslmode=require&channel_binding=require"


def test_drops_prisma_only_parameters() -> None:
    url = to_sqlalchemy_url(
        "postgresql://u:p@h/db?schema=public&sslmode=require&pgbouncer=true&connection_limit=5"
    )
    assert url == "postgresql+psycopg://u:p@h/db?sslmode=require"


def test_rejects_non_postgres_urls() -> None:
    with pytest.raises(ValueError, match="Unsupported database URL scheme"):
        to_sqlalchemy_url("mysql://u:p@h/db")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_url.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.url'`

- [ ] **Step 3: Implement**

Create `src/db/url.py`:

```python
"""Turn Composer's DATABASE_URL (written for Prisma) into a SQLAlchemy URL."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters Prisma understands but libpq/psycopg reject.
_PRISMA_ONLY_PARAMS = frozenset(
    {
        "schema",
        "connection_limit",
        "pool_timeout",
        "pgbouncer",
        "socket_timeout",
        "statement_cache_size",
        "sslaccept",
        "sslidentity",
        "sslpassword",
    }
)


def to_sqlalchemy_url(database_url: str) -> str:
    """Return ``database_url`` with the psycopg driver and libpq-only parameters."""
    parts = urlsplit(database_url)
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError(f"Unsupported database URL scheme {parts.scheme!r}")
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in _PRISMA_ONLY_PARAMS
    ]
    return urlunsplit(("postgresql+psycopg", parts.netloc, parts.path, urlencode(query), parts.fragment))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_url.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/db/url.py tests/unit/db/test_url.py
git commit -m "feat(db): derive the psycopg URL from the Prisma-style DATABASE_URL"
```

---

### Task 5: Pool settings

**Files:**
- Modify: `src/config.py` (after the `db_connect_initial_backoff_seconds` field, before `# ─── Auth`)
- Test: `tests/unit/db/test_settings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_settings.py`:

```python
from src.config import Settings


def test_sqlalchemy_pool_defaults_are_small_while_prisma_also_holds_connections() -> None:
    settings = Settings()
    assert settings.db_pool_size == 2
    assert settings.db_max_overflow == 3
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_settings.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'db_pool_size'`

- [ ] **Step 3: Implement**

In `src/config.py`, directly after the `db_connect_initial_backoff_seconds` field, add:

```python
    # SQLAlchemy pool. Kept small while Prisma also holds connections, so the
    # two together stay within Neon's connection limit (migration spec 3.1).
    db_pool_size: int = Field(
        default=2,
        description="Persistent SQLAlchemy connections per process.",
    )
    db_max_overflow: int = Field(
        default=3,
        description="Extra SQLAlchemy connections allowed under load.",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_settings.py tests/unit/test_config.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/unit/db/test_settings.py
git commit -m "feat(config): size the SQLAlchemy pool to coexist with Prisma"
```

---

### Task 6: Engine with fail-fast connect retry

**Files:**
- Create: `src/db/engine.py`
- Test: `tests/unit/db/test_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_engine.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import Settings
from src.db.engine import build_engine, connect_with_retry


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost:5432/db?sslmode=disable",
        "db_connect_max_attempts": 3,
        "db_connect_timeout_seconds": 1.0,
        "db_connect_initial_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return Settings(**base)


async def test_build_engine_uses_psycopg_and_configured_pool() -> None:
    engine = build_engine(_settings(db_pool_size=4, db_max_overflow=1))
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.query["sslmode"] == "disable"
        assert engine.pool.size() == 4  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        await engine.dispose()


class _FakeConnection:
    async def execute(self, _statement: object) -> None:
        return None


class _FlakyEngine:
    """Fails ``failures`` times with OperationalError, then connects."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.attempts = 0

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_FakeConnection]:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OperationalError("SELECT 1", {}, Exception("database is waking up"))
        yield _FakeConnection()


async def test_connect_with_retry_succeeds_after_transient_failures() -> None:
    engine = _FlakyEngine(failures=2)
    await connect_with_retry(cast(AsyncEngine, engine), _settings())
    assert engine.attempts == 3


async def test_connect_with_retry_gives_up_after_max_attempts() -> None:
    engine = _FlakyEngine(failures=10)
    with pytest.raises(OperationalError):
        await connect_with_retry(cast(AsyncEngine, engine), _settings())
    assert engine.attempts == 3
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.engine'`

- [ ] **Step 3: Implement**

Create `src/db/engine.py`:

```python
"""The async SQLAlchemy engine and its startup connect policy."""

import asyncio
import logging
import math

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.config import Settings
from src.db.url import to_sqlalchemy_url

logger = logging.getLogger(__name__)


def build_engine(settings: Settings) -> AsyncEngine:
    """Create (but do not connect) the engine for ``settings.database_url``.

    ``prepare_threshold=None`` turns off server-side prepared statements,
    which Neon's transaction-mode pooler does not support. ``pool_pre_ping``
    checks a pooled connection before reuse, because Neon suspends idle
    compute and drops its connections.
    """
    return create_async_engine(
        to_sqlalchemy_url(settings.database_url),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args={
            "prepare_threshold": None,
            # libpq takes whole seconds and treats anything below 2 as 2.
            "connect_timeout": max(2, math.ceil(settings.db_connect_timeout_seconds)),
        },
    )


async def connect_with_retry(engine: AsyncEngine, settings: Settings) -> None:
    """Prove the database is reachable, retrying briefly for Neon cold starts.

    Mirrors Prisma's startup policy in ``src/storage/db.py``: a few fast
    attempts with doubling backoff, so a genuinely down database fails a
    boot in seconds instead of crash-looping (2026-08 incident).
    """
    max_attempts = settings.db_connect_max_attempts
    backoff = settings.db_connect_initial_backoff_seconds
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            async with asyncio.timeout(settings.db_connect_timeout_seconds):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("sqlalchemy: connected on attempt %d", attempt)
            return
        except (OperationalError, TimeoutError) as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            logger.warning(
                "sqlalchemy: connect attempt %d/%d failed (%s); retrying in %.1fs",
                attempt,
                max_attempts,
                type(exc).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff *= 2
    assert last_exc is not None
    raise last_exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_engine.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/db/engine.py tests/unit/db/test_engine.py
git commit -m "feat(db): async engine on psycopg with Prisma's fail-fast connect policy"
```

---

### Task 7: Sessions and the app lifespan

**Files:**
- Create: `src/db/session.py`
- Create: `src/db/lifespan.py`
- Modify: `src/main.py` (the `async with prisma_lifespan(app) as db:` line in `lifespan`)
- Test: `tests/unit/db/test_session.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/db/test_session.py`:

```python
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import build_sessionmaker, get_session


class _FakeSessionmaker:
    def __init__(self) -> None:
        self.closed = False

    def __call__(self) -> "_FakeSessionmaker":
        return self

    async def __aenter__(self) -> str:
        return "session"

    async def __aexit__(self, *_exc: Any) -> None:
        self.closed = True


def _request(state: SimpleNamespace) -> Request:
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=state)))


async def test_get_session_yields_a_session_and_closes_it() -> None:
    sessionmaker = _FakeSessionmaker()
    generator = get_session(_request(SimpleNamespace(db_sessionmaker=sessionmaker)))
    session = await anext(generator)
    assert session == "session"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    assert sessionmaker.closed is True


async def test_get_session_fails_clearly_when_lifespan_did_not_run() -> None:
    generator = get_session(_request(SimpleNamespace()))
    with pytest.raises(RuntimeError, match="did lifespan run"):
        await anext(generator)


def test_sessions_do_not_expire_objects_on_commit() -> None:
    from src.config import Settings
    from src.db.engine import build_engine

    engine = build_engine(Settings(database_url="postgresql://u:p@localhost/db"))
    sessionmaker = build_sessionmaker(engine)
    assert sessionmaker.kw["expire_on_commit"] is False
    assert sessionmaker.class_ is AsyncSession
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/db/test_session.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.session'`

- [ ] **Step 3: Implement the session module**

Create `src/db/session.py`:

```python
"""Per-request SQLAlchemy sessions."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions keep loaded values after commit, so results stay usable."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "db_sessionmaker", None
    )
    if sessionmaker is None:
        raise RuntimeError("SQLAlchemy sessionmaker not attached to app.state; did lifespan run?")
    async with sessionmaker() as session:
        yield session
```

- [ ] **Step 4: Implement the lifespan**

Create `src/db/lifespan.py`:

```python
"""Start and stop the SQLAlchemy engine with the FastAPI app."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config import get_settings
from src.db.engine import build_engine, connect_with_retry
from src.db.session import build_sessionmaker


@asynccontextmanager
async def sqlalchemy_lifespan(app: FastAPI) -> AsyncGenerator[AsyncEngine, None]:
    """Connect the engine and attach it, plus a sessionmaker, to ``app.state``."""
    settings = get_settings()
    engine = build_engine(settings)
    try:
        await connect_with_retry(engine, settings)
        app.state.db_engine = engine
        app.state.db_sessionmaker = build_sessionmaker(engine)
        yield engine
    finally:
        await engine.dispose()
```

In `src/main.py`, add the import next to the other `src.` imports:

```python
from src.db.lifespan import sqlalchemy_lifespan
```

and change the line in `lifespan`:

```python
    async with prisma_lifespan(app) as db:
```

to:

```python
    async with prisma_lifespan(app) as db, sqlalchemy_lifespan(app):
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/db/test_session.py tests/unit/test_main.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/db/session.py src/db/lifespan.py src/main.py tests/unit/db/test_session.py
git commit -m "feat(db): start the SQLAlchemy engine alongside Prisma

Nothing queries through it yet; starting it now means phase 1 only has to
add repository code, and every integration run exercises the connection."
```

---

### Task 8: Base, enums and column helpers

**Files:**
- Create: `src/db/models/base.py`, `src/db/models/enums.py`

These are exercised by the model tests in Task 10 and the parity test in Task 12. There's no behaviour to test in isolation.

- [ ] **Step 1: Create the base and helpers**

Create `src/db/models/base.py`:

```python
"""Declarative base and column helpers shared by every model.

Models map the tables Prisma created. Names, types, nullability, defaults
and index names must match the database exactly; the schema-parity test
(tests/integration/db/test_schema_parity.py) enforces this. When it fails,
change the model, never the database.
"""

from datetime import datetime

from sqlalchemy import Boolean, MetaData, Text, text
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

from src.db.ids import new_id
from src.db.types import UTCDateTime, utcnow


class Base(DeclarativeBase):
    metadata = MetaData()


def id_column() -> MappedColumn[str]:
    """Text primary key. Prisma rows keep their cuid ids; new rows get UUIDv7."""
    return mapped_column(Text, primary_key=True, default=new_id)


def created_at_column() -> MappedColumn[datetime]:
    """``NOT NULL DEFAULT CURRENT_TIMESTAMP``; Python sets it too, so the value is known after insert."""
    return mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, server_default=text("CURRENT_TIMESTAMP")
    )


def updated_at_column() -> MappedColumn[datetime]:
    """No database default (Prisma set it in code); set on insert and on every update."""
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


def bool_column(default: bool) -> MappedColumn[bool]:
    """``boolean NOT NULL DEFAULT true|false``."""
    return mapped_column(
        Boolean, nullable=False, default=default, server_default=text("true" if default else "false")
    )
```

- [ ] **Step 2: Create the enums**

Create `src/db/models/enums.py`:

```python
"""Postgres enum types created by Prisma, reused as-is (never re-created)."""

import enum

from sqlalchemy.dialects.postgresql import ENUM


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class ApprovalDecision(enum.StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


USER_ROLE = ENUM(UserRole, name="UserRole", create_type=False, values_callable=_enum_values)
APPROVAL_DECISION = ENUM(
    ApprovalDecision, name="ApprovalDecision", create_type=False, values_callable=_enum_values
)
```

- [ ] **Step 3: Type-check**

Run: `uv run pyright src/db`
Expected: `0 errors`

- [ ] **Step 4: Commit**

```bash
git add src/db/models/base.py src/db/models/enums.py
git commit -m "feat(db): declarative base, Prisma's enum types and shared columns"
```

---

### Task 9: Models for all 17 tables

**Files:**
- Create: `src/db/models/workflows.py`, `executions.py`, `checkpoints.py`, `mcp.py`, `cloud_storage.py`, `users.py`, `llm.py`, `deployment.py`, `rate_limits.py`, `__init__.py`

Every column, default and index below comes from the production catalog listed in "Facts". Don't add relationships yet: phases add them, with `lazy="raise"`, when a repository needs them.

- [ ] **Step 1: Workflows**

Create `src/db/models/workflows.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column


class Workflow(Base):
    __tablename__ = "workflows"
    __table_args__ = (
        Index("workflows_user_id_idx", "user_id"),
        Index("workflows_external_slug_idx", "external_slug"),
        Index("workflows_external_slug_key", "external_slug", unique=True),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    difficulty: Mapped[str | None] = mapped_column(Text)
    estimated_time: Mapped[str | None] = mapped_column(Text)
    nodes: Mapped[Any] = mapped_column(JSONB, nullable=False)
    edges: Mapped[Any] = mapped_column(JSONB, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    is_template: Mapped[bool] = bool_column(False)
    is_public: Mapped[bool] = bool_column(False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    original_owner_email: Mapped[str | None] = mapped_column(Text)
    is_production: Mapped[bool] = bool_column(False)
    external_slug: Mapped[str | None] = mapped_column(Text)


class WorkflowAssignment(Base):
    __tablename__ = "workflow_assignments"
    __table_args__ = (
        Index("workflow_assignments_workflow_id_idx", "workflow_id"),
        Index("workflow_assignments_user_id_idx", "user_id"),
        Index("workflow_assignments_workflow_id_user_id_key", "workflow_id", "user_id", unique=True),
    )

    id: Mapped[str] = id_column()
    workflow_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflows.id",
            name="workflow_assignments_workflow_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    user_id: Mapped[str] = mapped_column(Text)
    assigned_by_id: Mapped[str] = mapped_column(Text)
    assigned_at: Mapped[datetime] = created_at_column()
```

- [ ] **Step 2: Executions, approvals, events**

Create `src/db/models/executions.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column
from src.db.models.enums import APPROVAL_DECISION, ApprovalDecision
from src.db.types import UTCDateTime


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        Index("workflow_executions_workflow_id_idx", "workflow_id"),
        Index("workflow_executions_user_id_idx", "user_id"),
        Index("workflow_executions_thread_id_key", "thread_id", unique=True),
        Index(
            "workflow_executions_workflow_id_idempotency_key_key",
            "workflow_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[str] = id_column()
    workflow_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflows.id",
            name="workflow_executions_workflow_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    user_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    current_node_id: Mapped[str | None] = mapped_column(Text)
    node_results: Mapped[Any] = mapped_column(JSONB, nullable=False)
    variables: Mapped[Any] = mapped_column(JSONB, nullable=False)
    input: Mapped[Any | None] = mapped_column(JSONB)
    output: Mapped[Any | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = created_at_column()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    thread_id: Mapped[str] = mapped_column(Text)
    original_owner_email: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    running_since: Mapped[datetime | None] = mapped_column(UTCDateTime())
    failure_code: Mapped[str | None] = mapped_column(Text)
    delivery_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        Index("approvals_execution_id_idx", "execution_id"),
        Index("approvals_approver_user_id_idx", "approver_user_id"),
    )

    id: Mapped[str] = id_column()
    execution_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "workflow_executions.id",
            name="approvals_execution_id_fkey",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
    )
    node_id: Mapped[str] = mapped_column(Text)
    approver_user_id: Mapped[str | None] = mapped_column(Text)
    approver_email: Mapped[str | None] = mapped_column(Text)
    via_email_link: Mapped[bool] = bool_column(False)
    decision: Mapped[ApprovalDecision] = mapped_column(APPROVAL_DECISION, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()


class ExecutionEvent(Base):
    __tablename__ = "execution_events"
    __table_args__ = (
        Index("execution_events_execution_id_idx", "execution_id"),
        Index("execution_events_execution_id_seq_key", "execution_id", "seq", unique=True),
    )

    id: Mapped[str] = id_column()
    execution_id: Mapped[str] = mapped_column(Text)
    seq: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
```

- [ ] **Step 3: Checkpoint tables** (retired in phase 7; mapped so the parity test covers them)

Create `src/db/models/checkpoints.py`:

```python
from datetime import datetime

from sqlalchemy import Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import BYTEA
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, created_at_column


class LangGraphCheckpoint(Base):
    __tablename__ = "langgraph_checkpoints"
    __table_args__ = (Index("langgraph_checkpoints_thread_id_idx", "thread_id"),)

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(Text)
    checkpoint: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    metadata_: Mapped[bytes] = mapped_column("metadata", BYTEA, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class LangGraphCheckpointWrite(Base):
    __tablename__ = "langgraph_checkpoint_writes"
    __table_args__ = (
        Index("langgraph_checkpoint_writes_thread_id_checkpoint_id_idx", "thread_id", "checkpoint_id"),
    )

    thread_id: Mapped[str] = mapped_column(Text, primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(
        Text, primary_key=True, default="", server_default=text("''::text")
    )
    checkpoint_id: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(Text, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    channel: Mapped[str] = mapped_column(Text)
    value: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
```

(`metadata_` because `metadata` is reserved on declarative classes; the column is still named `metadata`.)

- [ ] **Step 4: MCP**

Create `src/db/models/mcp.py`:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        Index("mcp_servers_user_id_idx", "user_id"),
        Index("mcp_servers_is_shared_idx", "is_shared"),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    header_name: Mapped[str | None] = mapped_column(Text)
    oauth_config: Mapped[Any | None] = mapped_column(JSONB)
    tools: Mapped[Any | None] = mapped_column(JSONB)
    connection_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="untested", server_default=text("'untested'::text")
    )
    last_tested: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = bool_column(True)
    is_official: Mapped[bool] = bool_column(False)
    is_shared: Mapped[bool] = bool_column(False)
    headers: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
    original_owner_email: Mapped[str | None] = mapped_column(Text)


class McpOAuthToken(Base):
    __tablename__ = "mcp_oauth_tokens"
    __table_args__ = (
        Index("mcp_oauth_tokens_mcp_server_id_idx", "mcp_server_id"),
        Index("mcp_oauth_tokens_user_id_idx", "user_id"),
        Index("mcp_oauth_tokens_mcp_server_id_user_id_key", "mcp_server_id", "user_id", unique=True),
    )

    id: Mapped[str] = id_column()
    mcp_server_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scope: Mapped[str | None] = mapped_column(Text)
    token_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="Bearer", server_default=text("'Bearer'::text")
    )
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class McpOAuthState(Base):
    __tablename__ = "mcp_oauth_states"
    __table_args__ = (
        Index("mcp_oauth_states_state_idx", "state"),
        Index("mcp_oauth_states_state_key", "state", unique=True),
        Index("mcp_oauth_states_expires_at_idx", "expires_at"),
    )

    id: Mapped[str] = id_column()
    mcp_server_id: Mapped[str] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text)
    code_verifier: Mapped[str] = mapped_column(Text)
    redirect_uri: Mapped[str] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    created_at: Mapped[datetime] = created_at_column()
```

- [ ] **Step 5: Cloud storage, users, LLM, deployment settings, rate limits**

Create `src/db/models/cloud_storage.py`:

```python
from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class CloudStorageConnection(Base):
    __tablename__ = "cloud_storage_connections"
    __table_args__ = (
        Index("cloud_storage_connections_user_id_idx", "user_id"),
        Index(
            "cloud_storage_connections_user_id_provider_account_email_key",
            "user_id",
            "provider",
            "account_email",
            unique=True,
        ),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    account_email: Mapped[str] = mapped_column(Text)
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    scope: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
```

Create `src/db/models/users.py`:

```python
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.models.enums import USER_ROLE, UserRole
from src.db.types import UTCDateTime


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("users_email_idx", "email"),
        Index("users_email_key", "email", unique=True),
    )

    id: Mapped[str] = id_column()
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(
        USER_ROLE, nullable=False, default=UserRole.MEMBER, server_default=text("'member'::\"UserRole\"")
    )
    is_active: Mapped[bool] = bool_column(True)
    must_change_password: Mapped[bool] = bool_column(False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("api_keys_user_id_idx", "user_id"),
        Index("api_keys_key_prefix_idx", "key_prefix"),
        Index("api_keys_key_hash_key", "key_hash", unique=True),
    )

    id: Mapped[str] = id_column()
    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("users.id", name="api_keys_user_id_fkey", ondelete="CASCADE", onupdate="CASCADE"),
    )
    label: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
```

Create `src/db/models/llm.py`:

```python
from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, bool_column, created_at_column, id_column, updated_at_column
from src.db.types import UTCDateTime


class LlmApiKey(Base):
    __tablename__ = "llm_api_keys"
    __table_args__ = (Index("llm_api_keys_provider_key", "provider", unique=True),)

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(Text)
    encrypted_key: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (
        Index("llm_models_provider_enabled_idx", "provider", "enabled"),
        Index("llm_models_provider_model_id_key", "provider", "model_id", unique=True),
    )

    id: Mapped[str] = id_column()
    provider: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = bool_column(True)
    verification_status: Mapped[str | None] = mapped_column(Text)
    verification_message: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
```

Create `src/db/models/deployment.py`:

```python
from datetime import datetime

from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, updated_at_column


class DeploymentSetting(Base):
    __tablename__ = "deployment_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = updated_at_column()
```

Create `src/db/models/rate_limits.py`:

```python
from datetime import datetime

from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION
from sqlalchemy.orm import Mapped, mapped_column

from src.db.models.base import Base, id_column
from src.db.types import UTCDateTime


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        Index("rate_limit_buckets_route_key_client_key_key", "route_key", "client_key", unique=True),
    )

    id: Mapped[str] = id_column()
    route_key: Mapped[str] = mapped_column(Text)
    client_key: Mapped[str] = mapped_column(Text)
    tokens: Mapped[float] = mapped_column(DOUBLE_PRECISION, nullable=False)
    last_refill: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
```

- [ ] **Step 6: Register every model**

Create `src/db/models/__init__.py`:

```python
"""Every model, imported so ``Base.metadata`` describes the whole schema."""

from src.db.models.base import Base
from src.db.models.checkpoints import LangGraphCheckpoint, LangGraphCheckpointWrite
from src.db.models.cloud_storage import CloudStorageConnection
from src.db.models.deployment import DeploymentSetting
from src.db.models.enums import ApprovalDecision, UserRole
from src.db.models.executions import Approval, ExecutionEvent, WorkflowExecution
from src.db.models.llm import LlmApiKey, LlmModel
from src.db.models.mcp import McpOAuthState, McpOAuthToken, McpServer
from src.db.models.rate_limits import RateLimitBucket
from src.db.models.users import ApiKey, User
from src.db.models.workflows import Workflow, WorkflowAssignment

__all__ = [
    "ApiKey",
    "Approval",
    "ApprovalDecision",
    "Base",
    "CloudStorageConnection",
    "DeploymentSetting",
    "ExecutionEvent",
    "LangGraphCheckpoint",
    "LangGraphCheckpointWrite",
    "LlmApiKey",
    "LlmModel",
    "McpOAuthState",
    "McpOAuthToken",
    "McpServer",
    "RateLimitBucket",
    "User",
    "UserRole",
    "Workflow",
    "WorkflowAssignment",
    "WorkflowExecution",
]
```

- [ ] **Step 7: Lint and type-check**

Run: `uv run ruff check src/db && uv run ruff format src/db && uv run pyright src/db`
Expected: no lint errors, `0 errors` from pyright.

- [ ] **Step 8: Commit**

```bash
git add src/db/models
git commit -m "feat(db): map all 17 Prisma tables as SQLAlchemy models

Names, types, defaults and index names are copied from the production
catalog so both ORMs can share the tables during the migration."
```

---

### Task 10: Model metadata unit tests

**Files:**
- Test: `tests/unit/db/test_models_metadata.py`

These catch mapping mistakes without a database. The integration parity test (Task 12) is the final authority.

- [ ] **Step 1: Write the tests**

Create `tests/unit/db/test_models_metadata.py`:

```python
from src.db.models import Base

EXPECTED_TABLES = {
    "api_keys",
    "approvals",
    "cloud_storage_connections",
    "deployment_settings",
    "execution_events",
    "langgraph_checkpoint_writes",
    "langgraph_checkpoints",
    "llm_api_keys",
    "llm_models",
    "mcp_oauth_states",
    "mcp_oauth_tokens",
    "mcp_servers",
    "rate_limit_buckets",
    "users",
    "workflow_assignments",
    "workflow_executions",
    "workflows",
}


def test_every_prisma_table_is_mapped_exactly_once() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_foreign_keys_cascade_like_prisma_created_them() -> None:
    fks = {
        (table.name, fk.parent.name, fk.column.table.name, fk.ondelete, fk.onupdate)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
    }
    assert fks == {
        ("api_keys", "user_id", "users", "CASCADE", "CASCADE"),
        ("approvals", "execution_id", "workflow_executions", "CASCADE", "CASCADE"),
        ("workflow_assignments", "workflow_id", "workflows", "CASCADE", "CASCADE"),
        ("workflow_executions", "workflow_id", "workflows", "CASCADE", "CASCADE"),
    }


def test_unique_keys_are_unique_indexes_named_like_prisma() -> None:
    unique_indexes = {
        index.name
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.unique
    }
    assert unique_indexes == {
        "api_keys_key_hash_key",
        "cloud_storage_connections_user_id_provider_account_email_key",
        "execution_events_execution_id_seq_key",
        "llm_api_keys_provider_key",
        "llm_models_provider_model_id_key",
        "mcp_oauth_states_state_key",
        "mcp_oauth_tokens_mcp_server_id_user_id_key",
        "rate_limit_buckets_route_key_client_key_key",
        "users_email_key",
        "workflow_assignments_workflow_id_user_id_key",
        "workflow_executions_thread_id_key",
        "workflow_executions_workflow_id_idempotency_key_key",
        "workflows_external_slug_key",
    }


def test_enum_types_are_reused_not_created() -> None:
    role = Base.metadata.tables["users"].c.role.type
    decision = Base.metadata.tables["approvals"].c.decision.type
    assert getattr(role, "name", None) == "UserRole"
    assert getattr(decision, "name", None) == "ApprovalDecision"
    assert getattr(role, "create_type", True) is False
    assert getattr(decision, "create_type", True) is False
```

- [ ] **Step 2: Run them**

Run: `uv run pytest tests/unit/db/test_models_metadata.py -v`
Expected: 4 passed. If one fails, fix the model (the expected values come from production).

- [ ] **Step 3: Commit**

```bash
git add tests/unit/db/test_models_metadata.py
git commit -m "test(db): pin table set, cascades, unique indexes and enum reuse"
```

---

### Task 11: Alembic with an empty baseline

**Files:**
- Create: `alembic.ini`
- Create: `src/db/schema_filters.py`
- Create: `src/db/migrations/env.py`, `src/db/migrations/script.py.mako`, `src/db/migrations/versions/0001_baseline.py`
- Test: `tests/unit/db/test_schema_filters.py`

- [ ] **Step 1: Write the failing filter test**

Create `tests/unit/db/test_schema_filters.py`:

```python
from src.db.schema_filters import include_name


def test_prisma_and_alembic_bookkeeping_tables_are_ignored() -> None:
    assert include_name("_prisma_migrations", "table", {}) is False
    assert include_name("alembic_version", "table", {}) is False


def test_application_tables_and_other_objects_are_included() -> None:
    assert include_name("workflows", "table", {}) is True
    assert include_name("workflows_user_id_idx", "index", {"table_name": "workflows"}) is True
```

Run: `uv run pytest tests/unit/db/test_schema_filters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db.schema_filters'`

- [ ] **Step 2: Implement the filter**

Create `src/db/schema_filters.py`:

```python
"""What Alembic compares and migrates."""

from collections.abc import Mapping

# Bookkeeping tables owned by the migration tools themselves.
IGNORED_TABLES = frozenset({"_prisma_migrations", "alembic_version"})


def include_name(name: str | None, type_: str, parent_names: Mapping[str, str | None]) -> bool:
    """Alembic ``include_name`` hook: skip the tools' own bookkeeping tables."""
    if type_ == "table":
        return name not in IGNORED_TABLES
    return True
```

Run: `uv run pytest tests/unit/db/test_schema_filters.py -v`
Expected: 2 passed

- [ ] **Step 3: Create the Alembic config**

Create `alembic.ini` at the repo root:

```ini
# Alembic owns schema changes from 2026-11-20 on (prisma/ is frozen).
# The database URL comes from Composer's settings (DATABASE_URL), not here.
[alembic]
script_location = %(here)s/src/db/migrations
prepend_sys_path = .
path_separator = os
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARNING
handlers = console
qualname =

[logger_sqlalchemy]
level = WARNING
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: Create the environment**

Create `src/db/migrations/env.py`:

```python
"""Alembic environment: runs migrations through Composer's async engine."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from src.config import get_settings
from src.db.engine import build_engine
from src.db.models import Base
from src.db.schema_filters import include_name

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        include_name=include_name,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = build_engine(get_settings())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (SQL script) migrations are not supported; run against a database.")

asyncio.run(_run_online())
```

Create `src/db/migrations/script.py.mako` (the template `alembic revision` uses):

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | tuple[str, ...] | None = ${repr(branch_labels)}
depends_on: str | tuple[str, ...] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

Create `src/db/migrations/versions/0001_baseline.py`:

```python
"""Baseline: the schema Prisma's 20 migrations created. No changes.

On an existing database, upgrading to this revision only records it in
``alembic_version``. Every later schema change is a new revision.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-20
"""

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    """Nothing to do: Prisma already created these tables."""


def downgrade() -> None:
    """Nothing to undo."""
```

- [ ] **Step 5: Check Alembic sees the baseline**

Run: `uv run alembic heads`
Expected: `0001_baseline (head)`

- [ ] **Step 6: Lint and type-check the new files**

Run: `uv run ruff check src/db && uv run ruff format --check src/db && uv run pyright src/db`
Expected: clean. If pyright reports errors only inside `src/db/migrations/env.py` about Alembic's dynamic `context` module, add `# pyright: basic` as the file's first line and note it in the commit message.

- [ ] **Step 7: Commit**

```bash
git add alembic.ini src/db/schema_filters.py src/db/migrations tests/unit/db/test_schema_filters.py
git commit -m "feat(db): adopt Alembic from an empty baseline over Prisma's schema

Alembic takes over schema changes; upgrading an existing database to the
baseline only records the revision, so production needs no migration."
```

---

### Task 12: Real-Postgres tests (parity, round-trip, engine)

**Files:**
- Test: `tests/integration/db/__init__.py`, `tests/integration/db/conftest.py`, `test_schema_parity.py`, `test_round_trip.py`, `test_engine_live.py`

These run in CI after `prisma migrate deploy` + `alembic upgrade head` (Task 13). Locally they need `TEST_DATABASE_URL` set to a migrated test database.

- [ ] **Step 1: Shared fixtures**

Create empty `tests/integration/db/__init__.py`, then `tests/integration/db/conftest.py`:

```python
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.db.url import to_sqlalchemy_url


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(
        to_sqlalchemy_url(os.environ["TEST_DATABASE_URL"]),
        connect_args={"prepare_threshold": None},
    )
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back, so tests leave no rows."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        db_session = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield db_session
        finally:
            await db_session.close()
            await transaction.rollback()
```

- [ ] **Step 2: Schema parity test**

Create `tests/integration/db/test_schema_parity.py`:

```python
from pprint import pformat
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from src.db.models import Base
from src.db.schema_filters import include_name

pytestmark = pytest.mark.integration


def _diff(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection,
        opts={
            "include_name": include_name,
            "compare_type": True,
            "compare_server_default": True,
        },
    )
    return compare_metadata(context, Base.metadata)


async def test_models_match_the_migrated_database_exactly(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == [], "Models differ from the database; fix the model:\n" + pformat(diff)


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def test_database_is_at_the_alembic_head(engine: AsyncEngine) -> None:
    head = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    async with engine.connect() as conn:
        current = await conn.run_sync(_current_revision)
    assert current == head
```

- [ ] **Step 3: Round-trip test for every column type**

Create `tests/integration/db/test_round_trip.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import (
    ApiKey,
    Approval,
    ApprovalDecision,
    CloudStorageConnection,
    DeploymentSetting,
    ExecutionEvent,
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    LlmApiKey,
    LlmModel,
    McpOAuthState,
    McpOAuthToken,
    McpServer,
    RateLimitBucket,
    User,
    UserRole,
    Workflow,
    WorkflowAssignment,
    WorkflowExecution,
)

pytestmark = pytest.mark.integration


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def test_workflow_execution_approval_and_event_round_trip(session: AsyncSession) -> None:
    workflow = Workflow(name="round trip", tags=["a", "b"], nodes=[{"id": "start"}], edges=[])
    session.add(workflow)
    await session.flush()
    execution = WorkflowExecution(
        workflow_id=workflow.id,
        status="queued",
        node_results={"start": {"ok": True}},
        variables={},
        thread_id=_unique("thread"),
    )
    session.add(execution)
    await session.flush()
    session.add_all(
        [
            Approval(execution_id=execution.id, node_id="approve", decision=ApprovalDecision.APPROVED),
            ExecutionEvent(execution_id=execution.id, seq=1, type="started", payload={"n": 1}),
            WorkflowAssignment(workflow_id=workflow.id, user_id="u1", assigned_by_id="u2"),
        ]
    )
    await session.flush()
    session.expunge_all()

    loaded = (await session.execute(select(Workflow).where(Workflow.id == workflow.id))).scalar_one()
    assert loaded.tags == ["a", "b"]
    assert loaded.nodes == [{"id": "start"}]
    assert loaded.is_template is False and loaded.is_production is False
    assert loaded.created_at.tzinfo is UTC and loaded.updated_at.tzinfo is UTC

    run = (
        await session.execute(select(WorkflowExecution).where(WorkflowExecution.id == execution.id))
    ).scalar_one()
    assert run.node_results == {"start": {"ok": True}}
    assert run.delivery_attempts == 0
    assert uuid.UUID(run.id).version == 7

    approval = (await session.execute(select(Approval).where(Approval.execution_id == execution.id))).scalar_one()
    assert approval.decision is ApprovalDecision.APPROVED
    assert approval.via_email_link is False


async def test_users_keys_and_enum_default(session: AsyncSession) -> None:
    user = User(email=f"{_unique('user')}@example.com")
    session.add(user)
    await session.flush()
    session.add(ApiKey(user_id=user.id, label="ci", key_hash=_unique("hash"), key_prefix="ck_"))
    await session.flush()
    session.expunge_all()
    loaded = (await session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert loaded.role is UserRole.MEMBER
    assert loaded.is_active is True and loaded.must_change_password is False


async def test_bytes_text_defaults_and_double_precision(session: AsyncSession) -> None:
    thread_id = _unique("thread")
    now = datetime.now(UTC)
    session.add_all(
        [
            LangGraphCheckpoint(thread_id=thread_id, checkpoint_id="c1", checkpoint=b"\x00\x01", metadata_=b"{}"),
            LangGraphCheckpointWrite(
                thread_id=thread_id, checkpoint_id="c1", task_id="t", idx=0, channel="x", value=b"\xff"
            ),
            McpServer(name="m", url="https://example.com", auth_type="none"),
            McpOAuthToken(mcp_server_id="s", user_id=_unique("u"), encrypted_access_token="enc"),
            McpOAuthState(
                mcp_server_id="s",
                user_id="u",
                state=_unique("state"),
                code_verifier="v",
                redirect_uri="https://example.com/cb",
                expires_at=now + timedelta(minutes=5),
            ),
            CloudStorageConnection(
                user_id=_unique("u"), provider="google-drive", account_email="a@example.com",
                encrypted_access_token="enc",
            ),
            LlmApiKey(provider=_unique("provider"), encrypted_key="enc", key_prefix="sk-"),
            LlmModel(provider="anthropic", model_id=_unique("model")),
            DeploymentSetting(key=_unique("setting"), value="on"),
            RateLimitBucket(route_key="r", client_key=_unique("c"), tokens=2.5, last_refill=now),
        ]
    )
    await session.flush()
    session.expunge_all()

    checkpoint = (
        await session.execute(select(LangGraphCheckpoint).where(LangGraphCheckpoint.thread_id == thread_id))
    ).scalar_one()
    assert checkpoint.checkpoint == b"\x00\x01"
    assert checkpoint.checkpoint_ns == ""
    server = (await session.execute(select(McpServer).where(McpServer.name == "m"))).scalars().first()
    assert server is not None and server.connection_status == "untested" and server.enabled is True
    bucket = (await session.execute(select(RateLimitBucket).where(RateLimitBucket.route_key == "r"))).scalars().first()
    assert bucket is not None and bucket.tokens == 2.5


async def test_timestamps_store_utc_with_millisecond_precision(session: AsyncSession) -> None:
    moment = datetime(2026, 11, 20, 10, 0, 0, 123456, tzinfo=UTC)
    setting = DeploymentSetting(key=_unique("ts"), value="v", updated_at=moment)
    session.add(setting)
    await session.flush()
    session.expunge_all()
    loaded = (
        await session.execute(select(DeploymentSetting).where(DeploymentSetting.key == setting.key))
    ).scalar_one()
    assert loaded.updated_at == datetime(2026, 11, 20, 10, 0, 0, 123000, tzinfo=UTC)


async def test_updated_at_changes_on_update(session: AsyncSession) -> None:
    setting = DeploymentSetting(key=_unique("upd"), value="a", updated_at=datetime(2020, 1, 1, tzinfo=UTC))
    session.add(setting)
    await session.flush()
    setting.value = "b"
    await session.flush()
    assert setting.updated_at > datetime(2026, 1, 1, tzinfo=UTC)
```

- [ ] **Step 4: Live engine test**

Create `tests/integration/db/test_engine_live.py`:

```python
import os

import pytest
from sqlalchemy import text

from src.config import Settings
from src.db.engine import build_engine, connect_with_retry

pytestmark = pytest.mark.integration


async def test_engine_connects_without_server_side_prepared_statements() -> None:
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"])
    engine = build_engine(settings)
    try:
        await connect_with_retry(engine, settings)
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
            raw = await conn.get_raw_connection()
            assert raw.driver_connection is not None
            assert raw.driver_connection.prepare_threshold is None
    finally:
        await engine.dispose()
```

- [ ] **Step 5: Run locally if a test database is available**

Run (with `TEST_DATABASE_URL` and `DATABASE_URL` pointing at a migrated test database):
`uv run alembic upgrade head && uv run pytest tests/integration/db -v`
Expected: 8 passed. Without a test database, they're skipped: `8 skipped (TEST_DATABASE_URL not set)`. CI runs them for real (Task 13).

If the parity test reports differences, change the **model** to match the database and re-run. Known fixes: a reported server-default difference on a column means the model's `server_default` text doesn't match the catalog value in "Facts"; a reported index difference means a name or uniqueness mismatch.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/db
git commit -m "test(db): prove models match the real schema and round-trip every type"
```

---

### Task 13: Run Alembic in CI and in the deploy

**Files:**
- Modify: `.github/workflows/ci.yml` (after both `Apply migrations` steps, lines 67–68 and 342–343)
- Modify: `.github/workflows/live-provider-smoke.yml` (after the `Apply migrations` step, lines 83–84)
- Modify: `.github/workflows/deploy-gcp.yml` (the `Apply database migrations` step)

- [ ] **Step 1: CI and smoke workflows**

In `.github/workflows/ci.yml`, after **each** of the two steps

```yaml
      - name: Apply migrations
        run: uv run prisma migrate deploy
```

insert:

```yaml
      - name: Apply Alembic migrations
        run: uv run alembic upgrade head
```

Make the same insertion in `.github/workflows/live-provider-smoke.yml` after its `Apply migrations` step.

- [ ] **Step 2: Deploy workflow**

In `.github/workflows/deploy-gcp.yml`, in the `Apply database migrations` step, replace:

```yaml
          uv run prisma migrate deploy
          uv run prisma migrate status
```

with:

```yaml
          uv run prisma migrate deploy
          uv run prisma migrate status
          # Alembic owns schema changes from 2026-11-20 (prisma/ is frozen).
          # On the first run this only records the empty baseline revision.
          uv run alembic upgrade head
          uv run alembic current
```

- [ ] **Step 3: Validate the YAML**

Run: `uv run python -c "import yaml; [yaml.safe_load(open(f)) for f in ('.github/workflows/ci.yml', '.github/workflows/deploy-gcp.yml', '.github/workflows/live-provider-smoke.yml')]; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml .github/workflows/live-provider-smoke.yml .github/workflows/deploy-gcp.yml
git commit -m "ci: apply Alembic migrations after Prisma's in CI and before deploy

The deploy applies schema before the new revision starts, so a schema
change made through Alembic can't reach production ahead of its code."
```

---

### Task 14: Guard rails for the transition

**Files:**
- Test: `tests/unit/db/test_prisma_frozen.py`, `tests/unit/db/test_prisma_import_boundary.py`

- [ ] **Step 1: Prisma's schema is frozen**

Create `tests/unit/db/test_prisma_frozen.py`:

```python
"""Prisma's schema and migrations are frozen; schema changes go through Alembic.

If this fails, you changed prisma/. Revert it and write an Alembic revision
instead (uv run alembic revision -m "..."). Only the migration's phase 6 PR
may update this fingerprint, when Prisma is removed.
"""

import hashlib
from pathlib import Path

PRISMA_DIR = Path(__file__).resolve().parents[3] / "prisma"
FROZEN_FINGERPRINT = "c8a3b0849e731c37b11ce406d390a274751b4195a727b4ff9b1d0ca2e0d61dce"


def _fingerprint() -> str:
    files = sorted(
        p for p in [PRISMA_DIR / "schema.prisma", *(PRISMA_DIR / "migrations").rglob("*")] if p.is_file()
    )
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(PRISMA_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    return digest.hexdigest()


def test_prisma_schema_and_migrations_are_unchanged() -> None:
    assert _fingerprint() == FROZEN_FINGERPRINT
```

- [ ] **Step 2: Migrated areas can't import Prisma**

Create `tests/unit/db/test_prisma_import_boundary.py`:

```python
"""Code in migrated areas must not import Prisma.

Each migration phase adds the areas it ported to MIGRATED_PATHS. The list
only grows, until phase 6 removes Prisma entirely.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATED_PATHS = ["src/db"]
PRISMA_IMPORT = re.compile(r"^\s*(from|import)\s+prisma\b", re.MULTILINE)


def test_migrated_areas_do_not_import_prisma() -> None:
    offenders: list[str] = []
    for relative in MIGRATED_PATHS:
        root = REPO_ROOT / relative
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            if PRISMA_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == [], f"Prisma imported in migrated areas: {offenders}"
```

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/unit/db/test_prisma_frozen.py tests/unit/db/test_prisma_import_boundary.py -v`
Expected: 2 passed. (If the fingerprint test fails on an untouched checkout, the `prisma/` folder contains an untracked file: check `git status prisma`.)

- [ ] **Step 4: Commit**

```bash
git add tests/unit/db/test_prisma_frozen.py tests/unit/db/test_prisma_import_boundary.py
git commit -m "test(db): freeze Prisma's schema and keep Prisma out of migrated areas

Feature work continues during the migration; these checks stop it from
changing Prisma's schema or reintroducing Prisma in ported code."
```

---

### Task 15: Documentation

**Files:**
- Modify: `CLAUDE.md` (stack table, development commands)
- Modify: `docs/decisions.md` (append ADR-0035; mark ADR-0001 superseded)
- Modify: `docs/operations/production-deployment.md` (section 1.1a)
- Modify: `CHANGELOG.md` (under `## [Unreleased]`)

- [ ] **Step 1: CLAUDE.md**

In the stack table, replace the row

```
| ORM | **Prisma Python** (user explicitly chose over SQLAlchemy) |
```

with these two rows:

```
| ORM | **SQLAlchemy 2.x (async) + Alembic**, replacing Prisma Python (archived upstream) area by area per `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`. Both run until the migration's phase 6. **Schema changes go through Alembic only; `prisma/` is frozen.** |
| Postgres driver | **psycopg 3** (asyncpg is removed during the migration) |
```

In "Development commands", after the `uv run prisma migrate dev --name <migration_name>` block, add:

```bash
# Schema changes (Prisma's schema is frozen): write an Alembic revision
uv run alembic revision -m "<what changed>"

# Apply Alembic migrations (after `prisma migrate deploy` while both exist)
uv run alembic upgrade head
```

and change the comment above `uv run prisma migrate dev` to `# (Frozen) Prisma migrations: do not create new ones`.

- [ ] **Step 2: ADR-0035**

Append to `docs/decisions.md`:

```markdown
## ADR-0035: Replace Prisma Python with SQLAlchemy 2.x + Alembic on psycopg 3

**Date.** 2026-11-20. **Supersedes** ADR-0001 (custom Prisma checkpointer) once phase 5 ships.

**Context.** Prisma Client Python was archived by its maintainer in March 2025 (no releases since 0.15.0, August 2024; supports Python up to 3.12). It sits under 234 query call sites and ties the backend image to Node.js. Nothing is broken today, but nothing upstream will fix it.

**Decision.** Move to SQLAlchemy 2.x async + Alembic on psycopg 3, mapped onto the existing tables (no data migration), behind repository modules tested against real Postgres. Switch the LangGraph checkpointer to the official `langgraph-checkpoint-postgres`. Migrate in eight PRs with both ORMs live; Alembic owns schema changes from the first PR. Full design: `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`.

**Consequences.** Composer gains a maintained ORM, one database driver, and no Node.js in the backend. For about three weeks two ORMs share the tables, guarded by a schema-parity test, a Prisma-schema freeze and an import boundary. Python 3.15 is unblocked from the ORM side once Prisma is removed.

**Related.** ADR-0001, ADR-0033, ADR-0034.
```

Under the `## ADR-0001` heading, add the line:

```markdown
> **Superseded by ADR-0035** (2026-11-20): the checkpointer moves to `langgraph-checkpoint-postgres` in the SQLAlchemy migration's phase 5.
```

- [ ] **Step 3: Deployment doc**

In `docs/operations/production-deployment.md` section `1.1a`, replace the code block

```bash
uv run prisma generate
uv run prisma migrate deploy
uv run prisma migrate status
```

with:

```bash
uv run prisma generate
uv run prisma migrate deploy
uv run prisma migrate status
uv run alembic upgrade head
uv run alembic current
```

and add after it: `Alembic owns schema changes from 2026-11-20. On a database that has only Prisma's migrations, the first `alembic upgrade head` records the empty baseline revision and changes nothing.`

In `docs/operations/gcp-cloud-run-setup.md`, in the paragraph that begins "Apply `uv run prisma migrate deploy`", change its first sentence to: "Apply `uv run prisma migrate deploy` and then `uv run alembic upgrade head` against the intended database **before** running the deploy script for this release (the GitHub deploy workflow does both automatically)." Also add `docs/operations/gcp-cloud-run-setup.md` to this task's `git add`.

- [ ] **Step 4: CHANGELOG**

Under `## [Unreleased]`, add:

```markdown
### Changed — SQLAlchemy foundation, migration phase 0 of 7 (2026-11-20)

- **Why:** Prisma Client Python, Composer's ORM, is archived upstream. This starts the move to SQLAlchemy 2.x + Alembic on psycopg 3 (ADR-0035).
- **What:** models for all 17 tables, mapped onto the existing schema; an empty Alembic baseline; a schema-parity test; the SQLAlchemy engine starts alongside Prisma. No behaviour change, and no route uses SQLAlchemy yet.
- **Rules from now on:** schema changes are Alembic revisions only (`prisma/` is frozen and a test enforces it). The deploy runs `alembic upgrade head` after `prisma migrate deploy`.
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/decisions.md docs/operations/production-deployment.md docs/operations/gcp-cloud-run-setup.md CHANGELOG.md
git commit -m "docs: record the SQLAlchemy move and the Alembic-only schema rule"
```

---

### Task 16: Full verification and PR

- [ ] **Step 1: Run everything CI runs**

Run:

```bash
uv run ruff check src tests && \
uv run ruff format --check src tests && \
uv run pyright src tests && \
uv run pytest tests/unit -q
```

Expected: all clean; the unit suite passes (about 1,700 tests plus the new `tests/unit/db` ones).

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin feat/sqlalchemy-phase-0
gh pr create --base main --title "feat(db): SQLAlchemy foundation (migration phase 0)" --body-file - <<'EOF'
## Summary

Phase 0 of the move from Prisma Client Python (archived upstream) to SQLAlchemy 2.x + Alembic on psycopg 3. ADR-0035; spec `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`; plan `docs/superpowers/plans/2026-11-20-sqlalchemy-phase-0-foundation.md`.

- Models for all 17 existing tables, matched to the production catalog (names, types, defaults, unique indexes, cascades, enum types)
- `UTCDateTime`, UUIDv7 ids, psycopg engine with Prisma's fail-fast connect policy; engine starts alongside Prisma
- Alembic from an empty baseline; CI and deploy run `alembic upgrade head` after `prisma migrate deploy`
- Guard rails: schema-parity test, Prisma-schema freeze, Prisma import boundary for migrated areas

**No behaviour change:** no route uses SQLAlchemy yet. On first deploy Alembic only records the baseline revision.

## Test plan
- [ ] ruff, ruff format --check, pyright strict, unit suite
- [ ] `tests/integration/db` passes in CI (8 tests, not skipped): parity, head revision, round-trip of every column type, live engine
- [ ] After merge: deploy log shows `Running upgrade  -> 0001_baseline`; `/health` 200; no new backend errors
EOF
```

- [ ] **Step 3: Confirm CI ran the database tests for real**

In the "Lint, typecheck, and test" job log, confirm `Apply Alembic migrations` ran and that `tests/integration/db` reports **8 passed** (not skipped).

- [ ] **Step 4: After merge, confirm the deploy**

In the deploy run's `Apply database migrations` step, expect `Running upgrade  -> 0001_baseline` on the first deploy and `0001_baseline (head)` from `alembic current`. Backend `/health` returns 200, and backend logs show no new errors.

---

## Later phases (each gets its own plan when it starts)

| Phase | Plan scope |
|---|---|
| 1 | Settings and admin: repositories for `deployment_settings`, `llm_api_keys`, `llm_models`, `api_keys`, `users`; port `src/api/admin_*`, `api_keys`, `auth_*` and LLM key sync; API behaviour tests proven on Prisma first; add those paths to `MIGRATED_PATHS` |
| 2 | Integrations: `mcp_servers`, `mcp_oauth_tokens`, `mcp_oauth_states`, `cloud_storage_connections`, including the shared-server token fallback (MCP fix 5) |
| 3 | Workflows: CRUD, assignments, builder saves, `AlreadyExists` for slug conflicts |
| 4 | Executions core: the 15 guarded status updates as named functions with race tests, the claim transaction, approvals, events store, rate limiter |
| 5 | Checkpointer and notifications: add `psycopg-pool` and `langgraph-checkpoint-postgres`; `setup()` in the deploy step; paused-run guard; two-way copy tool; LISTEN/NOTIFY on psycopg |
| 6 | Removal: Prisma, Node.js, asyncpg; squashed Alembic base for fresh databases; update the frozen fingerprint test into a "prisma/ absent" check |
| 7 | Cleanup: Alembic revision dropping the old checkpoint tables and `_prisma_migrations` |

### Carry into the phase 1 plan (from the phase 0 final review)

- Settings must be read after any env override (see `tests/conftest.py`).
- Check whether any Prisma write stores non-JSON-native values (datetime, Decimal, Enum) in Json columns; if so, set the engine's `json_serializer`.
- Compare `timestamp(3)` columns against bound `utcnow()` values, never `func.now()` (it depends on the session TimeZone).
- Relationships use `lazy="raise"` and `passive_deletes=True`.
- Grow `MIGRATED_PATHS` with each ported area.
- Phase 5: add LangGraph's checkpoint tables (`checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`) to `IGNORED_TABLES` in `src/db/schema_filters.py`.
- Consider a `concurrency:` group on the deploy workflow (pre-existing; owner decision).
