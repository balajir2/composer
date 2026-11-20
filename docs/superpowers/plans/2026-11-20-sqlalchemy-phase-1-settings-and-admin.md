# SQLAlchemy Migration Phase 1 — Settings and Admin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every read and write of the five Phase 1 tables (`deployment_settings`, `llm_models`, `llm_api_keys`, `users`, `api_keys`) from Prisma to SQLAlchemy repository modules, with no change in API behaviour.

**Architecture:** Each table gets one module in `src/db/repos/` that is the only code running its queries and returns frozen dataclasses, never ORM objects. Routes take an `AsyncSession` from the existing `get_session` dependency and commit their own writes. Code that runs outside a request (agent executor, startup key sync, auth dependencies, CLI) gets sessions from a small process-wide registry (`src/db/runtime.py`). API-level behaviour tests are written first and must pass on the Prisma code before any porting starts; they then pass unchanged afterwards.

**Tech Stack:** SQLAlchemy 2.x async ORM, psycopg 3, PostgreSQL 15, FastAPI, pytest + pytest-asyncio, pyright strict, ruff.

**Spec:** [`docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md`](../specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md) (§2.1 rules, §3.1 coexistence, §4.3–4.5, §5, §5.1 done criteria). Phase 0 plan and its "Carry into the phase 1 plan" list: [`2026-11-20-sqlalchemy-phase-0-foundation.md`](2026-11-20-sqlalchemy-phase-0-foundation.md).

## Global Constraints

- Only `src/db/repos/` runs queries. Routes and the engine call named repository functions and get plain typed results back (dataclasses), never live ORM objects.
- Callers own transactions. A route opens the session (via `Depends(get_session)`) and calls `await session.commit()` after its writes. Repository functions never commit.
- Guarded status updates are named functions returning `True` if this caller won.
- No hidden queries: relationships use `lazy="raise"` and `passive_deletes=True` (none are needed in this phase).
- An area moves whole: every read and write of a Phase 1 table switches in this PR, including the reads in files that belong to later phases (`events_ws.py`, `mcp_servers.py`, `workflows.py`, `test_cleanup.py`, `migration/reconcile.py`, `executors/agent.py`, `api/builder.py`, `api/admin_builder.py`). No Prisma transaction touches a Phase 1 table (the only `db.tx` is the Phase 4 claim), so no transaction spans both ORMs.
- Unique-constraint violations become `AlreadyExists(constraint=…)` at the repository boundary.
- Timestamps: write `utcnow()` from `src/db/types.py` (aware UTC, whole milliseconds), never `datetime.now(UTC)` or `func.now()`, so the in-memory value equals the stored one.
- `INSERT … ON CONFLICT DO UPDATE` bypasses the ORM's `onupdate`, so every upsert sets `updated_at=utcnow()` explicitly in `set_`.
- Tests: repository tests are `@pytest.mark.integration` and run on real Postgres; route and engine unit tests fake repository functions (`monkeypatch.setattr` on the repo module), not the ORM.
- Routes import repositories as modules (`from src.db.repos import users as users_repo`) and call `users_repo.get_by_id(...)`, so tests can patch the module attribute.
- Do not edit `prisma/` (frozen by `tests/unit/db/test_prisma_frozen.py`), `pyproject.toml`, `uv.lock`, or `.github/workflows/*`. No new dependencies.
- Commits: author Balaji Rajan `<balajirajan@gmail.com>`; end every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work in the worktree `D:/GitHub/composer/.worktrees/sqla-phase-1` (branch `feat/sqlalchemy-phase-1`). Create its virtualenv once before Task 1: `"/c/Users/Balaji Rajan/.local/bin/uv.exe" sync --all-extras && .venv/Scripts/python -m prisma generate`.
- Before every commit run, from the worktree root:
  `.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format --check src tests && .venv/Scripts/python -m pyright src tests`
- Integration tests need `TEST_DATABASE_URL`. Locally the `.env` Neon credentials are rejected; use a throwaway container and delete it afterwards:
  ```bash
  docker run -d --rm --name composer-p1-pg -e POSTGRES_USER=composer_test -e POSTGRES_PASSWORD=composer_test -e POSTGRES_DB=composer_test -p 55432:5432 postgres:15
  export TEST_DATABASE_URL=postgresql://composer_test:composer_test@localhost:55432/composer_test
  export DATABASE_URL=$TEST_DATABASE_URL DB_CONNECT_TIMEOUT_SECONDS=10
  .venv/Scripts/python -m prisma migrate deploy && .venv/Scripts/python -m alembic upgrade head
  # ... run tests ...
  docker stop composer-p1-pg
  ```
- Coordination: Codex is building telemetry on `feat/telemetry-foundation`. It owns `src/telemetry/**`. This plan owns `src/db/**` and `tests/conftest.py`. In `src/main.py` touch only the key-sync line (Task 5). Task 1 delivers the database health helper Codex's readiness probe (its Task 4.3) consumes.

---

## File structure

```
src/db/
  errors.py              NEW  AlreadyExists + unique-violation translation
  runtime.py             NEW  process-wide sessionmaker registry, session_scope(), one_shot_session()
  health.py              NEW  check_database() + alembic_head_revision() (for Codex's /ready)
  types.py               MOD  add ensure_utc()
  lifespan.py            MOD  register/unregister the sessionmaker
  repos/
    __init__.py          NEW
    common.py            NEW  Unset sentinel
    deployment_settings.py NEW
    llm_models.py        NEW
    llm_api_keys.py      NEW
    users.py             NEW
    api_keys.py          NEW
tests/unit/db_fakes.py   NEW  override_session(), FakeSession, FakeSessionmaker
tests/unit/db/           NEW tests for errors, runtime, health, ensure_utc
tests/integration/db/repos/   NEW real-Postgres repository + race tests
tests/integration/sqla_phase1/ NEW API behaviour tests (written first, run on Prisma)
```

Callers converted (by task): `src/api/admin_deployment_settings.py` (Task 3); `src/api/admin_llm_models.py`, `src/api/llm_models.py`, `src/api/llm_models_live.py`, `src/builder/model_policy.py`, `src/api/admin_builder.py`, `src/api/builder.py`, `src/executors/agent.py` (Task 4); `src/api/admin_llm_keys.py`, `src/api/admin_tools.py`, `src/security/key_sync.py`, `src/main.py`, `src/cli/keys.py` (Task 5); `src/security/auth.py`, `src/api/auth_common.py`, `src/api/auth_standalone.py`, `src/api/users.py`, `src/api/events_ws.py`, `src/api/mcp_servers.py`, `src/api/workflows.py`, `src/api/test_cleanup.py`, `src/migration/reconcile.py`, `src/cli/main.py` (Task 6); `src/api/api_keys.py`, `src/security/api_key_auth.py`, `src/api/admin_users.py` (Task 7).

---

### Task 1: Foundation: errors, session runtime, health check, test fakes

**Files:**
- Create: `src/db/errors.py`, `src/db/runtime.py`, `src/db/health.py`, `src/db/repos/__init__.py`, `src/db/repos/common.py`, `tests/unit/db_fakes.py`
- Modify: `src/db/types.py` (append `ensure_utc`), `src/db/lifespan.py`
- Test: `tests/unit/db/test_errors.py`, `tests/unit/db/test_runtime.py`, `tests/unit/db/test_ensure_utc.py`, `tests/integration/db/test_health.py`, `tests/unit/db/test_lifespan.py` (extend)

**Interfaces:**
- Produces:
  - `class AlreadyExists(Exception)` with attribute `constraint: str | None`
  - `async def add_or_already_exists(session: AsyncSession, *rows: object) -> None`: adds and flushes `rows` inside a savepoint; raises `AlreadyExists` on a unique violation and leaves the outer transaction usable. **Revised after Task 1 review:** the original `flush_or_already_exists(session)` (called after `session.add`) did not protect the caller's transaction, because `begin_nested()` flushes pending rows before it opens the savepoint. Task 1's code below shows the original version; the shipped helper is `add_or_already_exists`.
  - `def register_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None`
  - `def get_registered_sessionmaker() -> async_sessionmaker[AsyncSession] | None`
  - `def request_sessionmaker(conn: HTTPConnection) -> async_sessionmaker[AsyncSession] | None` (works for `Request` and `WebSocket`)
  - `def session_scope() -> AbstractAsyncContextManager[AsyncSession]`: raises `RuntimeError` if nothing is registered
  - `def one_shot_session(settings: Settings) -> AbstractAsyncContextManager[AsyncSession]`: CLI use; own non-pooled engine, disposed on exit
  - `@dataclass(frozen=True) class DatabaseHealth: reachable: bool; schema_current: bool | None; error: str | None`
  - `async def check_database(engine: AsyncEngine, *, timeout_seconds: float, expected_revision: str | None) -> DatabaseHealth`
  - `def alembic_head_revision() -> str`
  - `def ensure_utc(value: datetime) -> datetime`
  - `class Unset(enum.Enum): UNSET`; `UNSET = Unset.UNSET`
  - Test helpers: `class FakeSession` (AsyncMock `commit`, `rollback`, `close`), `def override_session(app: FastAPI) -> FakeSession`, `class FakeSessionmaker`

- [ ] **Step 1: Write the failing unit tests**

`tests/unit/db/test_ensure_utc.py`:
```python
from datetime import UTC, datetime, timedelta, timezone

from src.db.types import ensure_utc


def test_naive_datetime_is_taken_as_utc_like_prisma_did() -> None:
    assert ensure_utc(datetime(2026, 12, 1, 9, 30)) == datetime(2026, 12, 1, 9, 30, tzinfo=UTC)


def test_aware_datetime_is_converted_to_utc() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    assert ensure_utc(datetime(2026, 12, 1, 15, 0, tzinfo=ist)) == datetime(
        2026, 12, 1, 9, 30, tzinfo=UTC
    )
```

`tests/unit/db/test_runtime.py`:
```python
from collections.abc import Iterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.db import runtime


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    runtime.register_sessionmaker(None)
    yield
    runtime.register_sessionmaker(None)


async def test_session_scope_without_registration_raises() -> None:
    with pytest.raises(RuntimeError, match="no SQLAlchemy sessionmaker registered"):
        async with runtime.session_scope():
            pass


async def test_registered_sessionmaker_is_returned_and_used() -> None:
    engine = create_async_engine("postgresql+psycopg://u:p@localhost:1/x")
    sm = async_sessionmaker(engine)
    runtime.register_sessionmaker(sm)
    assert runtime.get_registered_sessionmaker() is sm
    async with runtime.session_scope() as session:
        assert isinstance(session, AsyncSession)
    await engine.dispose()
```

