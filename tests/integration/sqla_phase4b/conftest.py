"""Fixtures for the SQLAlchemy phase 4b behaviour oracle (in-app resume, emailed-link approval).

Written against the post-4a code, where these routes still use Prisma. These tests must keep
passing UNCHANGED after the routes move to SQLAlchemy, except the tests named ``..._today_...``
(they pin behaviour the plan's decisions DB1, DB2 and DB6 deliberately change in Task 3). There is
no adapter: every behaviour 4b changes is reached over HTTP. The approval-expiry sweep is called
through 4a's Phase4aAdapter (already on SQLAlchemy; 4b does not touch it).

Seams: ``src.api.executions.enqueue_execution`` and ``src.api.approval_email.enqueue_execution``
are monkeypatched with ``install_resume_enqueue`` (records calls, optionally raises); otherwise the
autouse driver from tests/integration/conftest.py runs claim-and-run inline over ASGI.

Approval tokens are bearer credentials: nothing here prints or logs one.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Coroutine, Sequence
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
    "adapter",
    "admin",
    "app",
    "db",
    "exec_api",
    "make_user",
    "member",
    "phase4a_env",
    "second_member",
    "sessionmaker_",
]

APPROVER = "approver@example.com"


# The three autouse fixtures below are copied from tests/integration/sqla_phase4a/conftest.py
# (lines ~252-277); keep them identical. Autouse fixtures of another conftest do not apply here.
@pytest.fixture(autouse=True)
def _network_lock(httpx_mock: HTTPXMock) -> None:  # pyright: ignore[reportUnusedFunction]
    del httpx_mock


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


async def confirm(client: AsyncClient, token: str) -> Response:
    return await client.post(f"/approvals/email/{token}/confirm")


async def open_link(client: AsyncClient, token: str) -> Response:
    return await client.get(f"/approvals/email/{token}")


def location(resp: Response) -> str:
    return resp.headers["location"]


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


async def snap(db: Db, execution_id: str) -> dict[str, Any]:
    """Every column of the execution row."""
    return Db.snapshot(await db.must(execution_id))


def install_resume_enqueue(
    monkeypatch: pytest.MonkeyPatch, rec: EnqueueRecorder
) -> EnqueueRecorder:
    monkeypatch.setattr("src.api.executions.enqueue_execution", rec)
    monkeypatch.setattr("src.api.approval_email.enqueue_execution", rec)
    return rec


def same_except(before: dict[str, Any], after: dict[str, Any], *changed: str) -> None:
    """Every column outside ``changed`` is identical."""
    assert set(before) == set(after)
    a = {k: v for k, v in before.items() if k not in changed}
    b = {k: v for k, v in after.items() if k not in changed}
    assert a == b


# --------------------------------------------------------------------------- lock-and-hold seam
#
# Every route first runs ``enforce`` (a ``SELECT ... FOR UPDATE`` on the rate-limit bucket), which
# serialises same-user / same-IP requests, so plain ``asyncio.gather`` races mostly end on the
# read-side status check and never reach the guarded write. The seam below parks requests exactly
# between their read and their guarded write: a separate session holds ``FOR UPDATE`` on the
# execution row; each request reads the row (plain SELECT, not blocked), then blocks on the guarded
# UPDATE. ``wait_blocked`` proves they are all parked, then the holder (optionally changing the row)
# commits and they race for real.


@contextlib.asynccontextmanager
async def hold_row(db: Db, execution_id: str) -> AsyncGenerator[tuple[AsyncSession, int]]:
    """Lock the execution row FOR UPDATE in a separate session. Yields (session, backend pid);
    the lock is released (rolled back unless the caller committed) on exit."""
    async with db.sm() as session:
        try:
            pid = (await session.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            await session.execute(
                text("SELECT id FROM workflow_executions WHERE id = :e FOR UPDATE"),
                {"e": execution_id},
            )
            yield session, int(pid)
        finally:
            await session.rollback()


async def wait_blocked(db: Db, pid: int, n: int, timeout: float = 10.0) -> None:
    """Wait until at least ``n`` backends are blocked behind backend ``pid`` (directly, or queued
    behind another waiter: Postgres lists earlier waiters as blockers too)."""
    deadline = time.monotonic() + timeout
    while True:
        async with db.sm() as s:
            count = (
                await s.execute(
                    text(
                        "WITH RECURSIVE chain(pid) AS ("
                        " SELECT pid FROM pg_stat_activity WHERE :pid = ANY(pg_blocking_pids(pid))"
                        " UNION"
                        " SELECT a.pid FROM pg_stat_activity a JOIN chain c"
                        " ON c.pid = ANY(pg_blocking_pids(a.pid))"
                        ") SELECT count(*) FROM chain"
                    ),
                    {"pid": pid},
                )
            ).scalar_one()
        if count >= n:
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"only {count} of {n} requests blocked behind the row lock")
        await asyncio.sleep(0.05)


async def race_behind_lock(
    db: Db,
    execution_id: str,
    requests: Sequence[Coroutine[Any, Any, Response]],
    *,
    holder_action: Callable[[AsyncSession], Awaitable[None]] | None = None,
    timeout: float = 30.0,
) -> list[Response]:
    """Park every request behind the row lock, let ``holder_action`` change the row (optional),
    commit, and return the responses in request order."""
    async with hold_row(db, execution_id) as (holder, pid):
        tasks = [asyncio.ensure_future(asyncio.wait_for(r, timeout)) for r in requests]
        try:
            await wait_blocked(db, pid, len(tasks))
            if holder_action is not None:
                await holder_action(holder)
            await holder.commit()
            return list(await asyncio.gather(*tasks))
        finally:
            # Release the row lock before cancelling, so a parked request's own cleanup can't
            # wait forever on the lock (a no-op when the commit above already ran).
            await holder.rollback()
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ += ["Db", "EnqueueRecorder", "Member"]
