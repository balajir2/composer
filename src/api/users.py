"""GET /users/search — minimal user lookup for any authenticated user.

Unlike /admin/users (admin-only, full user records), this endpoint lets a
non-admin workflow owner find people to share a workflow with. Deliberately
returns only id/email/displayName — no role, no isActive, no other admin-only
fields — to avoid turning this into a general user-enumeration endpoint.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db.repos import users as users_repo
from src.db.session import get_session
from src.security.auth import get_current_user_id
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)

router = APIRouter(tags=["users"])


class UserSearchResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    email: str
    display_name: str | None = Field(default=None, alias="displayName")


@router.get("/users/search", response_model=list[UserSearchResult])
async def search_users(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, ge=1, le=20),
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> list[UserSearchResult]:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="users_search",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_users_search_per_minute),
    )
    rows = await users_repo.search_active(session, q, limit)
    return [UserSearchResult.model_validate(row) for row in rows]


__all__ = ["UserSearchResult", "router"]
