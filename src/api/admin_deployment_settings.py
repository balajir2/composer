"""Admin-only deployment-setting CRUD (Phase 10d).

Used for per-deployment toggles: 'tool.tavily.enabled', etc.
Values are stored as strings; callers interpret.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repos import deployment_settings as settings_repo
from src.db.session import get_session
from src.security.auth import ensure_admin

router = APIRouter(prefix="/admin/deployment-settings", tags=["admin-deployment-settings"])


class SettingItem(BaseModel):
    key: str
    value: str


class SettingUpdate(BaseModel):
    value: str


@router.get("", response_model=list[SettingItem])
async def list_settings(
    session: AsyncSession = Depends(get_session), _admin: str = Depends(ensure_admin)
) -> list[SettingItem]:  # pyright: ignore[reportUnusedFunction]
    return [SettingItem(key=r.key, value=r.value) for r in await settings_repo.list_all(session)]


@router.get("/{key}", response_model=SettingItem)
async def get_setting(
    key: str, session: AsyncSession = Depends(get_session), _admin: str = Depends(ensure_admin)
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    row = await settings_repo.get(session, key)
    if row is None:
        raise HTTPException(404, f"setting {key!r} not set")
    return SettingItem(key=row.key, value=row.value)


@router.put("/{key}", response_model=SettingItem)
async def upsert_setting(
    key: str,
    payload: SettingUpdate,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    row = await settings_repo.upsert(session, key, payload.value)
    await session.commit()
    return SettingItem(key=row.key, value=row.value)


__all__ = ["router"]
