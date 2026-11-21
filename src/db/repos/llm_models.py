"""The LLM model catalog."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import add_or_already_exists
from src.db.models import LlmModel
from src.db.repos.common import UNSET, Unset
from src.db.types import utcnow


@dataclass(frozen=True)
class LlmModelRecord:
    id: str
    provider: str
    model_id: str
    label: str | None
    enabled: bool
    verification_status: str | None
    verification_message: str | None
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _record(r: LlmModel) -> LlmModelRecord:
    return LlmModelRecord(
        id=r.id,
        provider=r.provider,
        model_id=r.model_id,
        label=r.label,
        enabled=r.enabled,
        verification_status=r.verification_status,
        verification_message=r.verification_message,
        verified_at=r.verified_at,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


# get_by_id may answer from the session's identity map. The ORM UPDATE ... RETURNING
# statements refresh already-loaded rows through synchronize_session's default
# 'evaluate' strategy; populate_existing is belt-and-braces here.
async def list_models(
    session: AsyncSession, *, provider: str | None = None, enabled_only: bool = False
) -> list[LlmModelRecord]:
    stmt = select(LlmModel).order_by(LlmModel.provider, LlmModel.model_id)
    if provider is not None:
        stmt = stmt.where(LlmModel.provider == provider)
    if enabled_only:
        stmt = stmt.where(LlmModel.enabled.is_(True))
    return [_record(r) for r in await session.scalars(stmt)]


async def get_by_id(session: AsyncSession, model_pk: str) -> LlmModelRecord | None:
    row = await session.get(LlmModel, model_pk)
    return _record(row) if row else None


async def get_by_provider_model(
    session: AsyncSession, provider: str, model_id: str
) -> LlmModelRecord | None:
    row = await session.scalar(
        select(LlmModel).where(LlmModel.provider == provider, LlmModel.model_id == model_id)
    )
    return _record(row) if row else None


async def create(
    session: AsyncSession, *, provider: str, model_id: str, label: str | None, enabled: bool
) -> LlmModelRecord:
    row = LlmModel(provider=provider, model_id=model_id, label=label, enabled=enabled)
    await add_or_already_exists(session, row)
    return _record(row)


class _Changes(TypedDict, total=False):
    label: str | None
    enabled: bool
    verification_status: str
    verification_message: str
    verified_at: datetime


async def _update(session: AsyncSession, model_pk: str, values: _Changes) -> LlmModelRecord | None:
    stmt = (
        sa_update(LlmModel)
        .where(LlmModel.id == model_pk)
        .values(**values, updated_at=utcnow())
        .returning(LlmModel)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def update(
    session: AsyncSession,
    model_pk: str,
    *,
    label: str | Unset | None = UNSET,
    enabled: bool | Unset = UNSET,
) -> LlmModelRecord | None:
    values = _Changes()
    if not isinstance(label, Unset):
        values["label"] = label
    if not isinstance(enabled, Unset):
        values["enabled"] = enabled
    return await _update(session, model_pk, values)


async def record_verification(
    session: AsyncSession,
    model_pk: str,
    *,
    status: str,
    message: str,
    verified_at: datetime,
    disable: bool,
) -> LlmModelRecord | None:
    values = _Changes(
        verification_status=status, verification_message=message, verified_at=verified_at
    )
    if disable:
        values["enabled"] = False
    return await _update(session, model_pk, values)


async def delete(session: AsyncSession, model_pk: str) -> bool:
    result = await session.execute(
        sa_delete(LlmModel).where(LlmModel.id == model_pk).returning(LlmModel.id)
    )
    return result.first() is not None
