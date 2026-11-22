"""Cloud-storage connections: a user's linked storage account with encrypted OAuth tokens."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.ids import new_id
from src.db.models import CloudStorageConnection
from src.db.types import utcnow


@dataclass(frozen=True)
class CloudStorageConnectionRecord:
    id: str
    user_id: str
    provider: str
    account_email: str
    encrypted_access_token: str
    encrypted_refresh_token: str | None
    expires_at: datetime | None
    scope: str | None
    created_at: datetime
    updated_at: datetime


class ConnectionTokens(TypedDict):
    """What a grant stores. An upsert overwrites the access token and expiry; a NULL refresh
    token or scope keeps the stored value (Google omits the refresh token on re-consent)."""

    encrypted_access_token: str
    encrypted_refresh_token: str | None
    expires_at: datetime | None
    scope: str | None


class TokenChanges(TypedDict, total=False):
    """Columns a refresh may change; the refresh token only when Google rotated it."""

    encrypted_access_token: str
    encrypted_refresh_token: str
    expires_at: datetime | None


def _record(r: CloudStorageConnection) -> CloudStorageConnectionRecord:
    return CloudStorageConnectionRecord(
        id=r.id,
        user_id=r.user_id,
        provider=r.provider,
        account_email=r.account_email,
        encrypted_access_token=r.encrypted_access_token,
        encrypted_refresh_token=r.encrypted_refresh_token,
        expires_at=r.expires_at,
        scope=r.scope,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


async def get(session: AsyncSession, connection_id: str) -> CloudStorageConnectionRecord | None:
    row = await session.get(CloudStorageConnection, connection_id)
    return _record(row) if row else None


async def list_for_user(
    session: AsyncSession, user_id: str, provider: str | None = None
) -> list[CloudStorageConnectionRecord]:
    """A user's connections, optionally for one provider. Unordered, as before."""
    stmt = select(CloudStorageConnection).where(CloudStorageConnection.user_id == user_id)
    if provider:
        stmt = stmt.where(CloudStorageConnection.provider == provider)
    return [_record(r) for r in (await session.scalars(stmt)).all()]


async def upsert(
    session: AsyncSession,
    *,
    user_id: str,
    provider: str,
    account_email: str,
    values: ConnectionTokens,
) -> CloudStorageConnectionRecord:
    """One atomic statement; a re-grant updates the stored tokens in place, keeping the refresh
    token and scope when the new grant omits them."""
    now = utcnow()  # ON CONFLICT bypasses the ORM's onupdate, so set it here
    ins = insert(CloudStorageConnection).values(
        id=new_id(),
        user_id=user_id,
        provider=provider,
        account_email=account_email,
        created_at=now,
        updated_at=now,
        **values,
    )
    stmt = (
        ins.on_conflict_do_update(
            index_elements=[
                CloudStorageConnection.user_id,
                CloudStorageConnection.provider,
                CloudStorageConnection.account_email,
            ],
            set_={
                **values,
                # A re-grant that omits these keeps the stored value (Google and some IdPs
                # omit refresh_token and scope on re-consent); the first grant is unaffected.
                "encrypted_refresh_token": func.coalesce(
                    ins.excluded.encrypted_refresh_token,
                    CloudStorageConnection.encrypted_refresh_token,
                ),
                "scope": func.coalesce(ins.excluded.scope, CloudStorageConnection.scope),
                "updated_at": now,
            },
        )
        .returning(CloudStorageConnection)
        # Refresh a row already loaded in the session instead of returning stale values.
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one()
    return _record(row)


async def update_tokens(
    session: AsyncSession, connection_id: str, changes: TokenChanges
) -> CloudStorageConnectionRecord | None:
    stmt = (
        sa_update(CloudStorageConnection)
        .where(CloudStorageConnection.id == connection_id)
        .values(**changes, updated_at=utcnow())
        .returning(CloudStorageConnection)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def delete_for_user(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        sa_delete(CloudStorageConnection)
        .where(CloudStorageConnection.user_id == user_id)
        .returning(CloudStorageConnection.id)
    )
    return len(result.all())
