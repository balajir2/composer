"""Bearer-token path that recognizes 'ck_' API keys (Phase 10a).

Distinct from src/security/auth.py's JWT path — both live under the
Authorization header but differ by token prefix.  Route handlers that
support external invoke use this dep instead of get_current_user_id.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request, status

from src.db.repos import api_keys as keys_repo
from src.db.repos import users as users_repo
from src.db.runtime import request_sessionmaker
from src.db.types import utcnow
from src.security.api_keys import extract_prefix, verify_api_key

logger = logging.getLogger(__name__)


class ApiKeyAuthResult:
    __slots__ = ("api_key_id", "role", "user_id")

    def __init__(self, *, user_id: str, role: str, api_key_id: str) -> None:
        self.user_id = user_id
        self.role = role
        self.api_key_id = api_key_id


async def get_current_api_key_user(request: Request) -> ApiKeyAuthResult:
    """Resolve + validate an 'ck_' API key on Authorization: Bearer.

    Raises 401 on: missing, malformed, unknown prefix, hash mismatch, revoked, expired.
    Touches last_used_at on success.

    Does NOT use the request-scoped session: POST /api/run/{slug} can wait for a
    whole workflow run, and a request-scoped session would pin a pooled
    connection for that long. All lookups happen inside one short session of
    our own that is closed before the route body runs.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing Authorization header")
    token = header[len("bearer ") :].strip()
    if not token.startswith("ck_"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not an API key")

    prefix = extract_prefix(token)
    sessionmaker = request_sessionmaker(request)
    if sessionmaker is None:
        # The pre-port code had no database-less path (it required the DB), so
        # there is nothing to preserve in development either: fail closed.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication unavailable")

    async with sessionmaker() as session:
        row = await keys_repo.first_by_prefix(session, prefix)
        if row is None or row.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")
        if row.expires_at is not None and row.expires_at < utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key expired")
        if not verify_api_key(token, row.key_hash):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid API key")

        # Touch last_used_at; best-effort, don't block on failure.
        try:
            await keys_repo.touch_last_used(session, row.id, utcnow())
            await session.commit()
        except Exception as exc:
            # Class name only: DB error text can contain connection URLs.
            logger.warning("api key last_used_at update failed (%s)", type(exc).__name__)
            await session.rollback()

        user = await users_repo.get_by_id(session, row.user_id)
    # The owner was loaded inside the block; the session is closed before the
    # route body (and any workflow run) starts.
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key owner is inactive")
    return ApiKeyAuthResult(user_id=row.user_id, role=user.role.value, api_key_id=row.id)


__all__ = ["ApiKeyAuthResult", "get_current_api_key_user"]