`tests/unit/db/test_errors.py`:
```python
from unittest.mock import AsyncMock, MagicMock

import psycopg.errors
import pytest
from sqlalchemy.exc import IntegrityError

from src.db.errors import AlreadyExists, flush_or_already_exists


def _integrity(orig: Exception) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, orig)


class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _session(flush_error: Exception | None) -> MagicMock:
    session = MagicMock()
    session.begin_nested = MagicMock(return_value=_Nested())
    session.flush = AsyncMock(side_effect=flush_error)
    return session


async def test_unique_violation_becomes_already_exists_with_constraint_name() -> None:
    orig = psycopg.errors.UniqueViolation("duplicate key")
    session = _session(_integrity(orig))
    # psycopg exposes the violated constraint on diag; fake it here.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(orig), "diag", property(lambda _: MagicMock(constraint_name="users_email_key")))
        with pytest.raises(AlreadyExists) as info:
            await flush_or_already_exists(session)
    assert info.value.constraint == "users_email_key"


async def test_other_integrity_errors_propagate_unchanged() -> None:
    session = _session(_integrity(psycopg.errors.ForeignKeyViolation("fk")))
    with pytest.raises(IntegrityError):
        await flush_or_already_exists(session)


async def test_successful_flush_returns_none() -> None:
    session = _session(None)
    await flush_or_already_exists(session)
    session.flush.assert_awaited_once()
```

Extend `tests/unit/db/test_lifespan.py` with (read the file first and reuse its existing fakes for the engine and `connect_with_retry`):
```python
async def test_lifespan_registers_and_unregisters_the_sessionmaker(...) -> None:
    # inside the lifespan: runtime.get_registered_sessionmaker() is app.state.db_sessionmaker
    # after exit: runtime.get_registered_sessionmaker() is None
```
Write it with the same fixtures the file's existing tests use; assert exactly the two conditions in the comments.

- [ ] **Step 2: Run them and confirm they fail on missing names**

Run: `.venv/Scripts/python -m pytest --no-cov -q tests/unit/db/test_ensure_utc.py tests/unit/db/test_runtime.py tests/unit/db/test_errors.py tests/unit/db/test_lifespan.py`
Expected: FAIL / ImportError for `ensure_utc`, `src.db.runtime`, `src.db.errors`; the lifespan test fails on registration.

- [ ] **Step 3: Implement**

Append to `src/db/types.py`:
```python
def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` as aware UTC; a naive value is taken to be UTC.

    Prisma stored naive datetimes from request bodies as UTC wall-clock time.
    Call this at the API boundary for client-supplied datetimes so
    ``UTCDateTime`` (which rejects naive values) keeps accepting them.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
```

`src/db/errors.py`:
```python
"""Errors raised at the repository boundary."""

import psycopg.errors
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class AlreadyExists(Exception):
    """A unique constraint rejected the write. ``constraint`` names it when known."""

    def __init__(self, constraint: str | None) -> None:
        super().__init__(f"unique constraint violated: {constraint or 'unknown'}")
        self.constraint = constraint


async def flush_or_already_exists(session: AsyncSession) -> None:
    """Flush pending inserts in a savepoint, translating unique violations.

    The savepoint means a duplicate leaves the caller's transaction usable,
    so a route can still answer 409 and keep working with the session.
    """
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        if isinstance(exc.orig, psycopg.errors.UniqueViolation):
            raise AlreadyExists(exc.orig.diag.constraint_name) from exc
        raise
```

`src/db/runtime.py`:
```python
"""Sessions for code that runs outside a request.

The app lifespan registers its sessionmaker here. Executors, startup jobs and
auth dependencies take sessions from it; the CLI builds its own short-lived
engine with ``one_shot_session``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from src.config import Settings
from src.db.engine import build_engine
from src.db.session import build_sessionmaker

_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def register_sessionmaker(sm: async_sessionmaker[AsyncSession] | None) -> None:
    global _sessionmaker
    _sessionmaker = sm


def get_registered_sessionmaker() -> async_sessionmaker[AsyncSession] | None:
    return _sessionmaker


def request_sessionmaker(conn: HTTPConnection) -> async_sessionmaker[AsyncSession] | None:
    """The app's sessionmaker, or None when the app runs without a database (unit tests)."""
    return getattr(conn.app.state, "db_sessionmaker", None)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("no SQLAlchemy sessionmaker registered; is the app lifespan running?")
    async with _sessionmaker() as session:
        yield session


@asynccontextmanager
async def one_shot_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    """A session on its own non-pooled engine, for CLI commands."""
    engine = build_engine(settings, for_migrations=True)  # NullPool: right for one-shot runs
    try:
        async with build_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()
```

In `src/db/lifespan.py`, inside `sqlalchemy_lifespan`: call `register_sessionmaker(app.state.db_sessionmaker)` right after setting `app.state.db_sessionmaker`, and `register_sessionmaker(None)` first thing in the `finally:` block. Import it from `src.db.runtime`.

`src/db/repos/__init__.py`: one-line docstring `"""Repository modules: the only code that runs database queries."""`.

`src/db/repos/common.py`:
```python
"""Helpers shared by repository modules."""

import enum


class Unset(enum.Enum):
    """Marks an update argument that was not given (distinct from None)."""

    UNSET = enum.auto()


UNSET = Unset.UNSET
```

`src/db/health.py`:
```python
"""Database readiness, for the /ready probe (telemetry roadmap Task 4.3).

Errors are reported by exception class name only: never a message, which can
contain a connection string.
"""

import asyncio
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@dataclass(frozen=True)
class DatabaseHealth:
    reachable: bool
    schema_current: bool | None  # None when no expected revision was given or it was unreachable
    error: str | None


@cache
def alembic_head_revision() -> str:
    head = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI))).get_current_head()
    if head is None:
        raise RuntimeError("alembic has no head revision")
    return head


async def check_database(
    engine: AsyncEngine, *, timeout_seconds: float, expected_revision: str | None
) -> DatabaseHealth:
    try:
        async with asyncio.timeout(timeout_seconds), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            if expected_revision is None:
                return DatabaseHealth(reachable=True, schema_current=None, error=None)
            current = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
            return DatabaseHealth(
                reachable=True, schema_current=current == expected_revision, error=None
            )
    except Exception as exc:
        return DatabaseHealth(reachable=False, schema_current=None, error=type(exc).__name__)
```

`tests/unit/db_fakes.py`:
```python
"""Fakes for route unit tests: routes get a FakeSession; repo functions are patched."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi import FastAPI

from src.db.session import get_session


class FakeSession:
    """Stands in for AsyncSession. Repo functions are patched, so only these are called."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()


def override_session(app: FastAPI) -> FakeSession:
    session = FakeSession()

    async def _dep() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _dep
    return session


class FakeSessionmaker:
    """Assign to ``app.state.db_sessionmaker`` (or register it) when code opens its own sessions.

    Only do this in tests that patch every repo call the code makes: setting
    it also switches on the auth dependencies' live user checks.
    """

    def __init__(self, session: FakeSession | None = None) -> None:
        self.session = session or FakeSession()

    def __call__(self) -> "FakeSessionmaker._Ctx":
        return FakeSessionmaker._Ctx(self.session)

    class _Ctx:
        def __init__(self, session: FakeSession) -> None:
            self._session = session

        async def __aenter__(self) -> FakeSession:
            return self._session

        async def __aexit__(self, *exc: object) -> bool:
            return False


@asynccontextmanager
async def fake_scope(session: FakeSession) -> AsyncIterator[FakeSession]:
    yield session
```

`tests/integration/db/test_health.py`:
```python
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.db.health import alembic_head_revision, check_database

pytestmark = pytest.mark.integration


async def test_reachable_database_on_head_revision_is_current(engine: AsyncEngine) -> None:
    health = await check_database(engine, timeout_seconds=5, expected_revision=alembic_head_revision())
    assert health.reachable and health.schema_current is True and health.error is None


async def test_wrong_expected_revision_is_reported_not_raised(engine: AsyncEngine) -> None:
    health = await check_database(engine, timeout_seconds=5, expected_revision="not-a-revision")
    assert health.reachable and health.schema_current is False


async def test_unreachable_database_reports_class_name_only() -> None:
    bad = create_async_engine(
        "postgresql+psycopg://u:secret-pw@127.0.0.1:1/x", connect_args={"connect_timeout": 2}
    )
    health = await check_database(bad, timeout_seconds=3, expected_revision=None)
    await bad.dispose()
    assert health.reachable is False
    assert health.error is not None and "secret-pw" not in health.error
```

- [ ] **Step 4: Run unit and integration tests; confirm they pass**

Run: `.venv/Scripts/python -m pytest --no-cov -q tests/unit/db` then (with the container up) `.venv/Scripts/python -m pytest --no-cov -q -m integration tests/integration/db/test_health.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/db tests/unit/db tests/unit/db_fakes.py tests/integration/db/test_health.py
git commit -m "feat(db): session runtime, AlreadyExists and readiness check for phase 1

Code outside a request (executors, startup sync, auth dependencies, CLI)
needs SQLAlchemy sessions too; the lifespan now registers its sessionmaker
for them. check_database() is the helper the telemetry roadmap's /ready
probe consumes: it reports exception class names only, never messages,
which can carry connection strings.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: API behaviour tests, proven on the Prisma code

These tests define "unchanged". They use only HTTP calls plus SQLAlchemy for setup and cleanup (same tables either way), so they run before and after porting.

**Files:**
- Create: `tests/integration/sqla_phase1/__init__.py`, `tests/integration/sqla_phase1/conftest.py`, `test_settings_and_models.py`, `test_llm_keys.py`, `test_users_and_auth.py`, `test_api_keys.py` (all under `tests/integration/sqla_phase1/`)

**Interfaces:**
- Produces fixtures: `admin_headers: dict[str, str]`, `member: Member` (dataclass with `id`, `email`, `password`, `headers`), `sessionmaker_: async_sessionmaker[AsyncSession]`

- [ ] **Step 1: Write the fixtures**

`tests/integration/sqla_phase1/conftest.py`:
```python
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import User, UserRole


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]


async def _register(client: AsyncClient) -> Member:
    email = f"p1-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return Member(body["id"], email, password, {"Authorization": f"Bearer {body['accessToken']}"})


@pytest_asyncio.fixture
async def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return app.state.db_sessionmaker


@pytest_asyncio.fixture
async def member(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Member]:
    m = await _register(client)
    yield m
    async with sessionmaker_() as s:
        await s.execute(delete(User).where(User.id == m.id))  # api_keys cascade
        await s.commit()


@pytest_asyncio.fixture
async def admin_headers(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[dict[str, str]]:
    m = await _register(client)
    async with sessionmaker_() as s:
        await s.execute(update(User).where(User.id == m.id).values(role=UserRole.ADMIN))
        await s.commit()
    yield m.headers
    async with sessionmaker_() as s:
        await s.execute(delete(User).where(User.id == m.id))
        await s.commit()
```

