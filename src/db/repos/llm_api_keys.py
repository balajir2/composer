"""Encrypted LLM/tool provider API keys, one row per provider."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Boolean, literal_column, select
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.ids import new_id
from src.db.models import LlmApiKey
from src.db.types import utcnow


@dataclass(frozen=True)
class LlmApiKeyRecord:
    id: str
    provider: str
    encrypted_key: str
    key_prefix: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class UpsertResult:
    record: LlmApiKeyRecord
    created: bool


def _record(r: LlmApiKey) -> LlmApiKeyRecord:
    return LlmApiKeyRecord(
        id=r.id,
        provider=r.provider,
        encrypted_key=r.encrypted_key,
        key_prefix=r.key_prefix,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


async def list_keys(session: AsyncSession) -> list[LlmApiKeyRecord]:
    rows = await session.scalars(select(LlmApiKey).order_by(LlmApiKey.provider))
    return [_record(r) for r in rows]


async def get(session: AsyncSession, provider: str) -> LlmApiKeyRecord | None:
    row = await session.scalar(select(LlmApiKey).where(LlmApiKey.provider == provider))
    return _record(row) if row else None


async def exists(session: AsyncSession, provider: str) -> bool:
    found = await session.scalar(select(LlmApiKey.id).where(LlmApiKey.provider == provider))
    return found is not None


async def upsert(
    session: AsyncSession, provider: str, *, encrypted_key: str, key_prefix: str
) -> UpsertResult:
    """One atomic statement. ``xmax = 0`` is true only for a freshly inserted row.

    That is a widely used but undocumented Postgres behaviour, so ``created``
    is advisory: it only picks the CLI's "created"/"updated" wording.
    """
    now = utcnow()  # ON CONFLICT bypasses the ORM's onupdate, so set it here
    stmt = (
        insert(LlmApiKey)
        .values(
            id=new_id(),
            provider=provider,
            encrypted_key=encrypted_key,
            key_prefix=key_prefix,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[LlmApiKey.provider],
            set_={"encrypted_key": encrypted_key, "key_prefix": key_prefix, "updated_at": now},
        )
        .returning(LlmApiKey, literal_column("(xmax = 0)", Boolean).label("inserted"))
        # Refresh a row already loaded in the session instead of returning stale values.
        .execution_options(populate_existing=True)
    )
    row, inserted = (await session.execute(stmt)).one()
    return UpsertResult(record=_record(row), created=bool(inserted))


async def delete(session: AsyncSession, provider: str) -> bool:
    result = await session.execute(
        sa_delete(LlmApiKey).where(LlmApiKey.provider == provider).returning(LlmApiKey.id)
    )
    return result.first() is not None
