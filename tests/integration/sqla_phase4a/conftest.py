"""Fixtures for the SQLAlchemy phase 4a behaviour oracle (executions, events, rate limits).

These tests describe what the code does TODAY (Prisma-backed) and must keep passing unchanged
while ``workflow_executions``, ``execution_events`` and ``rate_limit_buckets`` move to
SQLAlchemy. The only code later tasks may edit is the ADAPTER AREA: the ``Phase4aAdapter`` class
and the "ADAPTER AREA imports" block below. The two tests listed in the module docstrings of
``test_events.py`` (retention gap, concurrent appends) and ``test_rate_limit.py`` (concurrent
over-admission) pin TODAY'S BUGS (plan decisions D2, D3) and are the only tests a later task may
change, in the way the plan's Task 3 describes.

Seams that do not change across the port:
* ``src.api.executions.enqueue_execution``, ``src.api.run.enqueue_execution`` and
  ``src.api.internal.enqueue_execution``: monkeypatched per test (``EnqueueRecorder``), or left at
  the default autouse driver from ``tests/integration/conftest.py`` which runs claim-and-run over
  ASGI.
* ``src.api.internal.LangGraphExecutor``: replaced by ``FakeExecutor`` when a test is about the
  claim, not the graph. The fake reads the row through a SEPARATE SQLAlchemy session inside
  ``run``, which is how the oracle proves the lease committed before the run.
* ``POST /internal/claim-and-run`` and ``POST /internal/sweep`` over HTTP (``ENVIRONMENT=
  development`` and ``CLOUD_TASKS_SERVICE_ACCOUNT`` unset: the OIDC check is a no-op).
* Everything else that is not HTTP goes through ``Phase4aAdapter``.

Network is never real: ``httpx_mock`` is requested for every test. Loopback is excluded (Prisma's
query engine is an HTTP server on localhost); the ASGI test client is never intercepted.
"""

import itertools
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

# ADAPTER AREA imports: exception classes that tests assert on are re-exported from here, so a
# rename in src only touches this block and the Phase4aAdapter bodies. Tests import them from this
# module, never from src.
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.errors import AlreadyExists
from src.db.models import (
    Approval,
    LangGraphCheckpoint,
    LangGraphCheckpointWrite,
    User,
    UserRole,
    Workflow,
    WorkflowAssignment,
    WorkflowExecution,
)
from src.db.models import (
    ExecutionEvent as ExecutionEventRow,
)
from src.main import create_app
from tests._rate_limit_reset import reset_rate_limit_buckets


class DuplicateKey(Exception):
    """Raised by ``Phase4aAdapter.start_execution`` for a unique violation (today
    ``prisma.errors.UniqueViolationError``, after the port ``AlreadyExists``)."""


DUPLICATE_KEY_ERRORS: tuple[type[BaseException], ...] = (DuplicateKey,)
# END ADAPTER AREA imports

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|\+00:00)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


def api_dt(value: str) -> datetime:
    """Parse an API timestamp into an aware UTC datetime, asserting TODAY'S wire format: an
    ISO-8601 string carrying an explicit UTC suffix (observed, see TS_SUFFIX)."""
    assert ISO.match(value), value
    assert value.endswith(TS_SUFFIX), value
    assert re.search(r":\d\d(\.\d{6})?Z$", value), (
        value
    )  # optional microsecond fraction (absent at 0 us)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def ms(value: datetime) -> datetime:
    """Round to millisecond precision the way Postgres does for the timestamp(3) columns."""
    total = value.microsecond + 500
    return value.replace(microsecond=0) + timedelta(microseconds=total // 1000 * 1000)


TS_SUFFIX = "Z"

EXECUTION_KEYS = {
    "id",
    "workflowId",
    "userId",
    "status",
    "currentNodeId",
    "nodeResults",
    "variables",
    "input",
    "output",
    "error",
    "failureCode",
    "startedAt",
    "completedAt",
    "threadId",
}


def _mock_everything_but_loopback(request: httpx.Request) -> bool:
    return request.url.host not in {"localhost", "127.0.0.1"}


HTTPX_MARK = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    should_mock=_mock_everything_but_loopback,
)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def jbody(resp: httpx.Response) -> dict[str, Any]:
    return cast("dict[str, Any]", resp.json())