- [ ] **Step 2: Write the behaviour tests**

Each test cleans up the rows it creates (unique keys/emails per run; delete in `finally`). Write these tests exactly; they pin today's status codes, JSON shapes and timestamp formats.

`test_settings_and_models.py`:
```python
import re
import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import DeploymentSetting, LlmModel
from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration
ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?(Z|\+00:00)$")


async def test_deployment_setting_create_update_list_get(
    client: AsyncClient, admin_headers: dict[str, str], sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    key = f"p1.test.{secrets.token_hex(4)}"
    try:
        assert (await client.get(f"/admin/deployment-settings/{key}", headers=admin_headers)).status_code == 404
        r = await client.put(f"/admin/deployment-settings/{key}", json={"value": "a"}, headers=admin_headers)
        assert r.status_code == 200 and r.json() == {"key": key, "value": "a"}
        r = await client.put(f"/admin/deployment-settings/{key}", json={"value": "b"}, headers=admin_headers)
        assert r.json() == {"key": key, "value": "b"}
        listed = (await client.get("/admin/deployment-settings", headers=admin_headers)).json()
        keys = [i["key"] for i in listed]
        assert {"key": key, "value": "b"} in listed and keys == sorted(keys)
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(DeploymentSetting).where(DeploymentSetting.key == key))
            await s.commit()


async def test_deployment_settings_are_admin_only(client: AsyncClient, member: Member) -> None:
    r = await client.get("/admin/deployment-settings", headers=member.headers)
    assert r.status_code == 403


async def test_llm_model_crud_conflict_and_member_listing(
    client: AsyncClient, admin_headers: dict[str, str], member: Member,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    provider = f"p1prov{secrets.token_hex(3)}"
    try:
        body = {"provider": provider, "modelId": "m-1", "label": "M1", "enabled": True}
        r = await client.post("/admin/llm-models", json=body, headers=admin_headers)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["modelId"] == "m-1" and created["verificationStatus"] is None
        assert ISO.match(created["createdAt"]) and ISO.match(created["updatedAt"])
        assert (await client.post("/admin/llm-models", json=body, headers=admin_headers)).status_code == 409
        r = await client.patch(f"/admin/llm-models/{created['id']}", json={"enabled": False}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["label"] == "M1"
        assert (await client.get(f"/llm-models?provider={provider}", headers=member.headers)).json() == []
        await client.patch(f"/admin/llm-models/{created['id']}", json={"enabled": True}, headers=admin_headers)
        listed = (await client.get(f"/llm-models?provider={provider}", headers=member.headers)).json()
        assert [m["modelId"] for m in listed] == ["m-1"]
        assert (await client.patch("/admin/llm-models/nope", json={}, headers=admin_headers)).status_code == 404
        assert (await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)).status_code == 204
        assert (await client.delete(f"/admin/llm-models/{created['id']}", headers=admin_headers)).status_code == 404
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmModel).where(LlmModel.provider == provider))
            await s.commit()
```

`test_llm_keys.py` (needs `ENCRYPTION_KEY` set, as CI does):
```python
import re

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import LlmApiKey

pytestmark = pytest.mark.integration
STR_DT = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?\+00:00$")  # str(datetime)


async def test_llm_key_upsert_get_list_delete(
    client: AsyncClient, admin_headers: dict[str, str], sessionmaker_: async_sessionmaker[AsyncSession]
) -> None:
    provider = "typesafe"  # in _ALLOWED_PROVIDERS; the original row (if any) is restored afterwards
    async with sessionmaker_() as s:
        original = await s.scalar(select(LlmApiKey).where(LlmApiKey.provider == provider))
        if original is not None:
            s.expunge(original)
    try:
        r = await client.put(f"/admin/llm-keys/{provider}", json={"value": "sk-first-value"}, headers=admin_headers)
        assert r.status_code == 200 and r.json()["keyPrefix"] == "sk-fir"
        assert STR_DT.match(r.json()["updatedAt"]), r.json()["updatedAt"]
        r = await client.put(f"/admin/llm-keys/{provider}", json={"value": "sk-second"}, headers=admin_headers)
        assert r.json()["keyPrefix"] == "sk-sec"
        got = (await client.get(f"/admin/llm-keys/{provider}", headers=admin_headers)).json()
        assert got["provider"] == provider and got["keyPrefix"] == "sk-sec"
        listed = (await client.get("/admin/llm-keys", headers=admin_headers)).json()
        assert [k["provider"] for k in listed] == sorted(k["provider"] for k in listed)
        assert (await client.put("/admin/llm-keys/not-a-provider", json={"value": "x"}, headers=admin_headers)).status_code == 422
        assert (await client.delete(f"/admin/llm-keys/{provider}", headers=admin_headers)).status_code == 204
        assert (await client.get(f"/admin/llm-keys/{provider}", headers=admin_headers)).status_code == 404
    finally:
        async with sessionmaker_() as s:
            await s.execute(delete(LlmApiKey).where(LlmApiKey.provider == provider))
            if original is not None:
                s.add(original)
            await s.commit()
```

`test_users_and_auth.py`:
```python
import secrets

import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration


async def test_duplicate_registration_is_409(client: AsyncClient, member: Member) -> None:
    r = await client.post("/auth/register", json={"email": member.email, "password": "another-password-1"})
    assert r.status_code == 409


async def test_me_change_password_and_login(client: AsyncClient, member: Member) -> None:
    me = (await client.get("/auth/me", headers=member.headers)).json()
    assert me == {"id": member.id, "email": member.email, "displayName": None, "role": "member"}
    r = await client.post(
        "/auth/change-password",
        json={"currentPassword": member.password, "newPassword": "brand-new-password-2"},
        headers=member.headers,
    )
    assert r.status_code in (200, 204), r.text
    assert (await client.post("/auth/login", json={"email": member.email, "password": member.password})).status_code == 401
    assert (await client.post("/auth/login", json={"email": member.email, "password": "brand-new-password-2"})).status_code == 200


async def test_admin_role_deactivate_reactivate_and_reset(
    client: AsyncClient, admin_headers: dict[str, str], member: Member
) -> None:
    r = await client.post(f"/admin/users/{member.id}/role", json={"role": "admin"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["isActive"] is True
    r = await client.delete(f"/admin/users/{member.id}", headers=admin_headers)
    assert r.json()["isActive"] is False
    assert (await client.get("/auth/me", headers=member.headers)).status_code == 401
    assert (await client.post(f"/admin/users/{member.id}/reactivate", headers=admin_headers)).json()["isActive"] is True
    r = await client.post(f"/admin/users/{member.id}/reset-password", headers=admin_headers)
    temp = r.json()["temporaryPassword"]
    assert (await client.get("/auth/me", headers=member.headers)).status_code == 401  # must change password
    login = await client.post("/auth/login", json={"email": member.email, "password": temp})
    assert login.status_code in (200, 403), login.text  # record whichever today returns; keep it
    listed = (await client.get("/admin/users", headers=admin_headers)).json()
    emails = [u["email"] for u in listed]
    assert member.email in emails and emails == sorted(emails)
    assert (await client.post("/admin/users/nope/reactivate", headers=admin_headers)).status_code == 404


async def test_user_search_is_case_insensitive_and_treats_wildcards_literally(
    client: AsyncClient, member: Member
) -> None:
    hits = (await client.get(f"/users/search?q={member.email[:8].upper()}", headers=member.headers)).json()
    assert any(h["id"] == member.id for h in hits)
    assert set(hits[0]) == {"id", "email", "displayName"}
    token = secrets.token_hex(3)
    # "p1_" must not match "p1-…" if wildcards are escaped. Run on Prisma first:
    # whatever Prisma returns here is the behaviour to keep.
    wild = (await client.get(f"/users/search?q=p1_{token}", headers=member.headers)).json()
    assert wild == []
```

The two `in (…)` assertions above are deliberately loose only until Step 3: when they pass on Prisma, replace each with the single status code observed (`== 200` or `== 403`, `== 200` or `== 204`) so the test pins today's exact behaviour.

`test_api_keys.py`:
```python
import pytest
from httpx import AsyncClient

from tests.integration.sqla_phase1.conftest import Member

pytestmark = pytest.mark.integration


async def test_api_key_lifecycle_and_external_auth(client: AsyncClient, member: Member) -> None:
    created = await client.post(
        "/api-keys", json={"label": "ci", "expiresAt": "2099-01-01T00:00:00"}, headers=member.headers
    )
    assert created.status_code == 201, created.text  # naive expiresAt is accepted today
    body = created.json()
    assert body["key"].startswith("ck_") and body["expiresAt"].startswith("2099-01-01T00:00:00")
    listed = (await client.get("/api-keys", headers=member.headers)).json()
    assert [k["id"] for k in listed] == [body["id"]] and "key" not in listed[0]
    assert listed[0]["lastUsedAt"] is None
    # The ck_ key authenticates the external-invoke dependency; an unknown slug is 404, not 401.
    ext = await client.post("/api/run/does-not-exist", json={}, headers={"Authorization": f"Bearer {body['key']}"})
    assert ext.status_code != 401, ext.text
    after = (await client.get("/api-keys", headers=member.headers)).json()
    assert after[0]["lastUsedAt"] is not None
    assert (await client.delete(f"/api-keys/{body['id']}", headers=member.headers)).status_code == 204
    assert (await client.delete(f"/api-keys/{body['id']}", headers=member.headers)).status_code == 204  # idempotent
    revoked = await client.post("/api/run/does-not-exist", json={}, headers={"Authorization": f"Bearer {body['key']}"})
    assert revoked.status_code == 401
    assert (await client.delete("/api-keys/nope", headers=member.headers)).status_code == 404
```

- [ ] **Step 3: Run on the unchanged Prisma code; they must pass**

Run (container up): `.venv/Scripts/python -m pytest --no-cov -q -m integration tests/integration/sqla_phase1`
Expected: PASS. If an assertion fails, the test is wrong about today's behaviour: fix the test to match what Prisma does (never the app), and note the finding in the commit message. Pin the loose `in (…)` assertions to the observed codes. Check `_DB_ONLY_PROVIDERS` in `src/api/llm_models_live.py` and add one test: `GET /llm-models/available?provider=<a db-only provider>` with a seeded enabled `llm_models` row returns it with `"source": "db"`.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/sqla_phase1
git commit -m "test(db): pin phase 1 API behaviour on the Prisma code

