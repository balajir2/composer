import secrets
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.models import McpServer, User, UserRole
from src.db.repos import mcp_servers as repo
from src.db.repos.mcp_servers import McpServerCreate

pytestmark = pytest.mark.integration


def _uid() -> str:
    return "u" + secrets.token_hex(4)


async def _user(session: AsyncSession) -> str:
    uid = _uid()
    session.add(User(id=uid, email=f"{uid}@example.com", password_hash="x", role=UserRole.MEMBER))
    await session.flush()
    return uid


def _values(**over: Any) -> McpServerCreate:
    base: dict[str, Any] = {
        "name": "S",
        "url": "https://mcp.example.com/mcp",
        "auth_type": "none",
    }
    base.update(over)
    return cast("McpServerCreate", base)


async def test_create_get_and_defaults(session: AsyncSession) -> None:
    created = await repo.create(session, _values(user_id=None, description="d", is_shared=True))
    assert created.id
    assert created.connection_status == "untested"
    assert created.enabled is True and created.is_official is False and created.is_shared is True
    assert created.oauth_config is None and created.tools is None and created.headers is None
    got = await repo.get(session, created.id)
    assert got == created
    assert await repo.get(session, "missing") is None


async def test_create_with_explicit_id_duplicate_raises(session: AsyncSession) -> None:
    sid = "s" + secrets.token_hex(4)
    await repo.create(session, _values(id=sid))
    with pytest.raises(AlreadyExists):
        await repo.create(session, _values(id=sid))
    assert await repo.get(session, sid) is not None  # session still usable


async def test_list_visible_is_own_or_shared(session: AsyncSession) -> None:
    me = await _user(session)
    other = await _user(session)
    mine = await repo.create(session, _values(user_id=me))
    shared = await repo.create(session, _values(user_id=other, is_shared=True))
    private = await repo.create(session, _values(user_id=other))
    ids = {r.id for r in await repo.list_visible(session, me)}
    assert mine.id in ids and shared.id in ids and private.id not in ids


async def test_update_changes_only_given_keys(session: AsyncSession) -> None:
    s = await repo.create(
        session, _values(user_id=None, description="keep", oauth_config={"a": 1}, tools=[{"n": 1}])
    )
    updated = await repo.update(session, s.id, {"is_shared": True})
    assert updated is not None
    assert updated.is_shared is True
    assert updated.description == "keep" and updated.oauth_config == {"a": 1}
    assert updated.tools == [{"n": 1}] and updated.connection_status == "untested"
    assert await repo.update(session, "missing", {"is_shared": True}) is None


async def test_update_sets_updated_at_deterministically(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = await repo.create(session, _values())
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    updated = await repo.update(session, s.id, {"connection_status": "connected"})
    assert updated is not None and updated.updated_at == t
    got = await repo.get(session, s.id)
    assert got is not None and got.updated_at == t and got.connection_status == "connected"


async def test_update_refreshes_row_already_loaded_in_session(session: AsyncSession) -> None:
    s = await repo.create(session, _values(user_id=None))
    # Hold a strong reference: the identity map is weak, so without it this proves nothing.
    held = await session.get(McpServer, s.id)
    assert held is not None and held.is_shared is False
    updated = await repo.update(session, s.id, {"is_shared": True})
    assert updated is not None and updated.is_shared is True
    assert held.is_shared is True
    after = await repo.get(session, s.id)
    assert after is not None and after.is_shared is True


async def test_json_round_trip_and_none_is_sql_null(session: AsyncSession) -> None:
    cfg = {"authorizeUrl": "https://a", "scopes": ["x", "y"], "nested": {"k": None}}
    tools = [{"name": "t", "description": "d"}]
    s = await repo.create(session, _values(oauth_config=cfg, tools=tools, headers={"X-A": "b"}))
    got = await repo.get(session, s.id)
    assert got is not None
    assert got.oauth_config == cfg and got.tools == tools and got.headers == {"X-A": "b"}

    cleared = await repo.update(session, s.id, {"oauth_config": None, "tools": None})
    assert cleared is not None and cleared.oauth_config is None and cleared.tools is None
    nulls = (
        await session.execute(
            text(
                "SELECT oauth_config IS NULL, tools IS NULL, headers IS NULL "
                "FROM mcp_servers WHERE id = :i"
            ),
            {"i": s.id},
        )
    ).one()
    assert tuple(nulls) == (True, True, False)

    bare = await repo.create(session, _values())
    row = (
        await session.execute(
            text("SELECT oauth_config IS NULL, tools IS NULL FROM mcp_servers WHERE id = :i"),
            {"i": bare.id},
        )
    ).one()
    assert tuple(row) == (True, True)


async def test_delete(session: AsyncSession) -> None:
    s = await repo.create(session, _values())
    assert await repo.delete(session, s.id) is True
    assert await repo.get(session, s.id) is None
    assert await repo.delete(session, s.id) is False


async def test_delete_for_user_counts_only_that_users_rows(session: AsyncSession) -> None:
    me = await _user(session)
    other = await _user(session)
    await repo.create(session, _values(user_id=me))
    await repo.create(session, _values(user_id=me))
    keep = await repo.create(session, _values(user_id=other))
    assert await repo.delete_for_user(session, me) == 2
    assert await repo.delete_for_user(session, me) == 0
    assert await repo.get(session, keep.id) is not None


async def test_claim_orphans_by_email_counts_and_is_idempotent(session: AsyncSession) -> None:
    email = f"{_uid()}@example.com"
    me = await _user(session)
    other = await _user(session)
    a = await repo.create(session, _values(user_id=None, original_owner_email=email))
    b = await repo.create(session, _values(user_id=None, original_owner_email=email))
    owned = await repo.create(session, _values(user_id=other, original_owner_email=email))
    elsewhere = await repo.create(session, _values(user_id=None, original_owner_email="x@y.z"))
    assert await repo.claim_orphans_by_email(session, email, me) == 2
    assert await repo.claim_orphans_by_email(session, email, me) == 0
    for sid in (a.id, b.id):
        got = await repo.get(session, sid)
        assert got is not None and got.user_id == me
    got_owned = await repo.get(session, owned.id)
    assert got_owned is not None and got_owned.user_id == other
    got_else = await repo.get(session, elsewhere.id)
    assert got_else is not None and got_else.user_id is None


async def test_insert_if_absent(session: AsyncSession) -> None:
    sid = "s" + secrets.token_hex(4)
    assert await repo.insert_if_absent(session, _values(id=sid, name="first")) is True
    assert await repo.insert_if_absent(session, _values(id=sid, name="second")) is False
    got = await repo.get(session, sid)
    assert got is not None and got.name == "first"
