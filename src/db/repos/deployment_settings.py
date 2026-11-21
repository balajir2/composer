"""Deployment-wide key/value settings."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeploymentSetting
from src.db.types import utcnow


@dataclass(frozen=True)
class SettingRecord:
    key: str
    value: str
    updated_at: datetime


def _record(row: DeploymentSetting) -> SettingRecord:
    return SettingRecord(key=row.key, value=row.value, updated_at=row.updated_at)


# get/list_all may answer from the session's identity map; upsert uses populate_existing
# so rows already loaded in the session are refreshed with the new values.
async def list_all(session: AsyncSession) -> list[SettingRecord]:
    rows = await session.scalars(select(DeploymentSetting).order_by(DeploymentSetting.key))
    return [_record(r) for r in rows]


async def get(session: AsyncSession, key: str) -> SettingRecord | None:
    row = await session.get(DeploymentSetting, key)
    return _record(row) if row else None


async def upsert(session: AsyncSession, key: str, value: str) -> SettingRecord:
    now = utcnow()  # ON CONFLICT bypasses the ORM's onupdate, so set it here
    stmt = (
        insert(DeploymentSetting)
        .values(key=key, value=value, updated_at=now)
        .on_conflict_do_update(
            index_elements=[DeploymentSetting.key], set_={"value": value, "updated_at": now}
        )
        .returning(DeploymentSetting)
        .execution_options(populate_existing=True)
    )
    return _record((await session.scalars(stmt)).one())