These run before any porting so they define 'unchanged': status codes,
JSON shapes, timestamp formats, ordering, the naive expiresAt acceptance
and the search wildcard behaviour, exactly as Prisma serves them today.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Deployment settings

**Files:**
- Create: `src/db/repos/deployment_settings.py`, `tests/integration/db/repos/__init__.py`, `tests/integration/db/repos/test_deployment_settings.py`
- Modify: `src/api/admin_deployment_settings.py`
- Test: convert `tests/unit/api/test_admin_deployment_settings.py`
- (`src/builder/model_policy.py` also reads and writes deployment settings, but it reads the model catalog too, so it moves whole in Task 4.)

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class SettingRecord: key: str; value: str; updated_at: datetime
  async def list_all(session: AsyncSession) -> list[SettingRecord]          # ORDER BY key
  async def get(session: AsyncSession, key: str) -> SettingRecord | None
  async def upsert(session: AsyncSession, key: str, value: str) -> SettingRecord
  ```

- [ ] **Step 1: Write the failing repository test**

`tests/integration/db/repos/test_deployment_settings.py`:
```python
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import deployment_settings as repo

pytestmark = pytest.mark.integration


async def test_upsert_creates_then_updates_and_bumps_updated_at(session: AsyncSession) -> None:
    key = f"t.{secrets.token_hex(4)}"
    first = await repo.upsert(session, key, "a")
    second = await repo.upsert(session, key, "b")
    assert (first.value, second.value) == ("a", "b")
    assert second.updated_at >= first.updated_at
    assert await repo.get(session, key) == second


async def test_get_missing_is_none_and_list_is_sorted(session: AsyncSession) -> None:
    assert await repo.get(session, "missing-" + secrets.token_hex(4)) is None
    await repo.upsert(session, "zz." + secrets.token_hex(3), "1")
    await repo.upsert(session, "aa." + secrets.token_hex(3), "1")
    keys = [r.key for r in await repo.list_all(session)]
    assert keys == sorted(keys)
```

- [ ] **Step 2: Run it; expect ImportError**

Run: `.venv/Scripts/python -m pytest --no-cov -q -m integration tests/integration/db/repos/test_deployment_settings.py` → FAIL (no module).

- [ ] **Step 3: Implement the repository**

`src/db/repos/deployment_settings.py`:
```python
"""Deployment-wide key/value settings."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeploymentSetting
from src.db.types import utcnow


@dataclass(frozen=True)
class SettingRecord:
    key: str
    value: str
    updated_at: datetime


def _record(row: DeploymentSetting) -> SettingRecord:
    return SettingRecord(key=row.key, value=row.value, updated_at=row.updated_at)


async def list_all(session: AsyncSession) -> list[SettingRecord]:
    rows = await session.scalars(select(DeploymentSetting).order_by(DeploymentSetting.key))
    return [_record(r) for r in rows]


async def get(session: AsyncSession, key: str) -> SettingRecord | None:
    row = await session.get(DeploymentSetting, key)
    return _record(row) if row else None


async def upsert(session: AsyncSession, key: str, value: str) -> SettingRecord:
    now = utcnow()  # ON CONFLICT bypasses the ORM's onupdate, so set it here
    stmt = (
        insert(DeploymentSetting)
        .values(key=key, value=value, updated_at=now)
        .on_conflict_do_update(
            index_elements=[DeploymentSetting.key], set_={"value": value, "updated_at": now}
        )
        .returning(DeploymentSetting)
        .execution_options(populate_existing=True)
    )
    return _record((await session.scalars(stmt)).one())
```

- [ ] **Step 4: Run the repository test; expect PASS**

- [ ] **Step 5: Port the callers**

`src/api/admin_deployment_settings.py`: remove the Prisma and `get_db` imports; import `AsyncSession`, `get_session` and `deployment_settings as settings_repo`. Each handler takes `session: AsyncSession = Depends(get_session)`:
```python
@router.get("", response_model=list[SettingItem])
async def list_settings(
    session: AsyncSession = Depends(get_session), _admin: str = Depends(ensure_admin)
) -> list[SettingItem]:  # pyright: ignore[reportUnusedFunction]
    return [SettingItem(key=r.key, value=r.value) for r in await settings_repo.list_all(session)]


@router.get("/{key}", response_model=SettingItem)
async def get_setting(
    key: str, session: AsyncSession = Depends(get_session), _admin: str = Depends(ensure_admin)
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    row = await settings_repo.get(session, key)
    if row is None:
        raise HTTPException(404, f"setting {key!r} not set")
    return SettingItem(key=row.key, value=row.value)


@router.put("/{key}", response_model=SettingItem)
async def upsert_setting(
    key: str,
    payload: SettingUpdate,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    row = await settings_repo.upsert(session, key, payload.value)
    await session.commit()
    return SettingItem(key=row.key, value=row.value)
```

- [ ] **Step 6: Convert the unit tests**

Rewrite `tests/unit/api/test_admin_deployment_settings.py` with this pattern (example for `test_list_all`; convert every test in the file the same way):
```python
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from src.db.repos import deployment_settings as settings_repo
from src.db.repos.deployment_settings import SettingRecord
from tests.unit.db_fakes import override_session


def _record(key: str = "tool.tavily.enabled", value: str = "true") -> SettingRecord:
    return SettingRecord(key=key, value=value, updated_at=datetime(2026, 4, 22, tzinfo=UTC))


def test_list_all(monkeypatch: pytest.MonkeyPatch) -> None:
    client, app = _client_admin(monkeypatch)   # keep the existing env setup; drop the db MagicMock
    override_session(app)
    monkeypatch.setattr(settings_repo, "list_all", AsyncMock(return_value=[_record()]))
    resp = client.get("/admin/deployment-settings")
    assert resp.status_code == 200
    assert resp.json() == [{"key": "tool.tavily.enabled", "value": "true"}]
```
Mapping for the rest of the file: `db.deploymentsetting.find_many` → `settings_repo.list_all`; `find_unique` → `settings_repo.get`; `create`/`update` → `settings_repo.upsert`. Where a test asserted on `db.deploymentsetting.update.call_args`, assert on the patched `upsert`'s `await_args` instead, and assert `session.commit.await_count == 1` for the PUT. The `_client_member` 403 test keeps working unchanged because no `db_sessionmaker` is set (auth treats the caller as `member`).

- [ ] **Step 7: Run unit tests, the repo test and the behaviour tests**

Run: `.venv/Scripts/python -m pytest --no-cov -q tests/unit/api/test_admin_deployment_settings.py` then `-m integration tests/integration/db/repos/test_deployment_settings.py tests/integration/sqla_phase1/test_settings_and_models.py`.
Expected: all pass.

- [ ] **Step 8: Commit** — `feat(db): deployment settings on SQLAlchemy (phase 1)` with a why-line: upsert is now one atomic statement instead of find-then-write.

---

### Task 4: LLM model catalog

**Files:**
- Create: `src/db/repos/llm_models.py`, `tests/integration/db/repos/test_llm_models.py`
- Modify: `src/api/admin_llm_models.py`, `src/api/llm_models.py`, `src/api/llm_models_live.py`, `src/builder/model_policy.py` (whole file), `src/api/admin_builder.py`, `src/api/builder.py`, `src/executors/agent.py`
- Test: convert `tests/unit/api/test_admin_llm_models_routes.py`, `test_llm_models_live.py`, `test_llm_models_live_fetch.py`, and every unit test that fakes `db.llmmodel` or `db.deploymentsetting` for model policy, the builder routes or the agent (`grep -rlnE "llmmodel|deploymentsetting" tests/unit`)

**Interfaces:**
- Consumes: `AlreadyExists`, `add_or_already_exists`, `UNSET`/`Unset`, `get_registered_sessionmaker`
- Produces:
  ```python
  @dataclass(frozen=True)
  class LlmModelRecord:
      id: str; provider: str; model_id: str; label: str | None; enabled: bool
      verification_status: str | None; verification_message: str | None
      verified_at: datetime | None; created_at: datetime; updated_at: datetime
  async def list_models(session, *, provider: str | None = None, enabled_only: bool = False) -> list[LlmModelRecord]  # ORDER BY provider, model_id
  async def get_by_id(session, model_pk: str) -> LlmModelRecord | None
  async def get_by_provider_model(session, provider: str, model_id: str) -> LlmModelRecord | None
  async def create(session, *, provider: str, model_id: str, label: str | None, enabled: bool) -> LlmModelRecord  # raises AlreadyExists
  async def update(session, model_pk: str, *, label: str | Unset = UNSET, enabled: bool | Unset = UNSET) -> LlmModelRecord | None
  async def record_verification(session, model_pk: str, *, status: str, message: str, verified_at: datetime, disable: bool) -> LlmModelRecord | None
  async def delete(session, model_pk: str) -> bool
  ```
  `LlmModelSummary.model_validate(record)` works on the dataclass: the model has `from_attributes=True` and `populate_by_name=True`, so snake_case attributes fill the camelCase-aliased fields.

- [ ] **Step 1: Write the failing repository test**

`tests/integration/db/repos/test_llm_models.py`:
```python
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.repos import llm_models as repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


async def test_create_duplicate_raises_already_exists_and_session_stays_usable(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    with pytest.raises(AlreadyExists) as info:
        await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    assert info.value.constraint == "llm_models_provider_model_id_key"
    assert [m.model_id for m in await repo.list_models(session, provider=p)] == ["m"]


async def test_update_changes_only_given_fields(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label="L", enabled=True)
    updated = await repo.update(session, m.id, enabled=False)
    assert updated is not None and updated.label == "L" and updated.enabled is False
    assert await repo.update(session, "missing", enabled=False) is None


async def test_enabled_only_and_ordering(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    await repo.create(session, provider=p, model_id="b", label=None, enabled=True)
    await repo.create(session, provider=p, model_id="a", label=None, enabled=True)
    await repo.create(session, provider=p, model_id="c", label=None, enabled=False)
    assert [m.model_id for m in await repo.list_models(session, provider=p, enabled_only=True)] == ["a", "b"]


async def test_record_verification_can_disable(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    now = utcnow()
    r = await repo.record_verification(session, m.id, status="unavailable", message="gone", verified_at=now, disable=True)
    assert r is not None and (r.verification_status, r.enabled, r.verified_at) == ("unavailable", False, now)
    assert await repo.get_by_provider_model(session, p, "m") == r
    assert await repo.delete(session, m.id) is True and await repo.delete(session, m.id) is False
```

- [ ] **Step 2: Run it; expect ImportError**

- [ ] **Step 3: Implement**

`src/db/repos/llm_models.py`:
```python
"""The LLM model catalog."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_already_exists
from src.db.models import LlmModel
from src.db.repos.common import UNSET, Unset
from src.db.types import utcnow


