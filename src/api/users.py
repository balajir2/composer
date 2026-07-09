"""GET /users/search — minimal user lookup for any authenticated user.

Unlike /admin/users (admin-only, full user records), this endpoint lets a
non-admin workflow owner find people to share a workflow with. Deliberately
returns only id/email/displayName — no role, no isActive, no other admin-only
fields — to avoid turning this into a general user-enumeration endpoint.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.auth import get_current_user_id
from src.security.rate_limit import RateLimiter, enforce, get_rate_limiter, per_minute_config
from src.storage.db import get_db

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
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiter = Depends(get_rate_limiter),
) -> list[UserSearchResult]:  # pyright: ignore[reportUnusedFunction]
    await enforce(
        limiter,
        route_key="users_search",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_users_search_per_minute),
    )
    rows = await db.user.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={
            "isActive": True,
            "OR": [
                {"email": {"contains": q, "mode": "insensitive"}},
                {"displayName": {"contains": q, "mode": "insensitive"}},
            ],
        },
        take=limit,
        order={"email": "asc"},
    )
    return [UserSearchResult.model_validate(row) for row in rows]


__all__ = ["UserSearchResult", "router"]