def unique(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}"


def ago(**kw: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


def not_found(execution_id: str) -> str:
    return f"Execution {execution_id!r} not found."


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]
    token: str


MakeUser = Callable[..., Awaitable[Member]]


# --------------------------------------------------------------------------- graph factories


def start_node(node_id: str = "s", **data: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "start",
        "position": {"x": 0, "y": 0},
        "data": {"label": "Start", **data},
    }


def end_node(node_id: str = "e") -> dict[str, Any]:
    return {"id": node_id, "type": "end", "position": {"x": 400, "y": 0}, "data": {"label": "End"}}


def node(node_id: str, node_type: str, **data: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 200, "y": 0},
        "data": {"label": node_id, **data},
    }


def edge(source: str, target: str, edge_id: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": edge_id or f"{source}-{target}", "source": source, "target": target, **extra}


def chain(*middle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """start -> middle... -> end as (nodes, edges)."""
    nodes = [start_node(), *middle, end_node()]
    ids = [n["id"] for n in nodes]
    return nodes, [edge(a, b) for a, b in itertools.pairwise(ids)]


def approval_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes = [
        start_node(),
        node(
            "ua",
            "user-approval",
            approvalMessage="Please approve",
            approverEmail="approver@example.com",
        ),
        node("ok", "set-state", stateKey="result", stateValue="approved"),
        node("no", "set-state", stateKey="result", stateValue="rejected"),
        end_node(),
    ]
    edges = [
        edge("s", "ua"),
        edge("ua", "ok", branch="approved"),
        edge("ua", "no", branch="rejected"),
        edge("ok", "e"),
        edge("no", "e"),
    ]
    return nodes, edges


# --------------------------------------------------------------------------- environment


@pytest.fixture
def phase4a_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Standalone mode, known secrets, development environment (OIDC bypass for /internal/*)."""
    import os

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "ci-test-only-not-a-real-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.delenv("CLOUD_TASKS_SERVICE_ACCOUNT", raising=False)
    # Executions run real graphs here: never ship their traces to LangSmith from a dev shell.
    for name in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(name, "false")
    for name in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def app(phase4a_env: None) -> AsyncIterator[FastAPI]:
    """Overrides tests/conftest.py's `app` so the phase 4a env is applied before create_app()."""
    get_settings.cache_clear()
    _app = create_app()
    async with _app.router.lifespan_context(_app):
        yield _app


@pytest.fixture(autouse=True)
def _network_lock(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnusedFunction]
    """Request httpx_mock everywhere: any outbound request without a registered mock fails."""
    del httpx_mock


@pytest_asyncio.fixture(autouse=True)
async def _clean_buckets_after() -> AsyncIterator[None]:  # pyright: ignore[reportUnusedFunction]
    """The integration conftest clears buckets BEFORE each test; clear after the last one too."""
    yield
    await reset_rate_limit_buckets()


@pytest_asyncio.fixture
async def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)


# --------------------------------------------------------------------------- users + cleanup


async def purge_execution_artifacts(
    sm: async_sessionmaker[AsyncSession],
    *,
    user_ids: list[str],
    execution_ids: list[str] | None = None,
    thread_ids: list[str] | None = None,
) -> None:
    """Delete every execution of these users (or their workflows) plus events, checkpoint rows in
    both checkpoint tables and approvals, which have no cascading FK from the execution."""
    async with sm() as s:
        wf_ids = select(Workflow.id).where(Workflow.user_id.in_(user_ids))
        rows = (
            await s.execute(
                select(WorkflowExecution.id, WorkflowExecution.thread_id).where(
                    or_(
                        WorkflowExecution.workflow_id.in_(wf_ids),
                        WorkflowExecution.user_id.in_(user_ids),
                        WorkflowExecution.id.in_(execution_ids or []),
                    )
                )
            )
        ).all()
        ids = {r.id for r in rows} | set(execution_ids or [])
        threads = {r.thread_id for r in rows} | set(thread_ids or [])
        await s.execute(delete(ExecutionEventRow).where(ExecutionEventRow.execution_id.in_(ids)))
        # All five checkpoint tables (runs now write the official saver's tables too).
        from src.db.repos import checkpoints as checkpoints_repo

        await checkpoints_repo.delete_threads(s, list(threads))
        await s.execute(delete(Approval).where(Approval.execution_id.in_(ids)))
        await s.execute(delete(WorkflowExecution).where(WorkflowExecution.id.in_(ids)))
        await s.commit()


@pytest_asyncio.fixture
async def make_user(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[MakeUser]:
    made: list[str] = []

    async def _make(*, email: str | None = None) -> Member:
        email = email or f"p4a-{secrets.token_hex(6)}@example.com"
        # register is capped at 5/min per client address; setup of many users must never trip it
        await reset_rate_limit_buckets()
        password = "correct-horse-battery-staple"
        resp = await client.post("/auth/register", json={"email": email, "password": password})
        assert resp.status_code == 201, resp.text
        body = jbody(resp)
        m = Member(body["id"], email, password, bearer(body["accessToken"]), body["accessToken"])
        made.append(m.id)
        return m

    yield _make
    await purge_execution_artifacts(sessionmaker_, user_ids=made)
    async with sessionmaker_() as s:
        await s.execute(delete(Workflow).where(Workflow.user_id.in_(made)))
        await s.execute(
            delete(WorkflowAssignment).where(
                or_(
                    WorkflowAssignment.user_id.in_(made),
                    WorkflowAssignment.assigned_by_id.in_(made),
                )
            )
        )
        for uid in made:
            await s.execute(delete(User).where(User.id == uid))
        await s.commit()


@pytest_asyncio.fixture
async def member(make_user: MakeUser) -> Member:
    return await make_user()


@pytest_asyncio.fixture
async def second_member(make_user: MakeUser) -> Member:
    return await make_user()


@pytest_asyncio.fixture
async def third_member(make_user: MakeUser) -> Member:
    return await make_user()


@pytest_asyncio.fixture
async def admin(make_user: MakeUser, sessionmaker_: async_sessionmaker[AsyncSession]) -> Member:
    m = await make_user()
    async with sessionmaker_() as s:
        await s.execute(update(User).where(User.id == m.id).values(role=UserRole.ADMIN))
        await s.commit()
    return m


# --------------------------------------------------------------------------- DB seed / inspect


class Db:
    """SQLAlchemy helpers for test setup and inspection of the execution tables."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sm
        self.tracked_executions: list[str] = []
        self.tracked_threads: list[str] = []

    @staticmethod
    def snapshot(row: Any) -> dict[str, Any]:
        """Every column of an ORM row, for byte-identical before/after comparisons."""
        return {c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs}

    # ---- workflows

    async def seed_workflow(
        self,
        owner: Member | str | None,
        *,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        **columns: Any,
    ) -> str:
        if nodes is None:
            nodes, edges = chain()
        user_id = owner.id if isinstance(owner, Member) else owner
        async with self.sm() as s:
            wf = Workflow(
                user_id=user_id,
                name=columns.pop("name", unique("wf")),
                nodes=nodes,
                edges=edges or [],
                **columns,
            )
            s.add(wf)
            await s.commit()
            return wf.id

    async def publish_directly(self, workflow_id: str, slug: str) -> None:
        async with self.sm() as s:
            await s.execute(
                update(Workflow)
                .where(Workflow.id == workflow_id)
                .values(is_production=True, external_slug=slug)
            )
            await s.commit()

    async def assign(self, workflow_id: str, user_id: str, assigned_by: str) -> None:
        async with self.sm() as s:
            s.add(
                WorkflowAssignment(
                    workflow_id=workflow_id, user_id=user_id, assigned_by_id=assigned_by
                )
            )
            await s.commit()

    # ---- executions

    async def seed_execution(
        self,
        workflow_id: str,
        user_id: str | Member | None,
        *,
        status: str = "completed",
        **columns: Any,
    ) -> str:
        uid = user_id.id if isinstance(user_id, Member) else user_id
        columns.setdefault("node_results", {})
        columns.setdefault("variables", {})
        columns.setdefault("thread_id", unique("thread"))
        async with self.sm() as s:
            row = WorkflowExecution(workflow_id=workflow_id, user_id=uid, status=status, **columns)
            s.add(row)
            await s.commit()
            self.tracked_executions.append(row.id)
            self.tracked_threads.append(row.thread_id)
            return row.id

    async def execution(self, execution_id: str) -> WorkflowExecution | None:
        async with self.sm() as s:
            return await s.scalar(
                select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            )

    async def must(self, execution_id: str) -> WorkflowExecution:
        row = await self.execution(execution_id)
        assert row is not None, f"execution {execution_id} missing"
        return row

    async def set_execution(self, execution_id: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(
                update(WorkflowExecution)
                .where(WorkflowExecution.id == execution_id)
                .values(**values)
            )
            await s.commit()

    async def executions_of(self, workflow_id: str) -> list[WorkflowExecution]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(WorkflowExecution)
                        .where(WorkflowExecution.workflow_id == workflow_id)
                        .order_by(WorkflowExecution.started_at)
                    )
                ).all()
            )

    async def count_executions(self, ids: list[str]) -> int:
        async with self.sm() as s:
            return (
                await s.scalar(
                    select(func.count())
                    .select_from(WorkflowExecution)
                    .where(WorkflowExecution.id.in_(ids))
                )
            ) or 0

    async def raw(self, sql: str, **params: Any) -> list[Any]:
        async with self.sm() as s:
            return list((await s.execute(text(sql), params)).all())

    # ---- events

    async def add_events(
        self,
        execution_id: str,
        specs: list[tuple[int, str, dict[str, Any]]],
        *,
        created_at: datetime | None = None,
    ) -> None:
        async with self.sm() as s:
            for seq, type_, payload in specs:
                row = ExecutionEventRow(
                    execution_id=execution_id, seq=seq, type=type_, payload=payload
                )
                if created_at is not None:
                    row.created_at = created_at
                s.add(row)
            await s.commit()
        self.tracked_executions.append(execution_id)

    async def events(self, execution_id: str) -> list[ExecutionEventRow]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(ExecutionEventRow)
                        .where(ExecutionEventRow.execution_id == execution_id)
                        .order_by(ExecutionEventRow.seq)
                    )
                ).all()
            )

    async def event_seqs(self, execution_id: str) -> list[int]:
        return [e.seq for e in await self.events(execution_id)]

    async def event_types(self, execution_id: str) -> list[str]:
        return [e.type for e in await self.events(execution_id)]

    async def event_count(self, execution_ids: list[str]) -> int:
        async with self.sm() as s:
            return (
                await s.scalar(
                    select(func.count())
                    .select_from(ExecutionEventRow)
                    .where(ExecutionEventRow.execution_id.in_(execution_ids))
                )
            ) or 0

    # ---- checkpoints (both tables) and approvals

    async def add_checkpoint(self, thread_id: str) -> None:
        async with self.sm() as s:
            s.add(
                LangGraphCheckpoint(
                    thread_id=thread_id,
                    checkpoint_ns="",
                    checkpoint_id=unique("ckpt"),
                    checkpoint=b"c",
                    metadata_=b"m",
                )
            )
            s.add(
                LangGraphCheckpointWrite(
                    thread_id=thread_id,
                    checkpoint_ns="",
                    checkpoint_id=unique("ckpt"),
                    task_id="t",
                    idx=0,
                    channel="ch",
                    value=b"v",
                )
            )
            await s.commit()
        self.tracked_threads.append(thread_id)

    async def checkpoint_counts(self, thread_id: str) -> tuple[int, int]:
        async with self.sm() as s:
            a = await s.scalar(
                select(func.count())
                .select_from(LangGraphCheckpoint)
                .where(LangGraphCheckpoint.thread_id == thread_id)
            )
            b = await s.scalar(
                select(func.count())
                .select_from(LangGraphCheckpointWrite)
                .where(LangGraphCheckpointWrite.thread_id == thread_id)
            )
            return a or 0, b or 0

    async def add_approval(self, execution_id: str, node_id: str = "ua") -> str:
        from src.db.models import ApprovalDecision

        async with self.sm() as s:
            row = Approval(
                execution_id=execution_id, node_id=node_id, decision=ApprovalDecision.APPROVED
            )
            s.add(row)
            await s.commit()
            return row.id

    async def approval_count(self, execution_id: str) -> int:
        async with self.sm() as s:
            return (
                await s.scalar(
                    select(func.count())
                    .select_from(Approval)
                    .where(Approval.execution_id == execution_id)
                )
            ) or 0

    # ---- rate limit buckets

    async def bucket(self, route_key: str, client_key: str) -> list[tuple[float, datetime]]:
        """(tokens, last_refill as an aware UTC datetime) of the matching bucket rows."""
        rows = await self.raw(
            "SELECT tokens, last_refill FROM rate_limit_buckets "
            "WHERE route_key = :r AND client_key = :c",
            r=route_key,
            c=client_key,
        )
        return [(float(r[0]), r[1].replace(tzinfo=r[1].tzinfo or UTC)) for r in rows]

    async def seed_bucket(
        self, route_key: str, client_key: str, tokens: float, last_refill: datetime
    ) -> None:
        async with self.sm() as s:
            await s.execute(
                text(
                    "INSERT INTO rate_limit_buckets (id, route_key, client_key, tokens, last_refill) "
                    "VALUES (:i, :r, :c, :t, :l)"
                ),
                {
                    "i": unique("b"),
                    "r": route_key,
                    "c": client_key,
                    "t": tokens,
                    "l": last_refill.astimezone(UTC).replace(tzinfo=None),
                },
            )
            await s.commit()

    async def backdate_bucket(self, route_key: str, client_key: str, last_refill: datetime) -> None:
        async with self.sm() as s:
            await s.execute(
                text(
                    "UPDATE rate_limit_buckets SET last_refill = :l "
                    "WHERE route_key = :r AND client_key = :c"
                ),
                {
                    "l": last_refill.astimezone(UTC).replace(tzinfo=None),
                    "r": route_key,
                    "c": client_key,
                },
            )
            await s.commit()


@pytest_asyncio.fixture
async def db(sessionmaker_: async_sessionmaker[AsyncSession]) -> AsyncIterator[Db]:
    helper = Db(sessionmaker_)
    yield helper
    ids = list(helper.tracked_executions)
    threads = list(helper.tracked_threads)
    async with sessionmaker_() as s:
        await s.execute(delete(ExecutionEventRow).where(ExecutionEventRow.execution_id.in_(ids)))
        # All five checkpoint tables (runs now write the official saver's tables too).
        from src.db.repos import checkpoints as checkpoints_repo

        await checkpoints_repo.delete_threads(s, list(threads))
        await s.execute(delete(Approval).where(Approval.execution_id.in_(ids)))
        await s.execute(delete(WorkflowExecution).where(WorkflowExecution.id.in_(ids)))
        await s.commit()


# --------------------------------------------------------------------------- seams


@dataclass
class EnqueueRecorder:
    """Replacement for ``enqueue_execution``: records calls, optionally runs a hook first and/or
    raises."""

    calls: list[tuple[str, str]] = field(default_factory=lambda: [])
    before: Callable[[str], Awaitable[None]] | None = None
    raises: Exception | None = None

    async def __call__(
        self, execution_id: str, *, kind: str, db: object = None, **_kwargs: object
    ) -> None:
        self.calls.append((execution_id, kind))
        if self.before is not None:
            await self.before(execution_id)
        if self.raises is not None:
            raise self.raises


def install_enqueue(monkeypatch: pytest.MonkeyPatch, rec: EnqueueRecorder) -> EnqueueRecorder:
    monkeypatch.setattr("src.api.executions.enqueue_execution", rec)
    monkeypatch.setattr("src.api.run.enqueue_execution", rec)
    monkeypatch.setattr("src.api.internal.enqueue_execution", rec)
    return rec


def install_email_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the executor's approval email send (a seam that does not change in the port)."""
    sent: list[dict[str, Any]] = []

    async def _send(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr("src.engine.langgraph_executor.send_approval_email", _send)
    return sent


@dataclass
class FakeExecutorLog:
    runs: list[str] = field(default_factory=lambda: [])
    resumes: list[tuple[str, str]] = field(default_factory=lambda: [])
    seen: list[dict[str, Any]] = field(default_factory=lambda: [])
    on_run: Callable[[str], Awaitable[None]] | None = None
    on_resume: Callable[[str, str], Awaitable[None]] | None = None


def install_fake_executor(
    monkeypatch: pytest.MonkeyPatch, sm: async_sessionmaker[AsyncSession]
) -> FakeExecutorLog:
    """Replace ``src.api.internal.LangGraphExecutor`` with a fake that snapshots the row through
    a separate session when the claim hands over (proving the lease committed before the run)."""
    log = FakeExecutorLog()

    async def _observe(execution_id: str) -> None:
        async with sm() as s:
            row = await s.scalar(
                select(WorkflowExecution).where(WorkflowExecution.id == execution_id)
            )
            assert row is not None
            log.seen.append(Db.snapshot(row))

    class _FakeExecutor:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        async def run(self, execution_id: str, *, attempt_id: str | None = None) -> None:
            await _observe(execution_id)
            log.runs.append(execution_id)
            if log.on_run is not None:
                await log.on_run(execution_id)

        async def resume(
            self, execution_id: str, decision: str, *, attempt_id: str | None = None
        ) -> None:
            await _observe(execution_id)
            log.resumes.append((execution_id, decision))
            if log.on_resume is not None:
                await log.on_resume(execution_id, decision)

    monkeypatch.setattr("src.api.internal.LangGraphExecutor", _FakeExecutor)
    return log


# --------------------------------------------------------------------------- API helpers


@dataclass
class ExecApi:
    client: AsyncClient

    async def create(
        self,
        who: Member,
        workflow_id: str,
        run_input: Any = "hello",
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        body: dict[str, Any] = {"workflowId": workflow_id, "input": run_input}
        if idempotency_key is not None:
            body["idempotencyKey"] = idempotency_key
        return await self.client.post("/executions", json=body, headers=who.headers)

    async def get(self, who: Member, execution_id: str) -> httpx.Response:
        return await self.client.get(f"/executions/{execution_id}", headers=who.headers)

    async def cancel(self, who: Member, execution_id: str) -> httpx.Response:
        return await self.client.post(f"/executions/{execution_id}/cancel", headers=who.headers)

    async def delete(self, who: Member, execution_id: str) -> httpx.Response:
        return await self.client.delete(f"/executions/{execution_id}", headers=who.headers)

    async def claim(
        self, execution_id: str, kind: str = "run", headers: dict[str, str] | None = None
    ) -> httpx.Response:
        return await self.client.post(
            "/internal/claim-and-run",
            json={"executionId": execution_id, "kind": kind},
            headers=headers,
        )


@pytest.fixture
def exec_api(client: AsyncClient) -> ExecApi:
    return ExecApi(client)


async def mint_api_key(client: AsyncClient, who: Member) -> dict[str, str]:
    """A per-user external-invoke API key (`ck_...`) as an Authorization header."""
    resp = await client.post("/api-keys", json={"label": "p4a"}, headers=who.headers)
    assert resp.status_code == 201, resp.text
    key = jbody(resp)["key"]
    assert key.startswith("ck_")
    return bearer(key)


async def publish(
    client: AsyncClient, who: Member, workflow_id: str, slug: str, name: str = "wf"
) -> None:
    nodes, edges = chain()
    resp = await client.put(
        f"/workflows/{workflow_id}",
        json={
            "name": name,
            "nodes": nodes,
            "edges": edges,
            "isProduction": True,
            "externalSlug": slug,
        },
        headers=who.headers,
    )
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- service adapter


class Phase4aAdapter:
    """THE ONLY CODE LATER TASKS MAY EDIT in this package: the bodies of these methods (and the
    ADAPTER AREA imports at the top of this file).

    Entry points that are not plain HTTP endpoints, or whose seams change in phase 4a
    (``db: Prisma`` -> ``sessionmaker``), go through here. All src imports are local so a rename
    touches only this class.
    """

    def __init__(
        self, app: FastAPI, client: AsyncClient | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._app = app
        self._client = client
        self._monkeypatch = monkeypatch
        self._sm = cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)

    # ---- executor ---------------------------------------------------------------------------

    def _executor(self) -> Any:
        from src.engine.langgraph_executor import LangGraphExecutor

        return LangGraphExecutor(
            sessionmaker=self._sm,
            checkpointer=self._app.state.checkpointer,
            event_bus=self._app.state.event_bus,
        )

    async def executor_run(
        self,
        execution_id: str,
        *,
        before_transition: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Drive the real executor. `before_transition(kind)` runs right before the executor's
        guarded status write (`kind` is "completed", "waiting_approval" or "failed"), which is how
        a test lands a cancel or a lease sweep between the end of the graph and that write."""
        executor = self._executor()
        if before_transition is not None:
            hook = before_transition
            for kind, attr in (
                ("completed", "_mark_completed"),
                ("waiting_approval", "_mark_waiting_approval"),
                ("failed", "_mark_failed"),
            ):
                original = getattr(executor, attr)

                def _wrap(kind: str = kind, original: Any = original) -> Any:
                    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
                        await hook(kind)
                        return await original(*args, **kwargs)

                    return _wrapped

                setattr(executor, attr, _wrap())
        await executor.run(execution_id)

    async def executor_resume(self, execution_id: str, decision: str) -> None:
        await self._executor().resume(execution_id, decision)

    async def start_execution(
        self,
        workflow_id: str,
        user_id: str | None,
        run_input: Any,
        idempotency_key: str | None = None,
    ) -> str:
        try:
            row = await self._executor().start_execution(
                workflow_id=workflow_id,
                input=run_input,
                user_id=user_id,
                idempotency_key=idempotency_key,
            )
        except AlreadyExists as exc:
            raise DuplicateKey(str(exc)) from exc
        return cast("str", row.id)

    async def mark_completed(self, execution_id: str, final_state: dict[str, Any]) -> bool:
        return cast("bool", await self._executor()._mark_completed(execution_id, final_state))  # pyright: ignore[reportPrivateUsage]

    # ---- events -----------------------------------------------------------------------------

    async def append_event(self, execution_id: str, type_: str, payload: dict[str, Any]) -> int:
        from src.engine.events import ExecutionEvent

        event = ExecutionEvent(type=cast("Any", type_), execution_id=execution_id, payload=payload)
        try:
            return cast("int", await self._app.state.event_bus.append(event))
        except AlreadyExists as exc:
            raise DuplicateKey(str(exc)) from exc

    async def list_events_since(self, execution_id: str, after_seq: int) -> list[dict[str, Any]]:
        events = await self._app.state.event_bus.list_since(execution_id, after_seq=after_seq)
        return [{"seq": e.seq, "type": e.type, "payload": e.payload} for e in events]

    async def notify(self, execution_id: str, seq: int) -> None:
        from src.engine.events_notify import notify_execution_event

        await notify_execution_event(execution_id, seq=seq)

    def detach_database(self) -> Callable[[], None]:
        """Run the app without its database handle (today ``app.state.db``; after the port
        ``app.state.db_sessionmaker``); returns the function that puts it back."""
        state = self._app.state
        original = state.db_sessionmaker
        state.db_sessionmaker = None

        def _restore() -> None:
            state.db_sessionmaker = original

        return _restore

    # ---- failure seams ----------------------------------------------------------------------

    def break_guarded_failure_write(self) -> None:
        """Make the claim route's crash handler's own "mark failed" write raise (today the
        Prisma ``update_many``; after the port ``executions_repo.mark_failed_if_running``)."""

        async def _raise(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("failure write is down")

        self._monkeypatch.setattr("src.db.repos.executions.mark_failed_if_running", _raise)

    # ---- sweeps -----------------------------------------------------------------------------

    def _hook_scans(self, after_scan: Callable[[], Awaitable[None]] | None) -> None:
        """Run the hook after the real candidate query and before the guarded writes."""
        if after_scan is None:
            return
        from src.db.repos import executions as executions_repo

        for name in ("list_orphaned_running", "list_waiting_approval", "list_expired_leases"):
            real = getattr(executions_repo, name)

            async def _scan(*args: Any, _real: Any = real, **kwargs: Any) -> Any:
                rows = await _real(*args, **kwargs)
                await after_scan()
                return rows

            self._monkeypatch.setattr(executions_repo, name, _scan)

    async def sweep_orphaned(
        self,
        *,
        grace_seconds: int,
        now: datetime,
        after_scan: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[int, int, int]:
        from src.maintenance.execution_sweeper import sweep_orphaned_running

        self._hook_scans(after_scan)
        r = await sweep_orphaned_running(self._sm, grace_seconds=grace_seconds, now=now)
        return r.scanned, r.marked_failed, r.errored

    async def sweep_approvals(
        self,
        *,
        timeout_hours: int,
        now: datetime,
        after_scan: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[int, int, int]:
        from src.maintenance.execution_sweeper import sweep_expired_approvals

        self._hook_scans(after_scan)
        r = await sweep_expired_approvals(self._sm, timeout_hours=timeout_hours, now=now)
        return r.scanned, r.marked_failed, r.errored

    async def sweep_leases(
        self,
        *,
        now: datetime,
        after_scan: Callable[[], Awaitable[None]] | None = None,
    ) -> tuple[int, int, int]:
        from src.maintenance.execution_sweeper import sweep_expired_leases

        self._hook_scans(after_scan)
        r = await sweep_expired_leases(self._sm, now=now)
        return r.scanned, r.marked_failed, r.errored

    async def sweep_events(self, *, retention_days: int, now: datetime) -> int:
        from src.maintenance.execution_sweeper import sweep_old_execution_events

        return await sweep_old_execution_events(self._sm, retention_days=retention_days, now=now)

    # ---- rate limiter -----------------------------------------------------------------------

    async def rate_check(
        self, route_key: str, client_key: str, capacity: int, refill_per_second: float
    ) -> tuple[bool, float]:
        from src.security.rate_limit import BucketConfig

        return cast(
            "tuple[bool, float]",
            await self._app.state.rate_limiter.check(
                route_key, client_key, BucketConfig(capacity, refill_per_second)
            ),
        )

    # ---- builder diagnosis ------------------------------------------------------------------

    async def diagnose(
        self, execution_id: str, *, workflow_id: str, user_id: str, role: str
    ) -> dict[str, Any] | None:
        from src.builder.diagnostics import build_diagnosis

        result = await build_diagnosis(
            self._sm, execution_id, workflow_id=workflow_id, user_id=user_id, role=role
        )
        return None if result is None else asdict(result)

    # ---- builder diagnosis via route -------------------------------------------------------

    async def builder_diagnosis_via_route(
        self,
        who: Member,
        workflow_id: str,
        execution_id: str,
    ) -> str | None:
        """POST /builder/{id}/message with an ``executionId`` through the REAL route and the REAL
        ``run_builder_turn``/``build_diagnosis``; only the LLM (model construction and the tool
        loop) is replaced. Returns the diagnosis system message the turn put in the LLM context, or
        None when no diagnosis was produced."""
        import src.api.builder as builder_module
        import src.builder.builder_agent as agent_module

        captured: list[str] = []

        async def _fake_loop(model: Any, ctx: Any, session: Any, *args: Any, **kwargs: Any) -> str:
            captured.extend(
                str(m.content) for m in session.messages if m.__class__.__name__ == "SystemMessage"
            )
            return "scripted"

        def _fake_model(*_args: Any, **_kwargs: Any) -> object:
            return object()

        self._monkeypatch.setattr(agent_module, "_tool_loop", _fake_loop)
        self._monkeypatch.setattr(builder_module, "build_chat_model", _fake_model)
        assert self._client is not None
        r = await self._client.post(
            f"/builder/{workflow_id}/message",
            json={"message": "why did it fail?", "executionId": execution_id},
            headers=who.headers,
        )
        assert r.status_code == 200, r.text
        diagnoses = [c for c in captured if c.startswith("Diagnosis of failed execution")]
        return diagnoses[0] if diagnoses else None


@pytest.fixture
def adapter(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> Phase4aAdapter:
    return Phase4aAdapter(app, client, monkeypatch)