@dataclass(frozen=True)
class LlmModelRecord:
    id: str
    provider: str
    model_id: str
    label: str | None
    enabled: bool
    verification_status: str | None
    verification_message: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _record(r: LlmModel) -> LlmModelRecord:
    return LlmModelRecord(
        id=r.id, provider=r.provider, model_id=r.model_id, label=r.label, enabled=r.enabled,
        verification_status=r.verification_status, verification_message=r.verification_message,
        verified_at=r.verified_at, created_at=r.created_at, updated_at=r.updated_at,
    )


async def list_models(
    session: AsyncSession, *, provider: str | None = None, enabled_only: bool = False
) -> list[LlmModelRecord]:
    stmt = select(LlmModel).order_by(LlmModel.provider, LlmModel.model_id)
    if provider is not None:
        stmt = stmt.where(LlmModel.provider == provider)
    if enabled_only:
        stmt = stmt.where(LlmModel.enabled.is_(True))
    return [_record(r) for r in await session.scalars(stmt)]


async def get_by_id(session: AsyncSession, model_pk: str) -> LlmModelRecord | None:
    row = await session.get(LlmModel, model_pk)
    return _record(row) if row else None


async def get_by_provider_model(session: AsyncSession, provider: str, model_id: str) -> LlmModelRecord | None:
    row = await session.scalar(
        select(LlmModel).where(LlmModel.provider == provider, LlmModel.model_id == model_id)
    )
    return _record(row) if row else None


async def create(
    session: AsyncSession, *, provider: str, model_id: str, label: str | None, enabled: bool
) -> LlmModelRecord:
    row = LlmModel(provider=provider, model_id=model_id, label=label, enabled=enabled)
    await add_or_already_exists(session, row)
    return _record(row)


