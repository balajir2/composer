"""E2E test-account cleanup endpoint: removes the account and its rows in all four phase 2 tables.

Test group 13 of the phase 2 plan. `DELETE /internal/test-users/me` is mounted for every
environment except ``production`` (src/main.py); the phase 2 env uses ``development``.
"""

import secrets

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db.models import User
from tests.integration.sqla_phase2.conftest import HTTPX_MARK, Db, MakeUser, Member, ServerApi

pytestmark = [pytest.mark.integration, HTTPX_MARK]

URL = "/internal/test-users/me"


async def _seed_all_four(servers: ServerApi, db: Db, user: Member) -> dict[str, str]:
    server = await servers.create_oauth(user)
    token_id = await db.seed_token(server["id"], user.id)
    state = await db.seed_state(server["id"], user.id)
    connection_id = await db.seed_connection(user.id)
    return {"server": server["id"], "token": token_id, "state": state, "connection": connection_id}


async def test_cleanup_deletes_account_and_all_its_integration_rows(
    client: AsyncClient,
    make_user: MakeUser,
    member: Member,
    servers: ServerApi,
    db: Db,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    e2e = await make_user(email=f"pw-{secrets.token_hex(5)}@example.com")
    mine = await _seed_all_four(servers, db, e2e)
    await servers.create(e2e, isShared=True)  # a shared server of the e2e user goes too
    bystander = await _seed_all_four(servers, db, member)
    assert await db.count_for_user(e2e.id) == {
        "servers": 2, "tokens": 1, "states": 1, "connections": 1,
    }  # fmt: skip

    r = await client.delete(URL, headers=e2e.headers)
    assert r.status_code == 204 and r.content == b""

    assert await db.count_for_user(e2e.id) == {
        "servers": 0, "tokens": 0, "states": 0, "connections": 0,
    }  # fmt: skip
    assert await db.server(mine["server"]) is None
    assert await db.token(mine["server"], e2e.id) is None
    assert await db.state(mine["state"]) is None
    assert await db.connection(mine["connection"]) is None
    async with sessionmaker_() as s:
        assert await s.scalar(select(User).where(User.id == e2e.id)) is None

    # nothing of another member's was touched
    assert await db.count_for_user(member.id) == {
        "servers": 1, "tokens": 1, "states": 1, "connections": 1,
    }  # fmt: skip
    assert await db.server(bystander["server"]) is not None
    assert await db.connection(bystander["connection"]) is not None

    # the deleted account's token no longer authenticates
    again = await client.delete(URL, headers=e2e.headers)
    assert again.status_code == 401


async def test_cleanup_refuses_real_accounts_and_keeps_their_rows(
    client: AsyncClient, member: Member, servers: ServerApi, db: Db
) -> None:
    seeded = await _seed_all_four(servers, db, member)
    r = await client.delete(URL, headers=member.headers)
    assert r.status_code == 403
    assert r.json() == {
        "detail": "this endpoint only deletes accounts matching the e2e test-user pattern"
    }
    assert await db.count_for_user(member.id) == {
        "servers": 1, "tokens": 1, "states": 1, "connections": 1,
    }  # fmt: skip
    assert await db.server(seeded["server"]) is not None
