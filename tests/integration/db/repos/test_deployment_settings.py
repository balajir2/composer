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