async def _update(session: AsyncSession, model_pk: str, values: dict[str, object]) -> LlmModelRecord | None:
    stmt = (
        sa_update(LlmModel)
        .where(LlmModel.id == model_pk)
        .values(**values, updated_at=utcnow())
        .returning(LlmModel)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def update(
    session: AsyncSession, model_pk: str, *, label: str | Unset = UNSET, enabled: bool | Unset = UNSET
) -> LlmModelRecord | None:
    values: dict[str, object] = {}
    if not isinstance(label, Unset):
        values["label"] = label
    if not isinstance(enabled, Unset):
        values["enabled"] = enabled
    return await _update(session, model_pk, values)


async def record_verification(
    session: AsyncSession, model_pk: str, *, status: str, message: str, verified_at: datetime, disable: bool
) -> LlmModelRecord | None:
    values: dict[str, object] = {
        "verification_status": status, "verification_message": message, "verified_at": verified_at,
    }
    if disable:
        values["enabled"] = False
    return await _update(session, model_pk, values)


async def delete(session: AsyncSession, model_pk: str) -> bool:
    result = await session.execute(
        sa_delete(LlmModel).where(LlmModel.id == model_pk).returning(LlmModel.id)
    )
    return result.first() is not None
```
Note: an empty PATCH (`{}`) still bumps `updated_at`, exactly as Prisma's `update(data={})` did.

- [ ] **Step 4: Run the repository test; expect PASS**

- [ ] **Step 5: Port the callers**

`src/api/llm_models.py` `list_enabled_models`: `rows = await models_repo.list_models(session, provider=provider, enabled_only=True)`; return `[LlmModelSummary.model_validate(r) for r in rows]`.

`src/api/admin_llm_models.py`:
- list: `models_repo.list_models(session)`.
- create: `try: row = await models_repo.create(session, provider=…, model_id=payload.model_id, label=payload.label, enabled=payload.enabled)` / `except AlreadyExists as exc: raise HTTPException(409, f"Model already exists for provider: {exc}") from exc`; then `await session.commit()`. (Previously any exception became 409; now only a duplicate does, and other errors surface as 500 instead of being disguised.)
- patch: `row = await models_repo.update(session, model_id, label=payload.label if payload.label is not None else UNSET, enabled=payload.enabled if payload.enabled is not None else UNSET)`; 404 when `None`; commit.
- verify: `row = await models_repo.get_by_id(…)`; the key lookup becomes `llm_api_keys` repo in Task 5 (keep the Prisma line here until then and port it in Task 5); `now = utcnow()`; when status is `ok`/`unavailable`: `updated = await models_repo.record_verification(session, model_id, status=result.status, message=result.message, verified_at=now, disable=auto_disabled)` then commit; otherwise `updated = row`. Keep `verified_at=now.isoformat()` in the response.
- delete: `if not await models_repo.delete(session, model_id): raise 404`; commit.

`src/api/llm_models_live.py` `_db_fallback(session, provider)`: `rows = await models_repo.list_models(session, provider=provider, enabled_only=True)`, then `LiveModel(modelId=r.model_id, label=r.label, source="db")`. The route takes `session: AsyncSession = Depends(get_session)`.

`src/builder/model_policy.py` (moves whole here: it reads the catalog and reads/writes deployment settings). Change every `db: Any` parameter to `session: AsyncSession`:
```python
async def _setting(session: AsyncSession, key: str) -> str | None:
    row = await settings_repo.get(session, key)
    return row.value if row else None


async def enabled_model_catalog(session: AsyncSession) -> dict[str, str | None]:
    rows = await models_repo.list_models(session, enabled_only=True)
    return {f"{r.provider}/{r.model_id}": r.label for r in rows}
```
In `save_policy`, replace the Prisma `upsert` with `await settings_repo.upsert(session, key, value)`; the caller commits. `src/api/admin_builder.py` and `src/api/builder.py`: every handler that calls `load_policy`, `save_policy` or `enabled_model_catalog` gains `session: AsyncSession = Depends(get_session)` and passes `session` instead of `db`; `admin_builder`'s save handler calls `await session.commit()` after `save_policy`. Leave their other `db` usage (workflows: Phase 3) untouched.

`src/executors/agent.py` `_ensure_model_available`:
```python
    sessionmaker = get_registered_sessionmaker()
    if sessionmaker is None:
        return
    async with sessionmaker() as session:
        row = await models_repo.get_by_provider_model(session, provider, model_name)
    if row is None:
        return
    if row.verification_status == "unavailable":
        raise ModelUnavailableError(
            f"Model {model_string!r} is marked unavailable: "
            f"{row.verification_message or 'see Admin → LLM models for details'}. "
            "Update the workflow's agent node to use a different model, or "
            "re-verify the row in Admin → LLM models if the provider has restored access."
        )
```
Remove the now-unused `get_current_db` import from `agent.py` only if nothing else in the file uses it.

- [ ] **Step 6: Convert the unit tests**

Mapping: `db.llmmodel.find_many` → `models_repo.list_models`; `find_unique(where={"id"})` → `get_by_id`; `find_unique(where={"provider_modelId"})` → `get_by_provider_model`; `create` → `create`; `update` → `update` or `record_verification`; `delete` → `delete`. Rows become `LlmModelRecord(...)` with snake_case fields. A test that made `create` raise a generic `Exception` to get 409 must raise `AlreadyExists("llm_models_provider_model_id_key")` instead. Agent tests that used `set_current_db(mock)` for the catalog lookup now `register_sessionmaker(FakeSessionmaker())` and patch `models_repo.get_by_provider_model`, and `register_sessionmaker(None)` in teardown.

- [ ] **Step 7: Run** the converted unit tests, `tests/unit/executors`, the repo test and `tests/integration/sqla_phase1/test_settings_and_models.py`. Expected: all pass.

- [ ] **Step 8: Commit** — `feat(db): LLM model catalog on SQLAlchemy (phase 1)`; mention that only a real duplicate is a 409 now.

---

### Task 5: LLM API keys

**Files:**
- Create: `src/db/repos/llm_api_keys.py`, `tests/integration/db/repos/test_llm_api_keys.py`
- Modify: `src/api/admin_llm_keys.py`, `src/api/admin_tools.py`, `src/api/admin_llm_models.py` (verify's key lookup), `src/security/key_sync.py`, `src/main.py` (key-sync call only), `src/cli/keys.py`
- Test: convert `tests/unit/api/test_admin_llm_keys.py`, `tests/unit/api/test_admin_tools.py`, `tests/unit/security/test_key_sync.py`, CLI key tests (`grep -rln "llmapikey" tests/unit`)

**Interfaces:**
- Consumes: `one_shot_session`
- Produces:
  ```python
  @dataclass(frozen=True)
  class LlmApiKeyRecord: id: str; provider: str; encrypted_key: str; key_prefix: str; created_at: datetime; updated_at: datetime
  @dataclass(frozen=True)
  class UpsertResult: record: LlmApiKeyRecord; created: bool
  async def list_keys(session) -> list[LlmApiKeyRecord]            # ORDER BY provider
  async def get(session, provider: str) -> LlmApiKeyRecord | None
  async def exists(session, provider: str) -> bool
  async def upsert(session, provider: str, *, encrypted_key: str, key_prefix: str) -> UpsertResult
  async def delete(session, provider: str) -> bool
  async def sync_llm_keys_from_db(session: AsyncSession) -> dict[str, str]   # key_sync.py, new parameter type
  ```

- [ ] **Step 1: Write the failing repository test**

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import llm_api_keys as repo

pytestmark = pytest.mark.integration
PROVIDER = "p1-test-provider"  # provider is free text in the table; the route allow-list is not involved


async def test_upsert_reports_created_then_updated(session: AsyncSession) -> None:
    first = await repo.upsert(session, PROVIDER, encrypted_key="e1", key_prefix="k1")
    second = await repo.upsert(session, PROVIDER, encrypted_key="e2", key_prefix="k2")
    assert first.created is True and second.created is False
    assert second.record.id == first.record.id and second.record.key_prefix == "k2"
    assert second.record.updated_at >= first.record.updated_at
    assert await repo.exists(session, PROVIDER) is True


async def test_delete_and_missing(session: AsyncSession) -> None:
    await repo.upsert(session, PROVIDER, encrypted_key="e", key_prefix="k")
    assert await repo.delete(session, PROVIDER) is True
    assert await repo.get(session, PROVIDER) is None and await repo.delete(session, PROVIDER) is False
```

- [ ] **Step 2: Run; expect ImportError.**

- [ ] **Step 3: Implement**

```python
"""Encrypted LLM/tool provider API keys, one row per provider."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LlmApiKey
from src.db.types import utcnow


@dataclass(frozen=True)
class LlmApiKeyRecord:
    id: str
    provider: str
    encrypted_key: str
    key_prefix: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UpsertResult:
    record: LlmApiKeyRecord
    created: bool


def _record(r: LlmApiKey) -> LlmApiKeyRecord:
    return LlmApiKeyRecord(
        id=r.id, provider=r.provider, encrypted_key=r.encrypted_key, key_prefix=r.key_prefix,
        created_at=r.created_at, updated_at=r.updated_at,
    )


async def list_keys(session: AsyncSession) -> list[LlmApiKeyRecord]:
    return [_record(r) for r in await session.scalars(select(LlmApiKey).order_by(LlmApiKey.provider))]


async def get(session: AsyncSession, provider: str) -> LlmApiKeyRecord | None:
    row = await session.scalar(select(LlmApiKey).where(LlmApiKey.provider == provider))
    return _record(row) if row else None


async def exists(session: AsyncSession, provider: str) -> bool:
    return (await session.scalar(select(LlmApiKey.id).where(LlmApiKey.provider == provider))) is not None


async def upsert(session: AsyncSession, provider: str, *, encrypted_key: str, key_prefix: str) -> UpsertResult:
    """One atomic statement. ``xmax = 0`` is true only for a freshly inserted row."""
    now = utcnow()
    stmt = (
        insert(LlmApiKey)
        .values(provider=provider, encrypted_key=encrypted_key, key_prefix=key_prefix,
                created_at=now, updated_at=now)
        .on_conflict_do_update(
            index_elements=[LlmApiKey.provider],
            set_={"encrypted_key": encrypted_key, "key_prefix": key_prefix, "updated_at": now},
        )
        .returning(LlmApiKey, literal_column("(xmax = 0)").label("inserted"))
        .execution_options(populate_existing=True)
    )
    row, inserted = (await session.execute(stmt)).one()
    return UpsertResult(record=_record(row), created=bool(inserted))


async def delete(session: AsyncSession, provider: str) -> bool:
    result = await session.execute(
        sa_delete(LlmApiKey).where(LlmApiKey.provider == provider).returning(LlmApiKey.id)
    )
    return result.first() is not None
```
(`insert(...).values(id=...)` is not needed: the model's `id_column` default supplies a UUIDv7 for the Core insert because `default=new_id` is a Python-side column default.)

- [ ] **Step 4: Run the repository test; expect PASS.** If `id` comes back NULL-constraint-failed, add `id=new_id()` to `.values(...)` (import from `src.db.ids`) and re-run.

- [ ] **Step 5: Port the callers**

- `admin_llm_keys.py`: list → `keys_repo.list_keys`; build `LlmKeySummary(provider=r.provider, key_prefix=r.key_prefix, updated_at=str(r.updated_at))`. get → `keys_repo.get`. put → `result = await keys_repo.upsert(session, provider, encrypted_key=encrypted, key_prefix=prefix)`, `await session.commit()`, keep the in-process `setattr(get_settings(), field, payload.value)` **after** the commit, return from `result.record`. test-connection → `keys_repo.get` then `decrypt(row.encrypted_key)`. delete → `if not await keys_repo.delete(...)`: 404; commit; then clear the setting.
- `admin_tools.py`: `has_db_key = await asyncio.wait_for(keys_repo.exists(session, tool_id), timeout=3.0)` inside the existing try/except; the route takes `session`.
- `admin_llm_models.py` verify: `key_row = await keys_repo.get(session, row.provider)`, `decrypt(key_row.encrypted_key)`.
- `key_sync.py`: signature `async def sync_llm_keys_from_db(session: AsyncSession) -> dict[str, str]`; replace `db.llmapikey.find_many()` with `await keys_repo.list_keys(session)` inside the same `try/except Exception`; rows use `row.provider`/`row.encrypted_key`. Remove the Prisma import.
- `main.py` lifespan: replace `await sync_llm_keys_from_db(db)` with
  ```python
  async with app.state.db_sessionmaker() as session:
      await sync_llm_keys_from_db(session)
  ```
  Change nothing else in `main.py`.
- `cli/keys.py`: every `db = Prisma(); await db.connect()` … `finally: await db.disconnect()` block becomes `async with one_shot_session(get_settings()) as session:`; list → `keys_repo.list_keys`, set → `result = await keys_repo.upsert(...)`, `await session.commit()`, print `created key …` if `result.created` else `updated key …`; delete → `if not await keys_repo.delete(...)` print the existing "no key set" message and return 1, else commit. `keys_sync` replaces its `find_many` the same way. Keep every printed message and return code identical.

- [ ] **Step 6: Convert the unit tests** — mapping: `db.llmapikey.find_many` → `keys_repo.list_keys`; `find_unique` → `keys_repo.get` (or `exists` in admin_tools); `create`/`update` → `keys_repo.upsert` returning `UpsertResult(record, created)`; `delete` → `keys_repo.delete` returning `bool`. `test_key_sync.py` passes a `FakeSession()` and patches `keys_repo.list_keys`. CLI tests patch `src.cli.keys.one_shot_session` with `lambda _s: fake_scope(FakeSession())` from `tests.unit.db_fakes`.

- [ ] **Step 7: Run** the converted unit tests, the repo test and `tests/integration/sqla_phase1/test_llm_keys.py`. Expected: PASS.

- [ ] **Step 8: Commit** — `feat(db): LLM API keys on SQLAlchemy (phase 1)`; note the in-process setting now updates only after the row is committed.

---

### Task 6: Users and auth

**Files:**
- Create: `src/db/repos/users.py`, `tests/integration/db/repos/test_users.py`
- Modify: `src/security/auth.py`, `src/api/auth_common.py`, `src/api/auth_standalone.py`, `src/api/users.py`, `src/api/events_ws.py`, `src/api/mcp_servers.py` (user lookups only), `src/api/workflows.py` (user lookups only), `src/api/test_cleanup.py` (user find/delete only), `src/migration/reconcile.py` (user lookup only), `src/cli/main.py` (reconcile call site)
- Test: convert `tests/unit/security/test_auth.py`, `tests/unit/api/test_auth_*.py`, `test_users_search.py`, `test_events_ws.py`, `test_mcp_servers.py`, `test_workflow_assignments.py`, `test_test_cleanup.py`, `tests/unit/migration/test_reconcile.py` — only the parts that fake `db.user`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class UserRecord:
      id: str; email: str; password_hash: str | None; display_name: str | None
      role: UserRole; is_active: bool; must_change_password: bool
      created_at: datetime; updated_at: datetime
  async def get_by_id(session, user_id: str) -> UserRecord | None
  async def get_by_email(session, email: str) -> UserRecord | None        # exact match; callers keep their own .lower()
  async def create(session, *, email: str, password_hash: str | None, display_name: str | None) -> UserRecord  # raises AlreadyExists
  async def list_all(session) -> list[UserRecord]                          # ORDER BY email
  async def search_active(session, q: str, limit: int) -> list[UserRecord] # ILIKE on email/display_name, ORDER BY email
  async def set_role(session, user_id: str, role: UserRole) -> UserRecord | None
  async def set_active(session, user_id: str, active: bool) -> UserRecord | None
  async def set_password(session, user_id: str, password_hash: str, *, must_change: bool) -> bool
  async def delete(session, user_id: str) -> bool
  ```

- [ ] **Step 1: Write the failing repository test**

```python
import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.models import UserRole
from src.db.repos import users as repo

pytestmark = pytest.mark.integration


def _email() -> str:
    return f"repo-{secrets.token_hex(5)}@example.com"


async def test_create_get_and_duplicate(session: AsyncSession) -> None:
    email = _email()
    u = await repo.create(session, email=email, password_hash="h", display_name=None)
    assert u.role is UserRole.MEMBER and u.is_active and not u.must_change_password
    assert await repo.get_by_email(session, email) == u and await repo.get_by_id(session, u.id) == u
    with pytest.raises(AlreadyExists):
        await repo.create(session, email=email, password_hash="h", display_name=None)


async def test_search_keeps_unescaped_wildcards_and_skips_inactive(session: AsyncSession) -> None:
    tag = secrets.token_hex(4)
    hit = await repo.create(session, email=f"x-{tag}@example.com", password_hash=None, display_name=None)
    off = await repo.create(session, email=f"y-{tag}@example.com", password_hash=None, display_name=None)
    await repo.set_active(session, off.id, False)
    assert [u.id for u in await repo.search_active(session, tag.upper(), 20)] == [hit.id]
    # Task 2 found Prisma does not escape LIKE wildcards: "_" matches any one character.
    assert [u.id for u in await repo.search_active(session, f"x_{tag}", 20)] == [hit.id]


async def test_setters(session: AsyncSession) -> None:
    u = await repo.create(session, email=_email(), password_hash="h", display_name="D")
    assert (await repo.set_role(session, u.id, UserRole.ADMIN)).role is UserRole.ADMIN  # type: ignore[union-attr]
    assert await repo.set_password(session, u.id, "h2", must_change=True) is True
    after = await repo.get_by_id(session, u.id)
    assert after is not None and after.password_hash == "h2" and after.must_change_password
    assert await repo.set_role(session, "missing", UserRole.ADMIN) is None
    assert await repo.delete(session, u.id) is True and await repo.get_by_id(session, u.id) is None
```
**Decided after Task 2:** Prisma's `contains` does not escape `_` or `%` (pinned by `test_user_search_does_not_escape_like_wildcards_today`). The port keeps that: no escaping. Changing it would be a product decision, out of scope for the migration.

- [ ] **Step 2: Run; expect ImportError.**

- [ ] **Step 3: Implement**

```python
"""Application users."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_already_exists
from src.db.models import User, UserRole
from src.db.types import utcnow


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    password_hash: str | None
    display_name: str | None
    role: UserRole
    is_active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


def _record(u: User) -> UserRecord:
    return UserRecord(
        id=u.id, email=u.email, password_hash=u.password_hash, display_name=u.display_name,
        role=u.role, is_active=u.is_active, must_change_password=u.must_change_password,
        created_at=u.created_at, updated_at=u.updated_at,
    )


def _contains_pattern(q: str) -> str:
    """Prisma's `contains` semantics: wildcards in ``q`` are NOT escaped (pinned in Task 2)."""
    return f"%{q}%"


async def get_by_id(session: AsyncSession, user_id: str) -> UserRecord | None:
    row = await session.get(User, user_id)
    return _record(row) if row else None


async def get_by_email(session: AsyncSession, email: str) -> UserRecord | None:
    row = await session.scalar(select(User).where(User.email == email))
    return _record(row) if row else None


async def create(
    session: AsyncSession, *, email: str, password_hash: str | None, display_name: str | None
) -> UserRecord:
    row = User(email=email, password_hash=password_hash, display_name=display_name)
    await add_or_already_exists(session, row)
    return _record(row)


async def list_all(session: AsyncSession) -> list[UserRecord]:
    return [_record(u) for u in await session.scalars(select(User).order_by(User.email))]


async def search_active(session: AsyncSession, q: str, limit: int) -> list[UserRecord]:
    pattern = _contains_pattern(q)
    stmt = (
        select(User)
        .where(
            User.is_active.is_(True),
            or_(User.email.ilike(pattern), User.display_name.ilike(pattern)),
        )
        .order_by(User.email)
        .limit(limit)
    )
    return [_record(u) for u in await session.scalars(stmt)]


async def _update(session: AsyncSession, user_id: str, values: dict[str, object]) -> UserRecord | None:
    stmt = (
        sa_update(User).where(User.id == user_id).values(**values, updated_at=utcnow())
        .returning(User).execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def set_role(session: AsyncSession, user_id: str, role: UserRole) -> UserRecord | None:
    return await _update(session, user_id, {"role": role})


async def set_active(session: AsyncSession, user_id: str, active: bool) -> UserRecord | None:
    return await _update(session, user_id, {"is_active": active})


async def set_password(session: AsyncSession, user_id: str, password_hash: str, *, must_change: bool) -> bool:
    return await _update(session, user_id, {"password_hash": password_hash, "must_change_password": must_change}) is not None


async def delete(session: AsyncSession, user_id: str) -> bool:
    result = await session.execute(sa_delete(User).where(User.id == user_id).returning(User.id))
    return result.first() is not None
```

- [ ] **Step 4: Run the repository test; expect PASS.**

- [ ] **Step 5: Port `src/security/auth.py`**

Replace each "if the app-wide Prisma client is available" block with the sessionmaker equivalent. The rule is unchanged: no database attached (unit tests) means no live check.
```python
from src.db.repos import users as users_repo
from src.db.runtime import request_sessionmaker


async def _live_user(request: HTTPConnection, user_id: str) -> tuple[bool, users_repo.UserRecord | None]:
    """(checked, user). checked=False when the app has no database attached."""
    sessionmaker = request_sessionmaker(request)
    if sessionmaker is None:
        return False, None
    async with sessionmaker() as session:
        return True, await users_repo.get_by_id(session, user_id)


def _ensure_active(user: users_repo.UserRecord | None) -> None:
    if user is None:
        raise AuthError("user no longer exists")
    if not user.is_active:
        raise AuthError("account is deactivated")
```
- `get_current_user_id`: `checked, user = await _live_user(request, user_id)`; if `checked`: `_ensure_active(user)` and `if user.must_change_password: raise AuthError("password change required")`.
- `get_user_id_allow_password_change`: if `checked`: `_ensure_active(user)`.
- `get_current_role`: `checked, user = …`; if not `checked`: return `(user_id, "member")`; else `role = user.role.value if user else "member"`.
- Delete `_ensure_active_user` and `_ensure_active_and_no_pending_password_change` and their Prisma imports; keep every `AuthError` message identical (the behaviour tests and unit tests assert on them).

- [ ] **Step 6: Port the user routes**

- `auth_common.py` `me`: `user = await users_repo.get_by_id(session, user_id)`; `StandaloneMeResponse(id=user.id, email=user.email, displayName=user.display_name, role=user.role.value)`. `sso_exchange`: `user = await users_repo.get_by_email(session, email)`; if None, `user = await users_repo.create(session, email=email, password_hash=None, display_name=display_name)` then `await session.commit()`; elif not `user.is_active`: raise `AuthError("account is deactivated")`.
- `auth_standalone.py`: register → keep the 409 pre-check via `get_by_email`, then `try: user = await users_repo.create(...)` / `except AlreadyExists: raise HTTPException(409, "email already registered")` (closes the race where two concurrent registrations used to 500), then commit. Login → `get_by_email`, `user.password_hash`. Refresh → `get_by_id`, `user.is_active`. Change password and reset password → `get_by_id` then `await users_repo.set_password(session, user_id, hash_password(...), must_change=False)` + commit. Forgot password → `get_by_email`. Keep every status code and detail string.
- `users.py`: `rows = await users_repo.search_active(session, q, limit)`; `UserSearchResult.model_validate(row)` works via `from_attributes` + `populate_by_name`.
- `events_ws.py`: replace the `db.user.find_unique` with
  ```python
  sessionmaker = request_sessionmaker(ws)
  user = None
  if sessionmaker is not None:
      async with sessionmaker() as session:
          user = await users_repo.get_by_id(session, user_id)
  if settings.deployment_mode == "standalone" and (user is None or not user.is_active):
      await ws.close(code=4401, reason="unauthenticated")
      return
  role = user.role.value if user is not None else None
  ```
  Leave the `workflowexecution` lookups (Phase 4) on Prisma.
- `mcp_servers.py`, `workflows.py`: each handler with a `db.user.find_unique` gains `session: AsyncSession = Depends(get_session)` and uses `users_repo.get_by_email(session, payload.email.lower())` / `users_repo.get_by_id(session, …)`. Everything else in those handlers stays on Prisma.
- `test_cleanup.py`: user find → `users_repo.get_by_id`; the final `db.user.delete` → `await users_repo.delete(session, user_id)` + `await session.commit()`, after the existing Prisma `delete_many` calls (those autocommit, so ordering is preserved).
- `migration/reconcile.py`: `reconcile_by_email(db, session, email)`; the user lookup uses `users_repo.get_by_email(session, email_lc)`. In `src/cli/main.py` wrap the call: `async with one_shot_session(get_settings()) as session: … run_reconcile(…, session=session)` — read `run_reconcile` first and thread `session` through it the same way.

- [ ] **Step 7: Convert the unit tests**

Mapping: `db.user.find_unique(where={"id"})` → `users_repo.get_by_id`; `where={"email"}` → `get_by_email`; `create` → `create`; `find_many` → `list_all` / `search_active`; `update(data={"role"})` → `set_role`; `{"isActive"}` → `set_active`; `{"passwordHash", "mustChangePassword"}` → `set_password` (returns bool); `delete` → `delete`. Fake rows become `UserRecord(...)` with `role=UserRole.MEMBER` etc. Tests that exercised the live auth checks (deactivated user → 401, pending password change → 401) set `app.state.db_sessionmaker = FakeSessionmaker()` and patch `users_repo.get_by_id`; tests that relied on "no Prisma client → member" need no change.

- [ ] **Step 8: Run** `tests/unit/security tests/unit/api tests/unit/migration`, the repo test, and `tests/integration/sqla_phase1/test_users_and_auth.py` plus the existing `tests/integration/test_standalone_auth_lifecycle.py` and `test_security_hardening.py`. Expected: PASS.

- [ ] **Step 9: Commit** — `feat(db): users and auth checks on SQLAlchemy (phase 1)`; mention the registration race now returns 409 instead of 500.

---

### Task 7: API keys and admin user management

**Files:**
- Create: `src/db/repos/api_keys.py`, `tests/integration/db/repos/test_api_keys.py`
- Modify: `src/api/api_keys.py`, `src/security/api_key_auth.py`, `src/api/admin_users.py`
- Test: convert `tests/unit/api/test_api_keys.py`, `tests/unit/api/test_admin_users.py`, `tests/unit/api/test_run_external.py` (the `db.apikey`/`db.user` parts)

**Interfaces:**
- Consumes: `users_repo`, `ensure_utc`, `utcnow`
- Produces:
  ```python
  @dataclass(frozen=True)
  class ApiKeyRecord:
      id: str; user_id: str; label: str; key_hash: str; key_prefix: str
      created_at: datetime; last_used_at: datetime | None; expires_at: datetime | None; revoked_at: datetime | None
  async def create(session, *, user_id: str, label: str, key_hash: str, key_prefix: str, expires_at: datetime | None) -> ApiKeyRecord
  async def list_for_user(session, user_id: str) -> list[ApiKeyRecord]          # ORDER BY created_at DESC
  async def get(session, key_id: str) -> ApiKeyRecord | None
  async def first_by_prefix(session, prefix: str) -> ApiKeyRecord | None        # ORDER BY created_at, id
  async def revoke_if_active(session, key_id: str, at: datetime) -> bool        # guarded: only when revoked_at IS NULL
  async def revoke_all_active_for_user(session, user_id: str, at: datetime) -> int
  async def touch_last_used(session, key_id: str, at: datetime) -> None
  ```

- [ ] **Step 1: Write the failing repository tests, including a race test**

```python
import asyncio
import secrets

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import User
from src.db.repos import api_keys as repo
from src.db.repos import users as users_repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


async def _user(session: AsyncSession) -> str:
    u = await users_repo.create(session, email=f"k-{secrets.token_hex(5)}@example.com", password_hash=None, display_name=None)
    return u.id


async def test_create_list_and_revoke_once(session: AsyncSession) -> None:
    uid = await _user(session)
    a = await repo.create(session, user_id=uid, label="a", key_hash="h1", key_prefix="ck_aaaa", expires_at=None)
    b = await repo.create(session, user_id=uid, label="b", key_hash="h2", key_prefix="ck_bbbb", expires_at=None)
    assert {k.id for k in await repo.list_for_user(session, uid)} == {a.id, b.id}
    assert await repo.first_by_prefix(session, "ck_aaaa") == a
    now = utcnow()
    assert await repo.revoke_if_active(session, a.id, now) is True
    assert await repo.revoke_if_active(session, a.id, now) is False
    assert await repo.revoke_all_active_for_user(session, uid, now) == 1
    await repo.touch_last_used(session, b.id, now)
    got = await repo.get(session, b.id)
    assert got is not None and got.last_used_at == now and got.revoked_at == now


async def test_concurrent_revocations_have_exactly_one_winner(engine: AsyncEngine) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        uid = await _user(s)
        key = await repo.create(s, user_id=uid, label="r", key_hash="h", key_prefix="ck_race", expires_at=None)
        await s.commit()
    try:
        async def attempt() -> bool:
            async with sm() as s:
                won = await repo.revoke_if_active(s, key.id, utcnow())
                await s.commit()
                return won
        results = await asyncio.gather(*(attempt() for _ in range(5)))
        assert results.count(True) == 1
    finally:
        async with sm() as s:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()
```

- [ ] **Step 2: Run; expect ImportError.**

- [ ] **Step 3: Implement**

```python
"""Hashed per-user API keys ('ck_…') for external workflow invocation."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ApiKey


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    user_id: str
    label: str
    key_hash: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


def _record(k: ApiKey) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=k.id, user_id=k.user_id, label=k.label, key_hash=k.key_hash, key_prefix=k.key_prefix,
        created_at=k.created_at, last_used_at=k.last_used_at, expires_at=k.expires_at,
        revoked_at=k.revoked_at,
    )


async def create(
    session: AsyncSession, *, user_id: str, label: str, key_hash: str, key_prefix: str,
    expires_at: datetime | None,
) -> ApiKeyRecord:
    row = ApiKey(user_id=user_id, label=label, key_hash=key_hash, key_prefix=key_prefix, expires_at=expires_at)
    session.add(row)
    await session.flush()
    return _record(row)


async def list_for_user(session: AsyncSession, user_id: str) -> list[ApiKeyRecord]:
    stmt = select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    return [_record(k) for k in await session.scalars(stmt)]


async def get(session: AsyncSession, key_id: str) -> ApiKeyRecord | None:
    row = await session.get(ApiKey, key_id)
    return _record(row) if row else None


async def first_by_prefix(session: AsyncSession, prefix: str) -> ApiKeyRecord | None:
    """Prisma's find_first had no order; this one is deterministic (oldest first)."""
    stmt = select(ApiKey).where(ApiKey.key_prefix == prefix).order_by(ApiKey.created_at, ApiKey.id).limit(1)
    row = await session.scalar(stmt)
    return _record(row) if row else None


async def revoke_if_active(session: AsyncSession, key_id: str, at: datetime) -> bool:
    stmt = (
        sa_update(ApiKey).where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=at).returning(ApiKey.id)
    )
    return (await session.execute(stmt)).first() is not None


async def revoke_all_active_for_user(session: AsyncSession, user_id: str, at: datetime) -> int:
    stmt = (
        sa_update(ApiKey).where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=at).returning(ApiKey.id)
    )
    return len((await session.execute(stmt)).all())


async def touch_last_used(session: AsyncSession, key_id: str, at: datetime) -> None:
    await session.execute(sa_update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=at))
