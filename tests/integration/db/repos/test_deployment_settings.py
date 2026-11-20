import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeploymentSetting
from src.db.repos import deployment_settings as repo

pytestmark = pytest.mark.integration


async def test_upsert_creates_then_updates_and_sets_updated_at(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = f"t.{secrets.token_hex(4)}"
    t1 = datetime(2026, 1, 1, 12, 0, 0, 1000, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, 12, 0, 0, 2000, tzinfo=UTC)
    times = iter([t1, t2])
    monkeypatch.setattr(repo, "utcnow", lambda: next(times))
    first = await repo.upsert(session, key, "a")
    second = await repo.upsert(session, key, "b")
    assert (first.value, first.updated_at) == ("a", t1)
    assert (second.value, second.updated_at) == ("b", t2)
    assert await repo.get(session, key) == second
    stored = await session.execute(
        select(DeploymentSetting.value, DeploymentSetting.updated_at)
        .where(DeploymentSetting.key == key)
        .execution_options(populate_existing=True)
    )
    assert tuple(stored.one()) == ("b", t2)


async def test_get_then_upsert_refreshes_loaded_row(session: AsyncSession) -> None:
    key = f"t.{secrets.token_hex(4)}"
    await repo.upsert(session, key, "old")
    # Hold a strong reference: the identity map is weak, so the repo's own
    # (discarded) instances would not stay in it.
    held = await session.get(DeploymentSetting, key)
    assert held is not None and held.value == "old"
    loaded = await repo.get(session, key)  # answered from the identity map
    assert loaded is not None and loaded.value == "old"
    updated = await repo.upsert(session, key, "new")
    assert updated.value == "new"
    after = await repo.get(session, key)
    assert after is not None and after.value == "new"


async def test_get_missing_is_none_and_list_is_sorted(session: AsyncSession) -> None:
    assert await repo.get(session, "missing-" + secrets.token_hex(4)) is None
    await repo.upsert(session, "zz." + secrets.token_hex(3), "1")
    await repo.upsert(session, "aa." + secrets.token_hex(3), "1")
    keys = [r.key for r in await repo.list_all(session)]
    assert keys == sorted(keys)
