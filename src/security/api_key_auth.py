"""Bearer-token path that recognizes 'ck_' API keys (Phase 10a).

Distinct from src/security/auth.py's JWT path — both live under the
Authorization header but differ by token prefix.  Route handlers that
support external invoke use this dep instead of get_current_user_id.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status

from src.security.api_keys import extract_prefix, verify_api_key
from src.storage.db import get_db

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


class ApiKeyAuthResult:
    __slots__ = ("api_key_id", "role", "user_id")

    def __init__(self, *, user_id: str, role: str, api_key_id: str) -> None:
        self.user_id = user_id
        self.role = role
        self.api_key_id = api_key_id


async def get_current_api_key_user(
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> ApiKeyAuthResult:
    """Resolve + validate an 'ck_' API key on Authorization: Bearer.

    Raises 401 on: missing, malformed, unknown prefix, hash mismatch, revoked, expired.
    Touches lastUsedAt on success.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing Authorization header")
    token = header[len("bearer ") :].strip()
    if not token.startswith("ck_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not an API key")

    prefix = extract_prefix(token)
    row = await db.apikey.find_first(  # pyright: ignore[reportAttributeAccessIssue]
        where={"keyPrefix": prefix}
    )
    if row is None or row.revokedAt is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
    if row.expiresAt is not None and row.expiresAt < datetime.now(UTC):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")
    if not verify_api_key(token, row.keyHash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")

    # Touch lastUsedAt; best-effort, don't block on failure.
    with contextlib.suppress(Exception):
        await db.apikey.update(  # pyright: ignore[reportAttributeAccessIssue]
            where={"id": row.id}, data={"lastUsedAt": datetime.now(UTC)}
        )

    # Fetch owning user for role.
    user = await db.user.find_unique(where={"id": row.userId})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None or getattr(user, "isActive", True) is False:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key owner is inactive")
    role = getattr(user, "role", None)
    role_str = (
        str(role.value) if role is not None and hasattr(role, "value") else str(role or "member")
    )
    return ApiKeyAuthResult(user_id=row.userId, role=role_str, api_key_id=row.id)


__all__ = ["ApiKeyAuthResult", "get_current_api_key_user"]