```

- [ ] **Step 4: Run the repository tests; expect PASS.**

- [ ] **Step 5: Port the callers**

- `api_keys.py` create: `expires_at = ensure_utc(payload.expires_at) if payload.expires_at else None` (keeps today's acceptance of a naive `expiresAt`, which `UTCDateTime` would otherwise reject with a 500); `row = await keys_repo.create(...)`; commit; build the response from `row.id`, `row.label`, `row.key_prefix`, `row.created_at`, `row.expires_at`. List: `[ApiKeySummary.model_validate(k) for k in await keys_repo.list_for_user(session, user_id)]`. Revoke: `row = await keys_repo.get(...)`; 404 if missing or `row.user_id != user_id`; `if row.revoked_at is None: await keys_repo.revoke_if_active(session, key_id, utcnow()); await session.commit()`.
- `api_key_auth.py`:
  ```python
  row = await keys_repo.first_by_prefix(session, prefix)
  if row is None or row.revoked_at is not None:
      raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
  if row.expires_at is not None and row.expires_at < utcnow():
      raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")
  if not verify_api_key(token, row.key_hash):
      raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
  try:
      await keys_repo.touch_last_used(session, row.id, utcnow())
      await session.commit()
  except Exception:
      await session.rollback()  # best effort, as before; keep the session usable
  user = await users_repo.get_by_id(session, row.user_id)
  if user is None or not user.is_active:
      raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key owner is inactive")
  return ApiKeyAuthResult(user_id=row.user_id, role=user.role.value, api_key_id=row.id)
  ```
  The dependency takes `session: AsyncSession = Depends(get_session)`; FastAPI gives the route the same session object within one request.
- `admin_users.py`: `_summary_from_row(row: UserRecord)` → `UserSummary(id=row.id, email=row.email, role=row.role.value, displayName=row.display_name, isActive=row.is_active)`. list → `users_repo.list_all`. role → `users_repo.set_role(session, user_id, UserRole(payload.role))`, 404 on None, commit. deactivate → 404 check via `get_by_id`, then **one transaction**: `await keys_repo.revoke_all_active_for_user(session, user_id, utcnow())` and `updated = await users_repo.set_active(session, user_id, False)`, then a single `await session.commit()` (Prisma ran these as two separate writes). reactivate → `set_active(..., True)` + commit. admin revoke → `get` + owner check + `revoke_if_active` + commit. reset-password → `get_by_id` 404 check, `set_password(..., must_change=True)` + commit.

- [ ] **Step 6: Convert the unit tests** — mapping: `db.apikey.create` → `keys_repo.create`; `find_many` → `list_for_user`; `find_unique` → `get`; `find_first` → `first_by_prefix`; `update(data={"revokedAt"})` → `revoke_if_active`; `update(data={"lastUsedAt"})` → `touch_last_used`; `update_many` → `revoke_all_active_for_user`; plus the user mapping from Task 6. Add one new unit test: `POST /api-keys` with a naive `expiresAt` passes an **aware** UTC datetime to `keys_repo.create` (assert on `await_args.kwargs["expires_at"].tzinfo is UTC`). Add one for deactivate: both repo calls happen and `session.commit.await_count == 1`.

- [ ] **Step 7: Run** the converted unit tests, the repo tests, `tests/integration/sqla_phase1/test_api_keys.py`, `test_users_and_auth.py` and `tests/integration/test_external_invoke.py`. Expected: PASS.

- [ ] **Step 8: Commit** — `feat(db): API keys and admin user management on SQLAlchemy (phase 1)`; mention deactivation (revoke keys + mark inactive) is now one transaction.

---

### Task 8: Enforce the boundary, update docs, run everything, open the PR

**Files:**
- Modify: `tests/unit/db/test_prisma_import_boundary.py` (`MIGRATED_PATHS`), `CHANGELOG.md`, `docs/architecture.md` (data-access section), `docs/superpowers/specs/2026-11-20-prisma-to-sqlalchemy-migration-design.md` (phase table: mark 1 done), `docs/superpowers/plans/2026-11-20-sqlalchemy-phase-1-settings-and-admin.md` (tick boxes)

- [ ] **Step 1: Grow the allow-list and watch it pass**

Set:
```python
MIGRATED_PATHS = [
    "src/db",
    "src/api/admin_deployment_settings.py",
    "src/api/admin_llm_keys.py",
    "src/api/admin_llm_models.py",
    "src/api/admin_tools.py",
    "src/api/admin_users.py",
    "src/api/api_keys.py",
    "src/api/auth_common.py",
    "src/api/auth_standalone.py",
    "src/api/llm_models.py",
    "src/api/llm_models_live.py",
    "src/api/users.py",
    "src/builder/model_policy.py",
    "src/cli/keys.py",
    "src/security/api_key_auth.py",
    "src/security/auth.py",
    "src/security/key_sync.py",
]
```
Run `.venv/Scripts/python -m pytest --no-cov -q tests/unit/db/test_prisma_import_boundary.py`. Any offender means a leftover `prisma` or `src.storage` import (often `from src.storage.db import get_db`): remove it. Then confirm no Phase 1 table is still queried through Prisma anywhere:
`grep -rnE '\b(db|prisma)\.(user|llmmodel|llmapikey|apikey|deploymentsetting)\.' src` → expected: no output.

- [ ] **Step 2: Docs**

- `CHANGELOG.md` `[Unreleased]`: `### Changed — SQLAlchemy migration phase 1: settings and admin (2026-11-20)`, listing: the five tables now go through `src/db/repos/`; behaviour unchanged, proven by API tests run on Prisma first; three deliberate fixes (only real duplicates give 409 on LLM models; concurrent registration gives 409 instead of 500; user deactivation is one transaction); `check_database()` added for the readiness probe.
- `docs/architecture.md`: in the data-access section, one paragraph on repository modules and `src/db/runtime.py`, and that Phase 1 tables are on SQLAlchemy.
- Spec §3.3: append "(done: PR #…)" to phase 1 once the PR exists.

