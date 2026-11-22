"""Concurrent refreshes of one expired token post to the IdP once (advisory lock).

New behaviour on top of the phase 2 oracle (which stays unchanged): two callers that find the
same token expired used to both POST the same refresh token, which IdPs that detect refresh-token
reuse may revoke. Real Postgres, fake IdP, and a DETERMINISTIC schedule: the refresh POST is
gated at the module seam (``_post_token_form``) with asyncio Events, so caller A is provably
inside the POST, holding the lock, while caller B runs.

A refused caller holds no connection while it waits (it polls with ``pg_try_advisory_xact_lock``),
so B does not show up in ``pg_locks``. Instead each test proves B is polling: it has not posted
and is not done while A is gated.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select

from src.db import locks
from src.db.models import CloudStorageConnection, McpOAuthToken
from src.security.encryption import decrypt
from tests.integration.sqla_phase2.conftest import (
    HTTPX_MARK,
    Db,
    DriveConnectionMissingError,
    FakeEndpoint,
    FakeEndpointFactory,
    McpTokenMissingError,
    Member,
    Phase2Adapter,
    ServerApi,
)
from tests.integration.sqla_phase2.test_cloud_storage import google

pytestmark = [pytest.mark.integration, HTTPX_MARK]

WAIT_S = 10.0  # upper bound for "A reached the gate"; nothing here sleeps for this long


@dataclass
class Gate:
    """Holds the refresh POST until ``release`` is set; counts how many POSTs were attempted."""

    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    entered: int = 0


def gate_refresh_post(monkeypatch: pytest.MonkeyPatch, module: Any, *, form_index: int) -> Gate:
    """Gate ``module._post_token_form`` for refresh_token grants (others pass straight through)."""
    gate = Gate()
    original = module._post_token_form  # pyright: ignore[reportPrivateUsage]

    async def _gated(*args: Any, **kwargs: Any) -> Any:
        if args[form_index].get("grant_type") == "refresh_token":
            gate.entered += 1
            gate.started.set()
            await gate.release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(module, "_post_token_form", _gated)
    return gate


@dataclass
class Backend:
    """One token kind (MCP OAuth token or Drive connection) behind a common test interface."""

    name: str
    module: Any
    form_index: int
    endpoint: FakeEndpoint
    call: Callable[[], Awaitable[str]]
    snapshot: Callable[[], Awaitable[dict[str, Any]]]
    delete_row: Callable[[], Awaitable[None]]
    missing_error: type[Exception]
    refresh_token_in: Callable[[], Awaitable[str]]


async def _mcp_backend(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> Backend:
    import src.mcp.oauth as mcp_oauth

    created = await servers.create_oauth(member)
    sid = created["id"]
    endpoint = fake_endpoint((await db.oauth_config(sid))["tokenUrl"])
    token_id = await db.seed_token(
        sid, member.id, access="old", refresh="old-refresh", expires_in=timedelta(minutes=-5)
    )

    async def snapshot() -> dict[str, Any]:
        return Db.snapshot(await db.token(sid, member.id))

    async def delete_row() -> None:
        async with db.sm() as s:
            await s.execute(delete(McpOAuthToken).where(McpOAuthToken.id == token_id))
            await s.commit()

    async def refresh_token_in() -> str:
        row = await db.token(sid, member.id)
        assert row is not None and row.encrypted_refresh_token is not None
        return decrypt(row.encrypted_refresh_token)

    return Backend(
        "mcp",
        mcp_oauth,
        1,
        endpoint,
        lambda: adapter.valid_access_token(sid, member.id),
        snapshot,
        delete_row,
        McpTokenMissingError,
        refresh_token_in,
    )


async def _drive_backend(
    member: Member,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
) -> Backend:
    import src.integrations.google_drive.oauth as drive_oauth

    endpoint, _ = google(fake_endpoint)
    cid = await db.seed_connection(
        member.id, access="old", refresh="old-refresh", expires_in=timedelta(seconds=-30)
    )

    async def snapshot() -> dict[str, Any]:
        return Db.snapshot(await db.connection(cid))

    async def delete_row() -> None:
        async with db.sm() as s:
            await s.execute(delete(CloudStorageConnection).where(CloudStorageConnection.id == cid))
            await s.commit()

    async def refresh_token_in() -> str:
        row = await db.connection(cid)
        assert row is not None and row.encrypted_refresh_token is not None
        return decrypt(row.encrypted_refresh_token)

    return Backend(
        "drive",
        drive_oauth,
        0,
        endpoint,
        lambda: adapter.valid_drive_token(cid),
        snapshot,
        delete_row,
        DriveConnectionMissingError,
        refresh_token_in,
    )


@pytest.fixture(params=["mcp", "drive"])
async def backend(
    request: pytest.FixtureRequest,
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Backend:
    monkeypatch.setattr(locks, "POLL_MIN_S", 0.02)
    monkeypatch.setattr(locks, "POLL_MAX_S", 0.05)
    if request.param == "mcp":
        return await _mcp_backend(member, servers, db, adapter, fake_endpoint)
    return await _drive_backend(member, db, adapter, fake_endpoint)


async def start_a_inside_the_post(backend: Backend, gate: Gate) -> "asyncio.Task[str]":
    task = asyncio.ensure_future(backend.call())
    await asyncio.wait_for(gate.started.wait(), WAIT_S)
    assert gate.entered == 1
    return task


async def let_b_poll(gate: Gate, b: "asyncio.Task[str]") -> None:
    """B has had ample time (dozens of poll cycles) but must neither post nor finish."""
    await asyncio.sleep(0.7)
    assert gate.entered == 1, "B posted while A held the lock"
    assert not b.done(), "B finished while A was still inside the POST"


# --------------------------------------------------------------------------- 1. one POST


async def test_concurrent_callers_post_once_and_both_get_the_new_token(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = gate_refresh_post(monkeypatch, backend.module, form_index=backend.form_index)
    backend.endpoint.push(
        {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}
    )

    a = await start_a_inside_the_post(backend, gate)
    b = asyncio.ensure_future(backend.call())
    await let_b_poll(gate, b)

    gate.release.set()
    assert await asyncio.gather(a, b) == ["new-access", "new-access"]
    assert gate.entered == 1 and len(backend.endpoint.requests) == 1  # exactly ONE refresh POST
    assert decrypt((await backend.snapshot())["encrypted_access_token"]) == "new-access"
    assert await backend.refresh_token_in() == "new-refresh"


# --------------------------------------------------------------------------- 2. A fails


async def test_when_a_fails_b_refreshes_once_with_the_original_refresh_token(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = gate_refresh_post(monkeypatch, backend.module, form_index=backend.form_index)
    backend.endpoint.push({"error": "temporarily_unavailable"}, status=400)
    backend.endpoint.push({"access_token": "new-access", "expires_in": 3600})

    a = await start_a_inside_the_post(backend, gate)
    b = asyncio.ensure_future(backend.call())
    await let_b_poll(gate, b)

    gate.release.set()
    a_result, b_result = await asyncio.gather(a, b, return_exceptions=True)

    assert isinstance(a_result, backend.module.TokenRefreshError)
    assert b_result == "new-access"
    forms = backend.endpoint.forms()
    assert len(forms) == 2  # A's failed POST, then B's
    assert forms[0]["refresh_token"] == forms[1]["refresh_token"] == "old-refresh"
    assert await backend.refresh_token_in() == "old-refresh"  # not rotated by the IdP


# --------------------------------------------------------------------------- 3. deadline


async def test_waiter_deadline_raises_the_timeout_error_and_leaves_the_row_unchanged(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend.module, "_REFRESH_WAIT_DEADLINE_S", 0.4)
    gate = gate_refresh_post(monkeypatch, backend.module, form_index=backend.form_index)
    backend.endpoint.push({"access_token": "new-access", "expires_in": 3600})
    before = await backend.snapshot()

    a = await start_a_inside_the_post(backend, gate)
    b = asyncio.ensure_future(backend.call())
    with pytest.raises(
        backend.module.TokenRefreshError, match="Timed out waiting for a concurrent token refresh"
    ):
        await asyncio.wait_for(b, WAIT_S)
    assert gate.entered == 1  # B never posted
    assert await backend.snapshot() == before  # B changed nothing

    gate.release.set()
    assert await a == "new-access"
    assert len(backend.endpoint.requests) == 1


# --------------------------------------------------------------------------- 4. deleted


async def test_row_deleted_while_b_waits_makes_both_raise_without_another_post(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = gate_refresh_post(monkeypatch, backend.module, form_index=backend.form_index)
    backend.endpoint.push({"access_token": "new-access", "expires_in": 3600})

    a = await start_a_inside_the_post(backend, gate)
    b = asyncio.ensure_future(backend.call())
    await let_b_poll(gate, b)
    await backend.delete_row()  # the advisory lock does not block row writes

    gate.release.set()
    results = await asyncio.gather(a, b, return_exceptions=True)
    assert all(isinstance(r, backend.missing_error) for r in results), results
    assert gate.entered == 1  # no extra POST


# --------------------------------------------------------------------------- 5. connections


async def test_waiting_callers_do_not_hold_pooled_connections(
    backend: Backend, monkeypatch: pytest.MonkeyPatch, db: Db
) -> None:
    """With A inside the POST, a crowd of waiters must not pile up connections: only A's
    session (the lock holder) stays open as a transaction in Postgres."""
    from sqlalchemy import text

    async def idle_in_transaction() -> int:
        async with db.sm() as s:
            count = await s.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND state = 'idle in transaction' AND pid <> pg_backend_pid()"
                )
            )
        return int(count or 0)

    gate = gate_refresh_post(monkeypatch, backend.module, form_index=backend.form_index)
    backend.endpoint.push({"access_token": "new-access", "expires_in": 3600})
    baseline = await idle_in_transaction()

    a = await start_a_inside_the_post(backend, gate)
    waiters = [asyncio.ensure_future(backend.call()) for _ in range(6)]
    # A waiter's try-lock transaction is open for a few milliseconds per poll, so a single
    # sample can catch one mid-attempt; what HOLDS a connection shows in every sample.
    samples: list[int] = []
    for _ in range(12):
        await asyncio.sleep(0.05)
        samples.append(await idle_in_transaction() - baseline)
    # A's lock-holding transaction, plus at most one stray; six waiters holding connections
    # would read 7.
    assert min(samples) <= 2, samples
    gate.release.set()
    assert set(await asyncio.gather(a, *waiters)) == {"new-access"}
    assert gate.entered == 1


# --------------------------------------------------------------------------- 6. re-grant vs refresh


async def test_reauthorization_cannot_be_overwritten_by_an_inflight_refresh(
    member: Member,
    servers: ServerApi,
    db: Db,
    adapter: Phase2Adapter,
    fake_endpoint: FakeEndpointFactory,
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refresh holds the (server, user) lock inside its POST; a re-authorization completing
    meanwhile waits for it instead of racing it, and its grant is what remains."""
    import src.mcp.oauth as mcp_oauth
    from tests.integration.sqla_phase2.test_mcp_oauth import authorize, callback, idp, location

    monkeypatch.setattr(locks, "POLL_MIN_S", 0.02)
    monkeypatch.setattr(locks, "POLL_MAX_S", 0.05)
    created = await servers.create_oauth(member)
    sid = created["id"]
    endpoint = await idp(db, fake_endpoint, sid)
    await db.seed_token(
        sid, member.id, access="old", refresh="old-refresh", expires_in=timedelta(minutes=-5)
    )
    gate = gate_refresh_post(monkeypatch, mcp_oauth, form_index=1)
    # POSTs reach the endpoint in this order: the re-authorization's code exchange (it is not
    # gated), then the released refresh.
    endpoint.push({"access_token": "reauth-access", "refresh_token": "reauth-refresh"})
    endpoint.push({"access_token": "refreshed-access", "expires_in": 3600})

    a = asyncio.ensure_future(adapter.valid_access_token(sid, member.id))
    await asyncio.wait_for(gate.started.wait(), WAIT_S)
    state = await authorize(client, member, sid)
    reauth = asyncio.ensure_future(callback(client, state))
    await asyncio.sleep(0.7)
    assert not reauth.done(), "the re-authorization stored its grant while a refresh held the lock"

    gate.release.set()
    assert await a == "refreshed-access"
    base, params = location(await asyncio.wait_for(reauth, WAIT_S))
    assert params["oauth"] == ["success"], (base, params)
    row = await db.token(sid, member.id)
    assert row is not None and row.encrypted_refresh_token is not None
    assert decrypt(row.encrypted_access_token) == "reauth-access"  # the grant is what remains
    assert decrypt(row.encrypted_refresh_token) == "reauth-refresh"
    async with db.sm() as s:  # one row per (server, user), never a duplicate
        rows = (
            await s.scalars(select(McpOAuthToken).where(McpOAuthToken.mcp_server_id == sid))
        ).all()
    assert len(rows) == 1
