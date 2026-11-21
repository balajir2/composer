"""Idempotent writer: inserts Composer rows, skips if already present by id.

Wraps JSON-typed fields with `prisma.Json(...)` before `create`.  This keeps
transformer functions pure (plain dicts for easy unit testing), while the
writer handles the Prisma-Python-specific coercion for `Json` / `Json?`
columns.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]
from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos import mcp_servers as mcp_servers_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
    from src.db.repos.mcp_servers import McpServerCreate


def _wrap_required(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    out = dict(data)
    for key in keys:
        out[key] = Json(out.get(key))
    return out


def _wrap_optional(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """Wrap nullable Json columns with Json() when set; drop the key when None.

    Prisma Python rejects `None` for `Json?` columns — use the absence of
    the key to represent NULL at the DB layer.
    """
    out = dict(data)
    for key in keys:
        if out.get(key) is None:
            out.pop(key, None)
        else:
            out[key] = Json(out[key])
    return out


async def upsert_workflow(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    """Return True if inserted, False if already present."""
    existing = await db.workflow.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    wrapped = _wrap_required(data, ("nodes", "edges"))
    await db.workflow.create(data=wrapped)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    return True


async def upsert_execution(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.workflowexecution.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    wrapped = _wrap_required(data, ("nodeResults", "variables"))
    wrapped = _wrap_optional(wrapped, ("input", "output"))
    await db.workflowexecution.create(data=wrapped)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    return True


_MCP_SERVER_COLUMNS = {
    "id": "id",
    "userId": "user_id",
    "originalOwnerEmail": "original_owner_email",
    "name": "name",
    "url": "url",
    "description": "description",
    "category": "category",
    "authType": "auth_type",
    "encryptedAccessToken": "encrypted_access_token",
    "headerName": "header_name",
    "oauthConfig": "oauth_config",
    "tools": "tools",
    "connectionStatus": "connection_status",
    "lastTested": "last_tested",
    "lastError": "last_error",
    "enabled": "enabled",
    "isOfficial": "is_official",
    "isShared": "is_shared",
    "headers": "headers",
}


def _as_datetime(value: Any) -> datetime | None:
    """A Convex export timestamp (epoch milliseconds, ISO string or datetime) as aware UTC.

    Naive values are taken to be UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value / 1000, tz=UTC)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


async def upsert_mcp_server(session: AsyncSession, data: dict[str, Any]) -> bool:
    """Insert unless a row with this id exists. The caller commits."""
    values: dict[str, Any] = {
        column: data[key] for key, column in _MCP_SERVER_COLUMNS.items() if key in data
    }
    if "last_tested" in values:
        values["last_tested"] = _as_datetime(values["last_tested"])
    return await mcp_servers_repo.insert_if_absent(session, cast("McpServerCreate", values))


async def upsert_mcp_oauth_token(session: AsyncSession, data: dict[str, Any]) -> bool:
    """Insert unless a row with this id exists. The caller commits."""
    values = mcp_oauth_repo.TokenInsert(
        id=data["id"],
        mcp_server_id=data["mcpServerId"],
        user_id=data["userId"],
        encrypted_access_token=data["encryptedAccessToken"],
        encrypted_refresh_token=data.get("encryptedRefreshToken"),
        expires_at=_as_datetime(data.get("expiresAt")),
        scope=data.get("scope"),
        token_type=data.get("tokenType") or "Bearer",
    )
    return await mcp_oauth_repo.insert_token_if_absent(session, values)


__all__ = [
    "upsert_execution",
    "upsert_mcp_oauth_token",
    "upsert_mcp_server",
    "upsert_workflow",
]
