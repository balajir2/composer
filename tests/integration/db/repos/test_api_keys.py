import asyncio
import secrets

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.db.errors import AlreadyExists
from src.db.models import User
from src.db.repos import api_keys as repo
from src.db.repos import users as users_repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


def _h() -> str:
    return "h-" + secrets.token_hex(8)


async def _user(session: AsyncSession) -> str:
    u = await users_repo.create(
        session,
        email=f"k-{secrets.token_hex(5)}@example.com",
        password_hash=None,
        display_name=None,
    )
    return u.id


async def test_create_list_and_revoke_once(session: AsyncSession) -> None:
    uid = await _user(session)
    pa, pb = "ck_" + secrets.token_hex(4), "ck_" + secrets.token_hex(4)
    a = await repo.create(
        session, user_id=uid, label="a", key_hash=_h(), key_prefix=pa, expires_at=None
    )
    b = await repo.create(
        session, user_id=uid, label="b", key_hash=_h(), key_prefix=pb, expires_at=None
    )
    assert {k.id for k in await repo.list_for_user(session, uid)} == {a.id, b.id}
    assert await repo.first_by_prefix(session, pa) == a
    now = utcnow()
    assert await repo.revoke_if_active(session, a.id, now) is True
    assert await repo.revoke_if_active(session, a.id, now) is False
    assert await repo.revoke_all_active_for_user(session, uid, now) == 1
    await repo.touch_last_used(session, b.id, now)
    got = await repo.get(session, b.id)
    assert got is not None and got.last_used_at == now and got.revoked_at == now


async def test_duplicate_key_hash_raises_and_session_stays_usable(session: AsyncSession) -> None:
    uid = await _user(session)
    h = _h()
    await repo.create(
        session, user_id=uid, label="a", key_hash=h, key_prefix="ck_x", expires_at=None
    )
    with pytest.raises(AlreadyExists):
        await repo.create(
            session, user_id=uid, label="b", key_hash=h, key_prefix="ck_y", expires_at=None
        )
    assert len(await repo.list_for_user(session, uid)) == 1


async def test_concurrent_revocations_have_exactly_one_winner(engine: AsyncEngine) -> None:
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        uid = await _user(s)
        key = await repo.create(
            s, user_id=uid, label="r", key_hash=_h(), key_prefix="ck_race", expires_at=None
        )
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
            await s.execute(delete(User).where(User.id == uid))  # cascades api_keys
            await s.commit()
