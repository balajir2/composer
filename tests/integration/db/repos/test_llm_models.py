import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.models import LlmModel
from src.db.repos import llm_models as repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


async def test_create_duplicate_raises_already_exists_and_session_stays_usable(
    session: AsyncSession,
) -> None:
    p = "p" + secrets.token_hex(3)
    await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    with pytest.raises(AlreadyExists) as info:
        await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    assert info.value.constraint == "llm_models_provider_model_id_key"
    assert [m.model_id for m in await repo.list_models(session, provider=p)] == ["m"]


async def test_update_changes_only_given_fields(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label="L", enabled=True)
    updated = await repo.update(session, m.id, enabled=False)
    assert updated is not None and updated.label == "L" and updated.enabled is False
    assert await repo.update(session, "missing", enabled=False) is None


async def test_update_sets_updated_at_deterministically(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    updated = await repo.update(session, m.id, label="new")
    assert updated is not None and updated.updated_at == t and updated.label == "new"
    got = await repo.get_by_id(session, m.id)
    assert got is not None and got.updated_at == t


async def test_update_refreshes_row_already_loaded_in_session(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label="old", enabled=True)
    # Hold a strong reference: the identity map is weak, so without it this proves nothing.
    held = await session.get(LlmModel, m.id)
    assert held is not None and held.label == "old"
    updated = await repo.update(session, m.id, label="new")
    assert updated is not None and updated.label == "new"
    assert held.label == "new"
    after = await repo.get_by_id(session, m.id)
    assert after is not None and after.label == "new"


async def test_enabled_only_and_ordering(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    await repo.create(session, provider=p, model_id="b", label=None, enabled=True)
    await repo.create(session, provider=p, model_id="a", label=None, enabled=True)
    await repo.create(session, provider=p, model_id="c", label=None, enabled=False)
    got = await repo.list_models(session, provider=p, enabled_only=True)
    assert [m.model_id for m in got] == ["a", "b"]
    assert [m.model_id for m in await repo.list_models(session, provider=p)] == ["a", "b", "c"]


async def test_record_verification_can_disable(session: AsyncSession) -> None:
    p = "p" + secrets.token_hex(3)
    m = await repo.create(session, provider=p, model_id="m", label=None, enabled=True)
    now = utcnow()
    r = await repo.record_verification(
        session, m.id, status="unavailable", message="gone", verified_at=now, disable=True
    )
    assert r is not None
    assert (r.verification_status, r.verification_message, r.enabled, r.verified_at) == (
        "unavailable",
        "gone",
        False,
        now,
    )
    assert await repo.get_by_provider_model(session, p, "m") == r
    assert await repo.delete(session, m.id) is True
    assert await repo.delete(session, m.id) is False
