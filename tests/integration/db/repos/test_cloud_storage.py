import secrets
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CloudStorageConnection
from src.db.repos import cloud_storage as repo
from src.db.types import utcnow

pytestmark = pytest.mark.integration


def _id() -> str:
    return "c-" + secrets.token_hex(6)


def _values(access: str = "enc-a", refresh: str | None = "enc-r") -> repo.ConnectionTokens:
    return repo.ConnectionTokens(
        encrypted_access_token=access,
        encrypted_refresh_token=refresh,
        expires_at=utcnow() + timedelta(hours=1),
        scope="drive.readonly",
    )


async def _upsert(
    session: AsyncSession,
    uid: str,
    email: str = "a@example.com",
    values: repo.ConnectionTokens | None = None,
) -> repo.CloudStorageConnectionRecord:
    return await repo.upsert(
        session,
        user_id=uid,
        provider="google-drive",
        account_email=email,
        values=values or _values(),
    )


async def test_upsert_creates_then_updates_in_place(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid = _id()
    t1 = datetime(2026, 1, 1, 12, 0, 0, 1000, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, 12, 0, 0, 2000, tzinfo=UTC)
    times = iter([t1, t2])
    monkeypatch.setattr(repo, "utcnow", lambda: next(times))
    first = await _upsert(session, uid, values=_values("a1", "r1"))
    second = await _upsert(session, uid, values=_values("a2", None))
    assert second.id == first.id
    assert second.encrypted_access_token == "a2"
    # A re-grant without a refresh token clears the stored one (pinned, like-for-like).
    assert second.encrypted_refresh_token is None
    assert first.created_at == t1 and second.created_at == t1
    assert first.updated_at == t1 and second.updated_at == t2


async def test_upsert_refreshes_a_row_already_held_in_the_session(session: AsyncSession) -> None:
    uid = _id()
    first = await _upsert(session, uid, values=_values("old"))
    # Hold a strong reference: the identity map is weak.
    held = await session.get(CloudStorageConnection, first.id)
    assert held is not None and held.encrypted_access_token == "old"
    result = await _upsert(session, uid, values=_values("new"))
    assert result.encrypted_access_token == "new"
    after = await repo.get(session, first.id)
    assert after is not None and after.encrypted_access_token == "new"


async def test_upsert_distinguishes_account_email(session: AsyncSession) -> None:
    uid = _id()
    a = await _upsert(session, uid, "a@example.com")
    b = await _upsert(session, uid, "b@example.com")
    assert a.id != b.id


async def test_update_tokens_changes_only_the_given_columns(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = await _upsert(session, _id(), values=_values("a", "r"))
    new_exp = utcnow() + timedelta(hours=2)
    t = datetime(2026, 3, 4, 5, 6, 7, 8000, tzinfo=UTC)
    monkeypatch.setattr(repo, "utcnow", lambda: t)
    held = await session.get(CloudStorageConnection, conn.id)
    assert held is not None
    updated = await repo.update_tokens(
        session, conn.id, repo.TokenChanges(encrypted_access_token="a2", expires_at=new_exp)
    )
    assert updated is not None
    assert updated.encrypted_access_token == "a2" and updated.expires_at == new_exp
    assert updated.encrypted_refresh_token == "r"
    assert updated.scope == "drive.readonly"
    assert updated.created_at == conn.created_at and updated.updated_at == t
    assert held.encrypted_access_token == "a2"  # populate_existing refreshed the held row


async def test_update_tokens_can_rotate_the_refresh_token(session: AsyncSession) -> None:
    conn = await _upsert(session, _id(), values=_values("a", "r"))
    updated = await repo.update_tokens(
        session,
        conn.id,
        repo.TokenChanges(encrypted_access_token="a2", encrypted_refresh_token="r2"),
    )
    assert updated is not None and updated.encrypted_refresh_token == "r2"


async def test_update_tokens_missing_connection_returns_none(session: AsyncSession) -> None:
    assert (
        await repo.update_tokens(session, "nope", repo.TokenChanges(encrypted_access_token="x"))
        is None
    )


async def test_get_missing_is_none(session: AsyncSession) -> None:
    assert await repo.get(session, "nope") is None


async def test_list_for_user_scopes_to_user_and_filters_provider(session: AsyncSession) -> None:
    uid, other = _id(), _id()
    a = await _upsert(session, uid, "a@example.com")
    b = await repo.upsert(
        session, user_id=uid, provider="dropbox", account_email="b@example.com", values=_values()
    )
    await _upsert(session, other, "a@example.com")
    assert {r.id for r in await repo.list_for_user(session, uid)} == {a.id, b.id}
    only = await repo.list_for_user(session, uid, "google-drive")
    assert [r.id for r in only] == [a.id]
    assert await repo.list_for_user(session, "nobody") == []


async def test_delete_for_user(session: AsyncSession) -> None:
    uid = _id()
    await _upsert(session, uid, "a@example.com")
    await _upsert(session, uid, "b@example.com")
    assert await repo.delete_for_user(session, uid) == 2
    assert await repo.delete_for_user(session, uid) == 0