- [ ] **Step 3: Run everything CI runs**

```bash
.venv/Scripts/python -m ruff check src tests && .venv/Scripts/python -m ruff format --check src tests \
  && .venv/Scripts/python -m pyright src tests \
  && .venv/Scripts/python -m pytest --no-cov -q -m "not integration" \
  && .venv/Scripts/python -m pytest --no-cov -q -m "integration and not live"
```
Expected: all green (integration with the container up). Stop and delete the container afterwards: `docker stop composer-p1-pg`.

- [ ] **Step 4: Commit, push, open the PR**

```bash
git add -A tests/unit/db/test_prisma_import_boundary.py CHANGELOG.md docs
git commit -m "chore(db): lock phase 1 areas to SQLAlchemy and document the move

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/sqlalchemy-phase-1
gh pr create --base main --title "feat(db): settings and admin on SQLAlchemy (migration phase 1)" --body-file <prepared body ending with the Claude Code attribution line>
```
Wait for all six CI checks. **Do not merge**: merging deploys to production and needs Balaji's go-ahead. After merge, the spec's done criterion 4 applies: watch the deploy and backend logs; phase 2 may start the next day.

---

## Self-review notes (resolved while writing)

- Spec §2.1 rules → Global Constraints; §3.1 rule 3 (area moves whole) → mixed-file reads in Tasks 3–6; §4.5 `AlreadyExists` → Task 1; §5 testing layers → repository, race (Task 7), behaviour (Task 2) and unit tests; §5.1 criterion 1 → Task 8 Step 1.
- Phase 0 carry-over: settings after env override (the `app` fixture already clears the cache; Task 2 relies on it); Json `json_serializer` (no Phase 1 table has a JSON column, so not needed yet, re-check in Phase 2); compare against bound `utcnow()` (API-key expiry in Task 7); relationships `lazy="raise"` (none added); `MIGRATED_PATHS` (Task 8); timestamp rounding (all writes use `utcnow()`, which is already whole milliseconds); deploy `concurrency:` group (owner decision, not in this plan).
- Types used across tasks: `SettingRecord`, `LlmModelRecord`, `LlmApiKeyRecord`/`UpsertResult`, `UserRecord`, `ApiKeyRecord`; `UNSET`/`Unset`; `FakeSession`, `FakeSessionmaker`, `fake_scope`, `override_session`. Names match between Interfaces blocks and code.
