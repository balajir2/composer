import secrets
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists
from src.db.models import UserRole
from src.db.repos import users as repo

pytestmark = pytest.mark.integration


def _email() -> str:
    return f"repo-{secrets.token_hex(5)}@example.com"


async def test_create_get_and_duplicate(session: AsyncSession) -> None:
    email = _email()
    u = await repo.create(session, email=email, password_hash="h", display_name=None)
    assert u.role is UserRole.MEMBER and u.is_active and not u.must_change_password
    assert await repo.get_by_email(session, email) == u
    assert await repo.get_by_id(session, u.id) == u
    with pytest.raises(AlreadyExists):
        await repo.create(session, email=email, password_hash="h", display_name=None)
    # the session is still usable after the duplicate
    assert await repo.get_by_id(session, u.id) == u


async def test_search_keeps_unescaped_wildcards_and_skips_inactive(session: AsyncSession) -> None:
    tag = secrets.token_hex(4)
    hit = await repo.create(
        session, email=f"x-{tag}@example.com", password_hash=None, display_name=None
    )
    off = await repo.create(
        session, email=f"y-{tag}@example.com", password_hash=None, display_name=None
    )
    await repo.set_active(session, off.id, False)
    assert [u.id for u in await repo.search_active(session, tag.upper(), 20)] == [hit.id]
    # Prisma does not escape LIKE wildcards: "_" matches any one character.
    assert [u.id for u in await repo.search_active(session, f"x_{tag}", 20)] == [hit.id]


async def test_setters(session: AsyncSession) -> None:
    u = await repo.create(session, email=_email(), password_hash="h", display_name="D")
    promoted = await repo.set_role(session, u.id, UserRole.ADMIN)
    assert promoted is not None and promoted.role is UserRole.ADMIN
    assert await repo.set_password(session, u.id, "h2", must_change=True) is True
    after = await repo.get_by_id(session, u.id)
    assert after is not None and after.password_hash == "h2" and after.must_change_password
    assert await repo.set_role(session, "missing", UserRole.ADMIN) is None
    assert await repo.set_active(session, "missing", False) is None
    assert await repo.set_password(session, "missing", "x", must_change=False) is False
    assert await repo.delete(session, u.id) is True
    assert await repo.get_by_id(session, u.id) is None
    assert await repo.delete(session, u.id) is False


async def test_list_all_orders_by_email(session: AsyncSession) -> None:
    tag = secrets.token_hex(4)
    b = await repo.create(session, email=f"b-{tag}@e.com", password_hash=None, display_name=None)
    a = await repo.create(session, email=f"a-{tag}@e.com", password_hash=None, display_name=None)
    ours = [u.id for u in await repo.list_all(session) if tag in u.email]
    assert ours == [a.id, b.id]


async def test_update_sets_updated_at_deterministically(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    u = await repo.create(session, email=_email(), password_hash=None, display_name=None)
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    updated = await repo.set_active(session, u.id, False)
    assert updated is not None and updated.updated_at == t and updated.is_active is False
