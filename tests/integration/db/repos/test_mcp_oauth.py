import asyncio
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.models import McpOAuthState, McpOAuthToken
from src.db.repos import mcp_oauth as repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


def _id() -> str:
    return "t-" + secrets.token_hex(6)


def _values(access: str = "enc-a", refresh: str | None = "enc-r") -> repo.TokenValues:
    return repo.TokenValues(
        encrypted_access_token=access,
        encrypted_refresh_token=refresh,
        expires_at=utcnow() + timedelta(hours=1),
        scope="read",
        token_type="Bearer",
    )


def _state_values(
    state: str | None = None, expires_in: timedelta = timedelta(minutes=5), user_id: str = "u1"
) -> repo.StateValues:
    return repo.StateValues(
        mcp_server_id="s1",
        user_id=user_id,
        state=state or _id(),
        code_verifier="verifier",
        redirect_uri="http://x/cb",
        scope="read write",
        expires_at=utcnow() + expires_in,
    )


async def test_upsert_creates_then_updates_in_place(session: AsyncSession) -> None:
    sid, uid = _id(), _id()
    first = await repo.upsert_token(session, sid, uid, _values("a1", "r1"))
    second = await repo.upsert_token(session, sid, uid, _values("a2", None))
    assert second.id == first.id
    # no refresh token in the re-grant: the stored one is kept
    assert second.encrypted_access_token == "a2" and second.encrypted_refresh_token == "r1"
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at
    got = await repo.get_token(session, sid, uid)
    assert got is not None and got.encrypted_access_token == "a2"


async def test_regrant_without_refresh_token_or_scope_keeps_the_stored_ones(
    session: AsyncSession,
) -> None:
    sid, uid = _id(), _id()
    await repo.upsert_token(session, sid, uid, _values("a1", "r1"))
    again = repo.TokenValues(
        encrypted_access_token="a2",
        encrypted_refresh_token=None,
        expires_at=None,
        scope=None,
        token_type="Bearer",
    )
    second = await repo.upsert_token(session, sid, uid, again)
    assert second.encrypted_refresh_token == "r1" and second.scope == "read"
    assert second.expires_at is None  # expiry belongs to the new access token


async def test_regrant_with_new_refresh_token_and_scope_replaces_them(
    session: AsyncSession,
) -> None:
    sid, uid = _id(), _id()
    await repo.upsert_token(session, sid, uid, _values("a1", "r1"))
    again = repo.TokenValues(
        encrypted_access_token="a2",
        encrypted_refresh_token="r2",
        expires_at=None,
        scope="write",
        token_type="Bearer",
    )
    second = await repo.upsert_token(session, sid, uid, again)
    assert second.encrypted_refresh_token == "r2" and second.scope == "write"


async def test_first_grant_without_refresh_token_stores_null(session: AsyncSession) -> None:
    first = await repo.upsert_token(session, _id(), _id(), _values("a1", None))
    assert first.encrypted_refresh_token is None


async def test_upsert_refreshes_a_row_already_held_in_the_session(session: AsyncSession) -> None:
    sid, uid = _id(), _id()
    first = await repo.upsert_token(session, sid, uid, _values("old"))
    # Hold a strong reference: the identity map is weak.
    held = await session.get(McpOAuthToken, first.id)
    assert held is not None and held.encrypted_access_token == "old"
    result = await repo.upsert_token(session, sid, uid, _values("new"))
    assert result.encrypted_access_token == "new"
    after = await repo.get_token(session, sid, uid)
    assert after is not None and after.encrypted_access_token == "new"


async def test_update_changes_only_the_given_columns(session: AsyncSession) -> None:
    sid, uid = _id(), _id()
    tok = await repo.upsert_token(session, sid, uid, _values("a", "r"))
    new_exp = utcnow() + timedelta(hours=2)
    updated = await repo.update_token(
        session, tok.id, repo.TokenChanges(encrypted_access_token="a2", expires_at=new_exp)
    )
    assert updated is not None
    assert updated.encrypted_access_token == "a2" and updated.expires_at == new_exp
    assert updated.encrypted_refresh_token == "r"
    assert updated.scope == "read" and updated.token_type == "Bearer"
    assert updated.created_at == tok.created_at


async def test_update_missing_token_returns_none(session: AsyncSession) -> None:
    assert (
        await repo.update_token(session, "nope", repo.TokenChanges(encrypted_access_token="x"))
        is None
    )


async def test_delete_token_and_for_user(session: AsyncSession) -> None:
    uid = _id()
    a = await repo.upsert_token(session, _id(), uid, _values())
    await repo.upsert_token(session, _id(), uid, _values())
    assert await repo.delete_token(session, a.id) is True
    assert await repo.delete_token(session, a.id) is False
    assert await repo.delete_tokens_for_user(session, uid) == 1
    assert await repo.delete_tokens_for_user(session, uid) == 0


async def test_insert_token_if_absent_keeps_the_first_row(session: AsyncSession) -> None:
    row = repo.TokenInsert(id=_id(), mcp_server_id=_id(), user_id=_id(), **_values("first"))
    assert await repo.insert_token_if_absent(session, row) is True
    again = repo.TokenInsert(
        id=row["id"],
        mcp_server_id=row["mcp_server_id"],
        user_id=row["user_id"],
        **_values("second"),
    )
    assert await repo.insert_token_if_absent(session, again) is False
    got = await repo.get_token(session, row["mcp_server_id"], row["user_id"])
    assert got is not None and got.encrypted_access_token == "first"


async def test_consume_state_returns_the_row_once(session: AsyncSession) -> None:
    created = await repo.create_state(session, _state_values())
    assert await repo.get_state(session, created.state) is not None
    consumed = await repo.consume_state(session, created.state, utcnow())
    assert consumed is not None and consumed.code_verifier == "verifier"
    assert consumed.state == created.state and consumed.scope == "read write"
    assert await repo.consume_state(session, created.state, utcnow()) is None
    assert await repo.get_state(session, created.state) is None


async def test_consume_state_leaves_an_expired_row_and_returns_none(session: AsyncSession) -> None:
    created = await repo.create_state(session, _state_values(expires_in=timedelta(minutes=-1)))
    assert await repo.consume_state(session, created.state, utcnow()) is None
    assert await repo.get_state(session, created.state) is not None  # not deleted


async def test_consume_unknown_state_is_none(session: AsyncSession) -> None:
    assert await repo.consume_state(session, "never", utcnow()) is None


async def test_reap_expired_and_delete_for_user(session: AsyncSession) -> None:
    uid = _id()
    stale = await repo.create_state(
        session, _state_values(expires_in=timedelta(minutes=-1), user_id=uid)
    )
    live = await repo.create_state(session, _state_values(user_id=uid))
    assert await repo.reap_expired_states(session, utcnow()) >= 1
    assert await repo.get_state(session, stale.state) is None
    assert await repo.get_state(session, live.state) is not None
    assert await repo.delete_states_for_user(session, uid) == 1


async def test_concurrent_consumes_have_exactly_one_winner(engine: AsyncEngine) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        created = await repo.create_state(s, _state_values())
        await s.commit()
    try:

        async def attempt() -> bool:
            async with sm() as s:
                won = await repo.consume_state(s, created.state, utcnow())
                await s.commit()
                return won is not None

        results = await asyncio.gather(*(attempt() for _ in range(5)))
        assert results.count(True) == 1
    finally:
        async with sm() as s:
            await s.execute(delete(McpOAuthState).where(McpOAuthState.id == created.id))
            await s.commit()
