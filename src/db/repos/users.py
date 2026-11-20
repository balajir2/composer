"""Application users."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_already_exists
from src.db.models import User, UserRole
from src.db.types import utcnow


@dataclass(frozen=True)
class UserRecord:
    id: str
    email: str
    password_hash: str | None
    display_name: str | None
    role: UserRole
    is_active: bool
    must_change_password: bool
    created_at: datetime
    updated_at: datetime


def _record(u: User) -> UserRecord:
    return UserRecord(
        id=u.id,
        email=u.email,
        password_hash=u.password_hash,
        display_name=u.display_name,
        role=u.role,
        is_active=u.is_active,
        must_change_password=u.must_change_password,
        created_at=u.created_at,
        updated_at=u.updated_at,
    )


def _contains_pattern(q: str) -> str:
    """Prisma's `contains` semantics: wildcards in ``q`` are NOT escaped (pinned by the oracle)."""
    return f"%{q}%"


async def get_by_id(session: AsyncSession, user_id: str) -> UserRecord | None:
    row = await session.get(User, user_id)
    return _record(row) if row else None


async def get_by_email(session: AsyncSession, email: str) -> UserRecord | None:
    row = await session.scalar(select(User).where(User.email == email))
    return _record(row) if row else None


async def create(
    session: AsyncSession, *, email: str, password_hash: str | None, display_name: str | None
) -> UserRecord:
    row = User(email=email, password_hash=password_hash, display_name=display_name)
    await add_or_already_exists(session, row)
    return _record(row)


async def list_all(session: AsyncSession) -> list[UserRecord]:
    return [_record(u) for u in await session.scalars(select(User).order_by(User.email))]


async def search_active(session: AsyncSession, q: str, limit: int) -> list[UserRecord]:
    pattern = _contains_pattern(q)
    stmt = (
        select(User)
        .where(
            User.is_active.is_(True),
            or_(User.email.ilike(pattern), User.display_name.ilike(pattern)),
        )
        .order_by(User.email)
        .limit(limit)
    )
    return [_record(u) for u in await session.scalars(stmt)]


class _Changes(TypedDict, total=False):
    role: UserRole
    is_active: bool
    password_hash: str
    must_change_password: bool


async def _update(session: AsyncSession, user_id: str, values: _Changes) -> UserRecord | None:
    stmt = (
        sa_update(User)
        .where(User.id == user_id)
        .values(**values, updated_at=utcnow())
        .returning(User)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def set_role(session: AsyncSession, user_id: str, role: UserRole) -> UserRecord | None:
    return await _update(session, user_id, {"role": role})


async def set_active(session: AsyncSession, user_id: str, active: bool) -> UserRecord | None:
    return await _update(session, user_id, {"is_active": active})


async def set_password(
    session: AsyncSession, user_id: str, password_hash: str, *, must_change: bool
) -> bool:
    changed = await _update(
        session,
        user_id,
        {"password_hash": password_hash, "must_change_password": must_change},
    )
    return changed is not None


async def delete(session: AsyncSession, user_id: str) -> bool:
    result = await session.execute(sa_delete(User).where(User.id == user_id).returning(User.id))
    return result.first() is not None
