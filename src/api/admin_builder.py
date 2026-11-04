"""Admin control over which models the "Build with AI" chat may use."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.builder.model_policy import load_policy, save_policy
from src.security.auth import ensure_admin
from src.storage.db import get_db

router = APIRouter(prefix="/admin/builder-models", tags=["admin-builder"])


class BuilderModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_model: str = Field(alias="defaultModel")
    # Every model the builder may use, default included.
    allowed_models: list[str] = Field(alias="allowedModels")


async def _current(db: Prisma) -> BuilderModelSettings:
    policy = await load_policy(db)
    return BuilderModelSettings(defaultModel=policy.default, allowedModels=policy.allowed_ids)


@router.get("", response_model=BuilderModelSettings, response_model_by_alias=True)
async def get_builder_models(  # pyright: ignore[reportUnusedFunction]
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> BuilderModelSettings:
    return await _current(db)


@router.put("", response_model=BuilderModelSettings, response_model_by_alias=True)
async def put_builder_models(  # pyright: ignore[reportUnusedFunction]
    payload: BuilderModelSettings,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    _admin: str = Depends(ensure_admin),
) -> BuilderModelSettings:
    try:
        await save_policy(db, default=payload.default_model, allowed=payload.allowed_models)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return await _current(db)


__all__ = ["router"]
