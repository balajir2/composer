"""Fixtures for the SQLAlchemy phase 3 behaviour oracle (workflows, workflow assignments).

These tests describe what the code does TODAY (Prisma-backed) and must keep passing unchanged
while ``workflows`` and ``workflow_assignments`` move to SQLAlchemy. The only code later tasks may
edit is the ADAPTER AREA: the ``Phase3Adapter`` class and the "ADAPTER AREA imports" block below.
No test is edited by a later task: revoking a missing assignment returns 404 today
(``test_revoke_missing_assignment_today_404``) and must keep doing so after the port.
Everything else here talks to the app over HTTP, or to the database through SQLAlchemy
setup/inspection helpers.

Seams:
* Builder save: the HTTP route ``POST /builder/{id}/message`` is driven with
  ``src.api.builder.run_builder_turn`` (the LLM tool loop) and ``build_chat_model`` replaced by a
  scripted turn. Only the LLM loop is replaced; loading the workflow, the visibility check, the
  secret encryption and the persist step are the real route code. Those two names are module
  attributes of ``src.api.builder`` and stay when that module's db access moves, so the seam does
  not change in the port.
* Executor workflow read: ``LangGraphExecutor`` is driven through the adapter (its constructor
  and the workflow read inside ``_prepare_compiled`` change in the port).

Coverage boundary: the OAB -> Composer migration writer (``insert_if_absent``) and reconcile
(``claim_orphans_by_email``) are NOT covered by this oracle. They are guarded by
``tests/integration/test_migration.py`` plus the new repository tests.

Network is never real: ``httpx_mock`` (pytest-httpx) is requested for every test, so an
unexpected outbound request fails the test. Loopback is excluded (Prisma's query engine is an
HTTP server on localhost); the ASGI test client is never intercepted.
"""

import itertools
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.models import (
    CloudStorageConnection,
    User,
    UserRole,
    Workflow,
    WorkflowAssignment,
    WorkflowExecution,
)

# ADAPTER AREA imports: exception classes that tests assert on would be re-exported from here, so
# a rename in src only touches this block and the Phase3Adapter bodies. Tests import them from
# this module, never from src. (Today none is needed: the executor raises a bare RuntimeError.)
from src.main import create_app
from src.security.encryption import decrypt_marked, encrypt
from tests._rate_limit_reset import reset_rate_limit_buckets

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|\+00:00)$")
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
REDACTED = "••••••••"


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


def jlist(resp: httpx.Response) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", resp.json())


def iso_dt(value: str) -> datetime:
    """Parse an API timestamp (`...Z` or `+00:00`) into an aware datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def unique(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}"


def not_found(workflow_id: str) -> str:
    return f"Workflow {workflow_id!r} not found."


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]
    refresh_token: str


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


def simple_graph() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return chain()


def payload(
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    if nodes is None:
        nodes, edges = simple_graph()
    body: dict[str, Any] = {"name": unique("wf"), "nodes": nodes, "edges": edges or []}
    body.update(overrides)
    return body


def jira(node_id: str = "j1", token: str | None = "jira-secret-token") -> dict[str, Any]:
    data: dict[str, Any] = {"domain": "t.atlassian.net", "email": "j@example.com"}
    if token is not None:
        data["apiToken"] = token
    return node(node_id, "jira", **data)


def vector_db(
    node_id: str = "v1", key: str = "vdb-secret-key", emb: str = "emb-secret-key"
) -> dict[str, Any]:
    return node(
        node_id,
        "vector-db",
        vectorDbApiKey=key,
        vectorDbEmbeddingApiKey=emb,
        vectorDbEndpoint="https://v.example.test",
        vectorDbCollection="c",
    )


def confluence(node_id: str = "c1", token: str = "conf-secret-token") -> dict[str, Any]:
    return node(
        node_id, "confluence", domain="t.atlassian.net", email="c@example.com", apiToken=token
    )


def http_node(node_id: str = "h1", auth: str = "Bearer http-secret-value") -> dict[str, Any]:
    return node(
        node_id,
        "http",
        httpUrl="https://api.example.test/x",
        httpMethod="GET",
        httpHeaders={"Authorization": auth, "Content-Type": "application/json"},
    )


def node_of(nodes: list[Any], node_id: str) -> dict[str, Any]:
    return next(cast("dict[str, Any]", n) for n in nodes if n["id"] == node_id)


# --------------------------------------------------------------------------- environment


@pytest.fixture
def phase3_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Standalone mode, known secrets, non-production environment (so test_cleanup is mounted)."""
    import os

    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "ci-test-only-not-a-real-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    # Executions run real graphs here: never ship their traces to LangSmith from a dev shell.
    for name in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING"):
        monkeypatch.setenv(name, "false")
    for name in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def app(phase3_env: None) -> AsyncIterator[FastAPI]:
    """Overrides tests/conftest.py's `app` so the phase 3 env is applied before create_app()."""
    get_settings.cache_clear()
    _app = create_app()
    async with _app.router.lifespan_context(_app):
        yield _app


