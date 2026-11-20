import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import LlmApiKey
from src.db.repos import llm_api_keys as repo

pytestmark = pytest.mark.integration


def _provider() -> str:
    # provider is free text in the table; the route allow-list is not involved
    return f"p1-test-{secrets.token_hex(4)}"


async def test_upsert_reports_created_then_updated(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _provider()
    t1 = datetime(2026, 1, 1, 12, 0, 0, 1000, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, 12, 0, 0, 2000, tzinfo=UTC)
    times = iter([t1, t2])
    monkeypatch.setattr(repo, "utcnow", lambda: next(times))
    first = await repo.upsert(session, provider, encrypted_key="e1", key_prefix="k1")
    second = await repo.upsert(session, provider, encrypted_key="e2", key_prefix="k2")
    assert first.created is True and second.created is False
    assert second.record.id == first.record.id and second.record.id
    assert second.record.key_prefix == "k2" and second.record.encrypted_key == "e2"
    assert first.record.updated_at == t1 and second.record.updated_at == t2
    assert second.record.created_at == t1  # created_at is not touched by the update
    assert await repo.exists(session, provider) is True


async def test_get_then_upsert_refreshes_loaded_row(session: AsyncSession) -> None:
    provider = _provider()
    await repo.upsert(session, provider, encrypted_key="old", key_prefix="o")
    # Hold a strong reference: the identity map is weak.
    loaded = await repo.get(session, provider)
    assert loaded is not None
    held = await session.get(LlmApiKey, loaded.id)
    assert held is not None and held.encrypted_key == "old"
    loaded = await repo.get(session, provider)
    assert loaded is not None and loaded.encrypted_key == "old"
    result = await repo.upsert(session, provider, encrypted_key="new", key_prefix="n")
    assert result.record.encrypted_key == "new"
    after = await repo.get(session, provider)
    assert after is not None and after.encrypted_key == "new" and after.key_prefix == "n"


async def test_list_is_ordered_by_provider(session: AsyncSession) -> None:
    await repo.upsert(session, "zz-" + secrets.token_hex(3), encrypted_key="e", key_prefix="k")
    await repo.upsert(session, "aa-" + secrets.token_hex(3), encrypted_key="e", key_prefix="k")
    providers = [r.provider for r in await repo.list_keys(session)]
    assert providers == sorted(providers)


async def test_delete_and_missing(session: AsyncSession) -> None:
    provider = _provider()
    await repo.upsert(session, provider, encrypted_key="e", key_prefix="k")
    assert await repo.delete(session, provider) is True
    assert await repo.get(session, provider) is None
    assert await repo.exists(session, provider) is False
    assert await repo.delete(session, provider) is False
