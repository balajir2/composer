"""Hashed per-user API keys ('ck_...') for external workflow invocation."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_already_exists
from src.db.models import ApiKey


@dataclass(frozen=True)
class ApiKeyRecord:
    id: str
    user_id: str
    label: str
    key_hash: str
    key_prefix: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


def _record(k: ApiKey) -> ApiKeyRecord:
    return ApiKeyRecord(
        id=k.id,
        user_id=k.user_id,
        label=k.label,
        key_hash=k.key_hash,
        key_prefix=k.key_prefix,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
        expires_at=k.expires_at,
        revoked_at=k.revoked_at,
    )


async def create(
    session: AsyncSession,
    *,
    user_id: str,
    label: str,
    key_hash: str,
    key_prefix: str,
    expires_at: datetime | None,
) -> ApiKeyRecord:
    row = ApiKey(
        user_id=user_id,
        label=label,
        key_hash=key_hash,
        key_prefix=key_prefix,
        expires_at=expires_at,
    )
    # key_hash is unique. A collision is astronomically unlikely, so AlreadyExists
    # is left to propagate (500); going through add_or_already_exists keeps the
    # session usable if it ever fires.
    await add_or_already_exists(session, row)
    return _record(row)


async def list_for_user(session: AsyncSession, user_id: str) -> list[ApiKeyRecord]:
    stmt = select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    return [_record(k) for k in await session.scalars(stmt)]


async def get(session: AsyncSession, key_id: str) -> ApiKeyRecord | None:
    row = await session.get(ApiKey, key_id)
    return _record(row) if row else None


async def first_by_prefix(session: AsyncSession, prefix: str) -> ApiKeyRecord | None:
    """Prisma's find_first had no order; this one is deterministic (oldest first)."""
    stmt = (
        select(ApiKey)
        .where(ApiKey.key_prefix == prefix)
        .order_by(ApiKey.created_at, ApiKey.id)
        .limit(1)
    )
    row = await session.scalar(stmt)
    return _record(row) if row else None


async def revoke_if_active(session: AsyncSession, key_id: str, at: datetime) -> bool:
    """Guarded: True only for the caller that flips revoked_at from NULL."""
    stmt = (
        sa_update(ApiKey)
        .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=at)
        .returning(ApiKey.id)
    )
    return (await session.execute(stmt)).first() is not None


async def revoke_all_active_for_user(session: AsyncSession, user_id: str, at: datetime) -> int:
    stmt = (
        sa_update(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=at)
        .returning(ApiKey.id)
    )
    return len((await session.execute(stmt)).all())


async def touch_last_used(session: AsyncSession, key_id: str, at: datetime) -> None:
    await session.execute(sa_update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=at))