@pytest.fixture(autouse=True)
def _network_lock(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnusedFunction]
    """Request httpx_mock everywhere: any outbound request without a registered mock fails."""
    del httpx_mock


@pytest_asyncio.fixture
async def sessionmaker_(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    return cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)


# --------------------------------------------------------------------------- users + cleanup


async def _register_user(client: AsyncClient, *, email: str | None = None) -> Member:
    email = email or f"p3-{secrets.token_hex(6)}@example.com"
    # register is capped at 5/min per client address; setup of many users must never trip it
    await reset_rate_limit_buckets()
    password = "correct-horse-battery-staple"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    body = jbody(resp)
    return Member(body["id"], email, password, bearer(body["accessToken"]), body["refreshToken"])


@pytest_asyncio.fixture
async def make_user(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[MakeUser]:
    """Factory registering users. At teardown it deletes the workflows they own (executions and
    assignments cascade at the DB level), every assignment they are party to (users are loose
    strings here, no FK), and the users themselves."""
    made: list[str] = []

    async def _make(*, email: str | None = None) -> Member:
        m = await _register_user(client, email=email)
        made.append(m.id)
        return m

    yield _make
    async with sessionmaker_() as s:
        await s.execute(delete(Workflow).where(Workflow.user_id.in_(made)))
        await s.execute(
            delete(CloudStorageConnection).where(CloudStorageConnection.user_id.in_(made))
        )
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
    """SQLAlchemy helpers for test setup and inspection of workflow tables."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sm

    @staticmethod
    def snapshot(row: Any) -> dict[str, Any]:
        """Every column of an ORM row, for byte-identical before/after comparisons."""
        return {c.key: getattr(row, c.key) for c in row.__mapper__.column_attrs}

    async def seed(
        self,
        owner: Member | str | None,
        *,
        name: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
        updated_at: datetime | None = None,
        **columns: Any,
    ) -> str:
        """Insert a workflow row directly; returns its id. Columns use model attribute names."""
        if nodes is None:
            nodes, edges = simple_graph()
        user_id = owner.id if isinstance(owner, Member) else owner
        async with self.sm() as s:
            wf = Workflow(
                user_id=user_id,
                name=name or unique("wf"),
                nodes=nodes,
                edges=edges or [],
                **columns,
            )
            if updated_at is not None:
                wf.updated_at = updated_at
            s.add(wf)
            await s.commit()
            return wf.id

    async def workflow(self, workflow_id: str) -> Workflow | None:
        async with self.sm() as s:
            return await s.scalar(select(Workflow).where(Workflow.id == workflow_id))

    async def must(self, workflow_id: str) -> Workflow:
        row = await self.workflow(workflow_id)
        assert row is not None, f"workflow {workflow_id} missing"
        return row

    async def backdate(self, workflow_id: str, when: datetime = LONG_AGO) -> None:
        async with self.sm() as s:
            await s.execute(
                text("UPDATE workflows SET updated_at = :t WHERE id = :i"),
                {"t": when, "i": workflow_id},
            )
            await s.commit()

    async def set_workflow(self, workflow_id: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(update(Workflow).where(Workflow.id == workflow_id).values(**values))
            await s.commit()

    async def count_workflows(self, ids: list[str]) -> int:
        async with self.sm() as s:
            return (
                await s.scalar(
                    select(func.count()).select_from(Workflow).where(Workflow.id.in_(ids))
                )
            ) or 0

    async def assign(self, workflow_id: str, user_id: str, assigned_by: str | None = None) -> str:
        async with self.sm() as s:
            row = WorkflowAssignment(
                workflow_id=workflow_id, user_id=user_id, assigned_by_id=assigned_by or user_id
            )
            s.add(row)
            await s.commit()
            return row.id

    async def assignments(self, workflow_id: str) -> list[WorkflowAssignment]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(WorkflowAssignment).where(
                            WorkflowAssignment.workflow_id == workflow_id
                        )
                    )
                ).all()
            )

    async def assignments_for_user(self, user_id: str) -> list[WorkflowAssignment]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(WorkflowAssignment).where(
                            or_(
                                WorkflowAssignment.user_id == user_id,
                                WorkflowAssignment.assigned_by_id == user_id,
                            )
                        )
                    )
                ).all()
            )

    async def seed_execution(self, workflow_id: str, user_id: str | None = None) -> str:
        async with self.sm() as s:
            row = WorkflowExecution(
                workflow_id=workflow_id,
                user_id=user_id,
                status="completed",
                node_results={},
                variables={},
                thread_id=unique("thread"),
            )
            s.add(row)
            await s.commit()
            return row.id

    async def execution_exists(self, execution_id: str) -> bool:
        async with self.sm() as s:
            return (
                await s.scalar(
                    select(WorkflowExecution.id).where(WorkflowExecution.id == execution_id)
                )
            ) is not None

    async def executions_for(self, workflow_id: str) -> list[WorkflowExecution]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(WorkflowExecution).where(
                            WorkflowExecution.workflow_id == workflow_id
                        )
                    )
                ).all()
            )

    async def stored_node(self, workflow_id: str, node_id: str) -> dict[str, Any]:
        row = await self.must(workflow_id)
        return node_of(cast("list[Any]", row.nodes), node_id)

    async def seed_connection(self, user_id: str) -> str:
        """A Google Drive connection with a still-valid access token (no refresh needed)."""
        async with self.sm() as s:
            row = CloudStorageConnection(
                user_id=user_id,
                provider="google-drive",
                account_email=f"{unique('acct')}@gmail.test",
                encrypted_access_token=encrypt("drive-access-p3"),
                encrypted_refresh_token=encrypt("drive-refresh-p3"),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
            s.add(row)
            await s.commit()
            return row.id

    async def user_exists(self, user_id: str) -> bool:
        async with self.sm() as s:
            return (await s.scalar(select(User.id).where(User.id == user_id))) is not None


@pytest_asyncio.fixture
async def db(sessionmaker_: async_sessionmaker[AsyncSession]) -> Db:
    return Db(sessionmaker_)


# --------------------------------------------------------------------------- API helpers


@dataclass
class WorkflowApi:
    """Create / read workflows through the real HTTP API."""

    client: AsyncClient

    async def create(self, owner: Member, **overrides: Any) -> dict[str, Any]:
        resp = await self.client.post(
            "/workflows", json=payload(**overrides), headers=owner.headers
        )
        assert resp.status_code == 201, resp.text
        return jbody(resp)

    async def get(self, who: Member, workflow_id: str) -> httpx.Response:
        return await self.client.get(f"/workflows/{workflow_id}", headers=who.headers)

    async def put(self, who: Member, workflow_id: str, body: dict[str, Any]) -> httpx.Response:
        return await self.client.put(f"/workflows/{workflow_id}", json=body, headers=who.headers)

    async def publish(self, who: Member, workflow: dict[str, Any], slug: str) -> httpx.Response:
        body = {
            "name": workflow["name"],
            "nodes": list(workflow["nodes"]),
            "edges": workflow["edges"],
            "isPublic": workflow["isPublic"],
            "isProduction": True,
            "externalSlug": slug,
        }
        return await self.put(who, workflow["id"], body)


@pytest.fixture
def wf_api(client: AsyncClient) -> WorkflowApi:
    return WorkflowApi(client)


async def mint_api_key(client: AsyncClient, who: Member) -> dict[str, str]:
    """A per-user external-invoke API key (`ck_...`) as an Authorization header."""
    resp = await client.post("/api-keys", json={"label": "p3"}, headers=who.headers)
    assert resp.status_code == 201, resp.text
    key = jbody(resp)["key"]
    assert key.startswith("ck_")
    return bearer(key)


# --------------------------------------------------------------------------- service adapter


class Phase3Adapter:
    """THE ONLY CODE LATER TASKS MAY EDIT in this package: the bodies of these methods (and the
    ADAPTER AREA imports at the top of this file).

    Entry points that are not plain HTTP endpoints, or whose seams change in phase 3
    (``db: Prisma`` -> ``sessionmaker``), go through here. All src imports are local so a rename
    touches only this class.
    """

    def __init__(self, app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        self._app = app
        self._client = client
        self._monkeypatch = monkeypatch
        self._db: Any = app.state.db
        self._sm = cast("async_sessionmaker[AsyncSession]", app.state.db_sessionmaker)
        self.builder_calls: list[dict[str, Any]] = []

    # ---- builder save -------------------------------------------------------------------------

    async def builder_turn(
        self,
        who: Member,
        workflow_id: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        *,
        rename_to: str | None = None,
        message: str = "build it",
        during_turn: Callable[[], Awaitable[None]] | None = None,
    ) -> httpx.Response:
        """POST /builder/{id}/message where the builder's LLM turn is scripted to hand back a
        workflow with these nodes/edges (and optionally a different name and description, which
        the route must NOT persist). `during_turn`, if given, runs while the "LLM" is thinking, so a
        test can change the stored row between the route's load and its save. Everything after
        the turn is the real route code."""
        import src.api.builder as builder_module
        from src.engine.workflow import Workflow as EngineWorkflow

        calls = self.builder_calls

        async def _scripted_turn(**kwargs: Any) -> tuple[list[dict[str, Any]], str, Any]:
            calls.append(kwargs)
            if during_turn is not None:
                await during_turn()
            current = kwargs["workflow"]
            updated = EngineWorkflow.model_validate(
                {
                    "id": current.id,
                    "name": rename_to or current.name,
                    "description": "changed by the builder" if rename_to else current.description,
                    "nodes": nodes,
                    "edges": edges,
                }
            )
            return [{"type": "scripted"}], "scripted reply", updated

        def _scripted_model(*args: Any, **kwargs: Any) -> object:
            return object()

        self._monkeypatch.setattr(builder_module, "run_builder_turn", _scripted_turn)
        self._monkeypatch.setattr(builder_module, "build_chat_model", _scripted_model)
        return await self._client.post(
            f"/builder/{workflow_id}/message",
            json={"message": message},
            headers=who.headers,
        )

    # ---- executor workflow read ---------------------------------------------------------------

    def _executor(self) -> Any:
        from src.engine.langgraph_executor import LangGraphExecutor

        return LangGraphExecutor(
            db=self._db,
            checkpointer=self._app.state.checkpointer,
            event_bus=self._app.state.event_bus,
        )

    async def run_execution(
        self, workflow_id: str, user_id: str | None, run_input: Any = "hello"
    ) -> dict[str, Any]:
        """Create an execution of an existing workflow, mark it running (what claim-and-run does)
        and drive the executor. Returns the final {status, output, error}."""
        executor = self._executor()
        row = await executor.start_execution(
            workflow_id=workflow_id, input=run_input, user_id=user_id
        )
        await self._db.workflowexecution.update(where={"id": row.id}, data={"status": "running"})
        await executor.run(row.id)
        final = await self._db.workflowexecution.find_unique(where={"id": row.id})
        assert final is not None
        return {"id": row.id, "status": final.status, "output": final.output, "error": final.error}

    async def run_after_workflow_deleted(self, workflow_id: str, user_id: str | None) -> bool:
        """Start an execution, delete its workflow underneath it (DB cascade removes the
        execution), then run it. Returns whether the execution row still exists."""
        executor = self._executor()
        row = await executor.start_execution(workflow_id=workflow_id, input="x", user_id=user_id)
        async with self._sm() as s:
            await s.execute(delete(Workflow).where(Workflow.id == workflow_id))
            await s.commit()
        await executor.run(row.id)
        found = await self._db.workflowexecution.find_unique(where={"id": row.id})
        return found is not None

    async def prepare_compiled_for(self, workflow_id: str) -> None:
        """The workflow read inside ``_prepare_compiled`` for an execution naming `workflow_id`.
        Raises whatever the executor raises for a missing workflow."""
        execution = SimpleNamespace(
            id="exec-synthetic",
            workflowId=workflow_id,
            input="x",
            userId=None,
            threadId=unique("thread"),
        )
        await self._executor()._prepare_compiled(execution)  # pyright: ignore[reportPrivateUsage]

    async def poll_file_triggers(self) -> dict[str, int]:
        """POST /internal/poll-file-triggers with the OIDC dependency overridden."""
        from src.api.internal import _verify_internal_oidc  # pyright: ignore[reportPrivateUsage]

        def _allow() -> None:
            return None

        self._app.dependency_overrides[_verify_internal_oidc] = _allow
        try:
            resp = await self._client.post("/internal/poll-file-triggers")
        finally:
            self._app.dependency_overrides.pop(_verify_internal_oidc, None)
        assert resp.status_code == 200, resp.text
        return cast("dict[str, int]", resp.json())


@pytest.fixture
def adapter(app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> Phase3Adapter:
    return Phase3Adapter(app, client, monkeypatch)


# --------------------------------------------------------------------------- misc helpers


def decrypts_to(stored: Any, plaintext: str) -> bool:
    """True when a stored secret field is marker-encrypted ciphertext of `plaintext`."""
    return (
        isinstance(stored, str)
        and stored.startswith("enc:v1:")
        and stored != plaintext
        and decrypt_marked(stored) == plaintext
    )


def secrets_absent(text_: str, *values: str) -> bool:
    return all(v not in text_ for v in values)


def ago(**kw: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)
