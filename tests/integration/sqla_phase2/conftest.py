"""Fixtures for the SQLAlchemy phase 2 behaviour oracle (MCP servers, MCP OAuth, cloud storage).

These tests describe what the code does TODAY (Prisma-backed) and must keep passing unchanged
while the four tables move to SQLAlchemy. The only code later tasks may edit is the body of the
``Phase2Adapter`` class below (its methods call the resolver / OAuth helpers, whose signatures
change from ``db`` to ``sessionmaker``). Everything else here talks to the app over HTTP or to
the database through SQLAlchemy setup/inspection helpers.

Network is never real:
* ``httpx_mock`` (pytest-httpx) answers OAuth token endpoints, Google, and the MCP server itself.
  The MCP seam is therefore the HTTP transport (``httpx`` POSTs made by ``MCPClient._rpc``):
  no Composer function is patched, so the seam cannot change in the port, and the tests can also
  assert which ``Authorization`` header the MCP client sent.
* pytest-httpx patches ``httpx.AsyncHTTPTransport`` only, so the ASGI test client
  (``httpx.AsyncClient(transport=ASGITransport(...))``) is never intercepted.
* ``_network_lock`` (autouse) requests ``httpx_mock`` for every test, so an unexpected outbound
  request fails the test instead of leaving the machine.
"""

import json
import os
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db.models import (
    CloudStorageConnection,
    McpOAuthState,
    McpOAuthToken,
    McpServer,
    User,
    UserRole,
)
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.integrations.google_drive.oauth import get_valid_drive_access_token
from src.main import create_app
from src.mcp.oauth import get_valid_access_token
from src.mcp.resolver import (
    resolve_mcp_tools_by_names,
    resolve_mcp_tools_for_node,
    resolve_single_mcp_tool,
)
from src.security.encryption import encrypt
from src.tools.base import BuildContext

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{1,6})?(Z|\+00:00)$")
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)


# Applied by every test module via `pytestmark`. Prisma's query engine is an HTTP server on
# localhost that the Prisma client reaches through httpx, so loopback must not be mocked; every
# other host is mocked and an unregistered request fails the test. Unrequested mocks are fine.
def _mock_everything_but_loopback(request: httpx.Request) -> bool:
    return request.url.host not in {"localhost", "127.0.0.1"}


HTTPX_MARK = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False,
    should_mock=_mock_everything_but_loopback,
)


