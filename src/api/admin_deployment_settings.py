"""Admin-only deployment-setting CRUD (Phase 10d).

Used for per-deployment toggles: 'tool.tavily.enabled', etc.
Values are stored as strings; callers interpret.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.security.auth import ensure_admin
from src.storage.db import get_db

router = APIRouter(prefix="/admin/deployment-settings", tags=["admin-deployment-settings"])


class SettingItem(BaseModel):
    key: str
    value: str


class SettingUpdate(BaseModel):
    value: str


@router.get("", response_model=list[SettingItem])
async def list_settings(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> list[SettingItem]:  # pyright: ignore[reportUnusedFunction]
    rows = await db.deploymentsetting.find_many(order={"key": "asc"})  # pyright: ignore[reportAttributeAccessIssue]
    return [SettingItem(key=r.key, value=r.value) for r in rows]


@router.get("/{key}", response_model=SettingItem)
async def get_setting(
    key: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    row = await db.deploymentsetting.find_unique(where={"key": key})  # pyright: ignore[reportAttributeAccessIssue]
    if row is None:
        raise HTTPException(404, f"setting {key!r} not set")
    return SettingItem(key=row.key, value=row.value)


@router.put("/{key}", response_model=SettingItem)
async def upsert_setting(
    key: str,
    payload: SettingUpdate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> SettingItem:  # pyright: ignore[reportUnusedFunction]
    existing = await db.deploymentsetting.find_unique(where={"key": key})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is None:
        row = await db.deploymentsetting.create(  # pyright: ignore[reportAttributeAccessIssue]
            data={"key": key, "value": payload.value}
        )
    else:
        row = await db.deploymentsetting.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"key": key}, data={"value": payload.value}
        )
    # update() returns Optional but `existing` was just confirmed non-None,
    # so the row exists.  Pyright can't track that across the await.
    return SettingItem(key=row.key, value=row.value)  # pyright: ignore[reportOptionalMemberAccess]


__all__ = ["router"]
