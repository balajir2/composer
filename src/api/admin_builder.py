"""Admin control over which models the "Build with AI" chat may use."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.builder.model_policy import load_policy, save_policy
from src.db.session import get_session
from src.security.auth import ensure_admin

router = APIRouter(prefix="/admin/builder-models", tags=["admin-builder"])


class BuilderModelSettings(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_model: str = Field(alias="defaultModel")
    # Every model the builder may use, default included.
    allowed_models: list[str] = Field(alias="allowedModels")


async def _current(session: AsyncSession) -> BuilderModelSettings:
    policy = await load_policy(session)
    return BuilderModelSettings(defaultModel=policy.default, allowedModels=policy.allowed_ids)


@router.get("", response_model=BuilderModelSettings, response_model_by_alias=True)
async def get_builder_models(  # pyright: ignore[reportUnusedFunction]
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> BuilderModelSettings:
    return await _current(session)


@router.put("", response_model=BuilderModelSettings, response_model_by_alias=True)
async def put_builder_models(  # pyright: ignore[reportUnusedFunction]
    payload: BuilderModelSettings,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> BuilderModelSettings:
    try:
        await save_policy(session, default=payload.default_model, allowed=payload.allowed_models)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await session.commit()
    return await _current(session)


__all__ = ["router"]