def split_url(url: str) -> tuple[str, dict[str, list[str]]]:
    """(scheme://host/path, query parameters with blank values kept) of a URL."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}", parse_qs(
        parts.query, keep_blank_values=True
    )


def query_of(url: str) -> dict[str, str]:
    """First value of every query parameter of a URL."""
    return {k: v[0] for k, v in split_url(url)[1].items()}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def jbody(resp: httpx.Response) -> dict[str, Any]:
    return cast("dict[str, Any]", resp.json())


def jlist(resp: httpx.Response) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", resp.json())


def form_of(request: httpx.Request) -> dict[str, str]:
    """Decode an application/x-www-form-urlencoded request body into a flat dict."""
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def aware(value: datetime | None) -> datetime:
    assert value is not None
    assert value.tzinfo is not None
    return value


def near(value: datetime | None, expected: datetime, tolerance_s: float = 30.0) -> bool:
    return abs((aware(value) - expected).total_seconds()) <= tolerance_s


def unique(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}"


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    password: str
    headers: dict[str, str]
    refresh_token: str


MakeUser = Callable[..., Awaitable[Member]]


# --------------------------------------------------------------------------- environment


@pytest.fixture
def phase2_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Standalone mode, known secrets, non-production environment (so test_cleanup is mounted).

    ``ENVIRONMENT=development`` is the default; ``/internal/test-users/me`` is mounted for every
    environment except ``production`` (src/main.py). Google OAuth credentials and the public
    backend URL are fixed so the Google-Drive tests are independent of any local .env.
    """
    monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("COMPOSER_DEPLOYMENT_MODE", "standalone")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET", "ci-test-only-not-a-real-secret")
    monkeypatch.setenv("ENCRYPTION_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "google-client-id-test")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "google-client-secret-test")
    monkeypatch.setenv("BACKEND_PUBLIC_URL", "http://backend.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def app(phase2_env: None) -> AsyncIterator[FastAPI]:
    """Overrides tests/conftest.py's `app` so the phase 2 env is applied before create_app()."""
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
    email = email or f"p2-{secrets.token_hex(6)}@example.com"
    password = "correct-horse-battery-staple"
    resp = await client.post("/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201, resp.text
    body = jbody(resp)
    return Member(body["id"], email, password, bearer(body["accessToken"]), body["refreshToken"])


@pytest_asyncio.fixture
async def make_user(
    client: AsyncClient, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[MakeUser]:
    """Factory registering users. At teardown it deletes the users AND every row the four phase 2
    tables hold for them (these tables have no FK to users, so nothing cascades)."""
    made: list[str] = []

    async def _make(*, email: str | None = None) -> Member:
        m = await _register_user(client, email=email)
        made.append(m.id)
        return m

    yield _make
    async with sessionmaker_() as s:
        server_ids = list(
            (await s.scalars(select(McpServer.id).where(McpServer.user_id.in_(made)))).all()
        )
        await s.execute(
            delete(McpOAuthToken).where(
                McpOAuthToken.user_id.in_(made) | McpOAuthToken.mcp_server_id.in_(server_ids)
            )
        )
        await s.execute(
            delete(McpOAuthState).where(
                McpOAuthState.user_id.in_(made) | McpOAuthState.mcp_server_id.in_(server_ids)
            )
        )
        await s.execute(delete(McpServer).where(McpServer.user_id.in_(made)))
        await s.execute(
            delete(CloudStorageConnection).where(CloudStorageConnection.user_id.in_(made))
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
async def admin(make_user: MakeUser, sessionmaker_: async_sessionmaker[AsyncSession]) -> Member:
    m = await make_user()
    async with sessionmaker_() as s:
        await s.execute(update(User).where(User.id == m.id).values(role=UserRole.ADMIN))
        await s.commit()
    return m


# --------------------------------------------------------------------------- DB seed / inspect


class Db:
    """SQLAlchemy helpers for test setup and inspection of the four phase 2 tables."""

    def __init__(self, sm: async_sessionmaker[AsyncSession]) -> None:
        self.sm = sm

    async def server(self, server_id: str) -> McpServer | None:
        async with self.sm() as s:
            return await s.scalar(select(McpServer).where(McpServer.id == server_id))

    async def oauth_config(self, server_id: str) -> dict[str, Any]:
        row = await self.server(server_id)
        assert row is not None and row.oauth_config is not None
        return cast("dict[str, Any]", row.oauth_config)

    async def headers(self, server_id: str) -> dict[str, str]:
        row = await self.server(server_id)
        assert row is not None and row.headers is not None
        return cast("dict[str, str]", row.headers)

    async def token(self, server_id: str, user_id: str) -> McpOAuthToken | None:
        async with self.sm() as s:
            return await s.scalar(
                select(McpOAuthToken).where(
                    McpOAuthToken.mcp_server_id == server_id, McpOAuthToken.user_id == user_id
                )
            )

    async def state(self, state: str) -> McpOAuthState | None:
        async with self.sm() as s:
            return await s.scalar(select(McpOAuthState).where(McpOAuthState.state == state))

    async def states_for(self, server_id: str, user_id: str) -> list[McpOAuthState]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(McpOAuthState).where(
                            McpOAuthState.mcp_server_id == server_id,
                            McpOAuthState.user_id == user_id,
                        )
                    )
                ).all()
            )

    async def connection(self, connection_id: str) -> CloudStorageConnection | None:
        async with self.sm() as s:
            return await s.scalar(
                select(CloudStorageConnection).where(CloudStorageConnection.id == connection_id)
            )

    async def connections_for(self, user_id: str) -> list[CloudStorageConnection]:
        async with self.sm() as s:
            return list(
                (
                    await s.scalars(
                        select(CloudStorageConnection).where(
                            CloudStorageConnection.user_id == user_id
                        )
                    )
                ).all()
            )

    async def count_for_user(self, user_id: str) -> dict[str, int]:
        """Row counts in the four tables that belong to `user_id`."""
        async with self.sm() as s:
            counts: dict[str, int] = {}
            for name, model in (
                ("servers", McpServer),
                ("tokens", McpOAuthToken),
                ("states", McpOAuthState),
                ("connections", CloudStorageConnection),
            ):
                col = model.user_id
                counts[name] = (
                    await s.scalar(select(func.count()).select_from(model).where(col == user_id))
                ) or 0
            return counts

    async def seed_token(
        self,
        server_id: str,
        user_id: str,
        *,
        access: str = "access-token",
        refresh: str | None = "refresh-token",
        expires_in: timedelta | None = timedelta(hours=1),
        scope: str | None = None,
    ) -> str:
        async with self.sm() as s:
            row = McpOAuthToken(
                mcp_server_id=server_id,
                user_id=user_id,
                encrypted_access_token=encrypt(access),
                encrypted_refresh_token=encrypt(refresh) if refresh is not None else None,
                expires_at=(datetime.now(UTC) + expires_in) if expires_in is not None else None,
                scope=scope,
            )
            s.add(row)
            await s.commit()
            return row.id

    async def seed_state(
        self,
        server_id: str,
        user_id: str,
        *,
        state: str | None = None,
        expires_in: timedelta = timedelta(minutes=5),
        redirect_uri: str = "http://backend.test/oauth/callback",
        verifier: str = "verifier-seeded",
        scope: str | None = "read",
    ) -> str:
        state = state or unique("state")
        async with self.sm() as s:
            s.add(
                McpOAuthState(
                    mcp_server_id=server_id,
                    user_id=user_id,
                    state=state,
                    code_verifier=verifier,
                    redirect_uri=redirect_uri,
                    scope=scope,
                    expires_at=datetime.now(UTC) + expires_in,
                )
            )
            await s.commit()
        return state

    async def seed_connection(
        self,
        user_id: str,
        *,
        provider: str = "google-drive",
        email: str | None = None,
        access: str = "drive-access",
        refresh: str | None = "drive-refresh",
        expires_in: timedelta | None = timedelta(hours=1),
    ) -> str:
        async with self.sm() as s:
            row = CloudStorageConnection(
                user_id=user_id,
                provider=provider,
                account_email=email or f"{unique('acct')}@gmail.test",
                encrypted_access_token=encrypt(access),
                encrypted_refresh_token=encrypt(refresh) if refresh is not None else None,
                expires_at=(datetime.now(UTC) + expires_in) if expires_in is not None else None,
            )
            s.add(row)
            await s.commit()
            return row.id

    async def set_server(self, server_id: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(update(McpServer).where(McpServer.id == server_id).values(**values))
            await s.commit()

    async def set_token(self, token_id: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(
                update(McpOAuthToken).where(McpOAuthToken.id == token_id).values(**values)
            )
            await s.commit()

    async def set_state(self, state: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(
                update(McpOAuthState).where(McpOAuthState.state == state).values(**values)
            )
            await s.commit()

    async def set_connection(self, connection_id: str, **values: Any) -> None:
        async with self.sm() as s:
            await s.execute(
                update(CloudStorageConnection)
                .where(CloudStorageConnection.id == connection_id)
                .values(**values)
            )
            await s.commit()


@pytest_asyncio.fixture
async def db(sessionmaker_: async_sessionmaker[AsyncSession]) -> Db:
    return Db(sessionmaker_)


# --------------------------------------------------------------------------- MCP server fake


@dataclass
class RpcCall:
    method: str
    headers: dict[str, str]


@dataclass
class FakeMcp:
    """A scripted MCP JSON-RPC endpoint served through httpx_mock (no Composer code is patched)."""

    url: str
    tools: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"name": "alpha", "description": "first tool", "inputSchema": {"type": "object"}},
            {"name": "beta", "description": "second tool", "inputSchema": {"type": "object"}},
        ]
    )
    calls: list[RpcCall] = field(default_factory=lambda: list[RpcCall]())
    connect_error: bool = False
    http_status: int | None = None
    fail_tools_list_from_call: int | None = None  # 1-based: fail this tools/list call and later

    @property
    def origin(self) -> str:
        parts = httpx.URL(self.url)
        return f"{parts.scheme}://{parts.netloc.decode()}"

    def methods(self) -> list[str]:
        return [c.method for c in self.calls]

    def authorizations(self) -> set[str]:
        return {c.headers.get("authorization", "") for c in self.calls}

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = cast("dict[str, Any]", json.loads(request.content))
        method = cast("str", body["method"])
        self.calls.append(RpcCall(method, dict(request.headers)))
        if self.connect_error:
            raise httpx.ConnectError("connection refused (scripted)")
        if self.http_status is not None:
            return httpx.Response(self.http_status, text="scripted failure")
        if method == "tools/list":
            n = sum(1 for c in self.calls if c.method == "tools/list")
            if self.fail_tools_list_from_call is not None and n >= self.fail_tools_list_from_call:
                return httpx.Response(500, text="tools/list scripted failure")
            result: dict[str, Any] = {"tools": self.tools}
        elif method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}}
        else:
            result = {"content": [{"type": "text", "text": "ok"}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


FakeMcpFactory = Callable[..., FakeMcp]


@pytest.fixture
def fake_mcp(httpx_mock: HTTPXMock) -> FakeMcpFactory:
    def _make(**overrides: Any) -> FakeMcp:
        fake = FakeMcp(url=f"https://{unique('mcp')}.example.test/mcp", **overrides)
        httpx_mock.add_callback(fake.handle, url=fake.url, method="POST", is_reusable=True)
        return fake

    return _make


@dataclass
class FakeEndpoint:
    """A scripted HTTP endpoint (OAuth token endpoint, Google userinfo...) served by httpx_mock.

    Responses are queued with ``push`` and consumed in order; a request with nothing queued
    raises, so an unexpected call fails loudly. Every request is kept in ``requests``.
    """

    url: str
    queue: list[tuple[int, dict[str, Any]]] = field(
        default_factory=lambda: list[tuple[int, dict[str, Any]]]()
    )
    requests: list[httpx.Request] = field(default_factory=lambda: list[httpx.Request]())

    def push(self, payload: dict[str, Any], status: int = 200) -> "FakeEndpoint":
        self.queue.append((status, payload))
        return self

    def forms(self) -> list[dict[str, str]]:
        return [form_of(r) for r in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.queue:
            raise AssertionError(f"unexpected request to {self.url}")
        status, payload = self.queue.pop(0)
        return httpx.Response(status, json=payload)


FakeEndpointFactory = Callable[..., FakeEndpoint]


@pytest.fixture
def fake_endpoint(httpx_mock: HTTPXMock) -> FakeEndpointFactory:
    def _make(url: str, method: str = "POST") -> FakeEndpoint:
        endpoint = FakeEndpoint(url=url)
        httpx_mock.add_callback(endpoint.handle, url=url, method=method, is_reusable=True)
        return endpoint

    return _make


def origin_of(url: str) -> str:
    """scheme://host[:port] of a URL: the RFC 8707 `resource` value for an MCP server URL."""
    u = httpx.URL(url)
    return f"{u.scheme}://{u.netloc.decode()}"


# --------------------------------------------------------------------------- service adapter


class Phase2Adapter:
    """THE ONLY CODE LATER TASKS MAY EDIT in this package: the bodies of these methods.

    Resolver and OAuth helpers are not HTTP endpoints and their signatures change in phase 2
    (``db: Prisma`` -> ``sessionmaker``). Tests assert only through this adapter. Today it calls
    the functions with the app's Prisma client.
    """

    def __init__(self, app: FastAPI) -> None:
        self._db: Any = app.state.db

    async def resolve_tools(self, server_id: str, user_id: str | None) -> list[str]:
        """Names of every tool the resolver returns for one server (empty selection = all)."""
        tools = await resolve_mcp_tools_by_names(server_id, [], user_id, self._db)
        return [t.name for t in tools]

    async def resolve_named_tools(
        self, server_id: str, user_id: str | None, names: list[str]
    ) -> list[str]:
        tools = await resolve_mcp_tools_by_names(server_id, names, user_id, self._db)
        return [t.name for t in tools]

    async def resolve_node_tools(self, server_ids: list[str], user_id: str | None) -> list[str]:
        """Agent-node resolution (`mcpServerIds` on an agent node)."""
        node = AgentNode.model_validate(
            {
                "id": "a",
                "type": "agent",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Agent", "mcpServerIds": server_ids},
            }
        )
        ctx = BuildContext(node=node, state=initial_state(), user_id=user_id)
        return [t.name for t in await resolve_mcp_tools_for_node(node, ctx, self._db)]

    async def resolve_single_tool(self, server_id: str, tool_name: str, user_id: str | None) -> str:
        """`mcp` node deterministic mode: the single bound tool's name."""
        _provider, tool = await resolve_single_mcp_tool(server_id, tool_name, user_id, self._db)
        return tool.name

    async def valid_access_token(self, server_id: str, user_id: str | None) -> str:
        """Plaintext OAuth access token for (server, user): fallback to the owner's token on
        shared servers, refresh when near expiry."""
        server = await self._db.mcpserver.find_unique(where={"id": server_id})
        assert server is not None, f"server {server_id} missing"
        return await get_valid_access_token(server, user_id, self._db)

    async def valid_drive_token(self, connection_id: str) -> str:
        """Plaintext Google Drive access token for a connection, refreshed when near expiry."""
        return await get_valid_drive_access_token(connection_id, self._db)


@pytest.fixture
def adapter(app: FastAPI) -> Phase2Adapter:
    return Phase2Adapter(app)


# --------------------------------------------------------------------------- API helpers


@dataclass
class ServerApi:
    """Create MCP servers through the real HTTP API."""

    client: AsyncClient

    async def create(self, owner: Member, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": unique("srv"),
            "url": f"https://{unique('mcp')}.example.test/mcp",
            "authType": "none",
        }
        payload.update(overrides)
        resp = await self.client.post("/mcp-servers", json=payload, headers=owner.headers)
        assert resp.status_code == 201, resp.text
        return jbody(resp)

    async def create_oauth(self, owner: Member, **overrides: Any) -> dict[str, Any]:
        """OAuth server whose IdP endpoints live on a unique `.example.test` host."""
        host = unique("idp")
        config: dict[str, Any] = {
            "authorizeUrl": f"https://{host}.example.test/authorize",
            "tokenUrl": f"https://{host}.example.test/token",
            "clientId": f"client-{host}",
            "clientSecret": f"secret-{host}",
            "scopes": ["read", "write"],
        }
        config.update(overrides.pop("oauthConfig", {}))
        return await self.create(owner, authType="oauth", oauthConfig=config, **overrides)


@pytest.fixture
def servers(client: AsyncClient) -> ServerApi:
    return ServerApi(client)
