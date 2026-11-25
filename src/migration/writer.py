"""Idempotent writer: inserts Composer rows, skips if already present by id.

Transformer functions stay pure (plain dicts for easy unit testing); the
writer maps their camelCase keys onto the repo's column names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from src.db.repos import executions as executions_repo
from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import workflows as workflows_repo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.db.repos.executions import ExecutionInsert
    from src.db.repos.mcp_servers import McpServerCreate
    from src.db.repos.workflows import WorkflowValues


_WORKFLOW_COLUMNS = {
    "id": "id",
    "userId": "user_id",
    "originalOwnerEmail": "original_owner_email",
    "name": "name",
    "description": "description",
    "category": "category",
    "tags": "tags",
    "difficulty": "difficulty",
    "estimatedTime": "estimated_time",
    "nodes": "nodes",
    "edges": "edges",
    "version": "version",
    "isTemplate": "is_template",
    "isPublic": "is_public",
}


async def upsert_workflow(session: AsyncSession, data: dict[str, Any]) -> bool:
    """Insert unless a row with this id exists. The caller commits."""
    unknown = sorted(set(data) - set(_WORKFLOW_COLUMNS))
    if unknown:
        raise ValueError(f"unknown workflow keys: {', '.join(unknown)}")
    values: dict[str, Any] = {
        column: data[key] for key, column in _WORKFLOW_COLUMNS.items() if key in data
    }
    return await workflows_repo.insert_if_absent(session, cast("WorkflowValues", values))


_EXECUTION_COLUMNS = {
    "id": "id",
    "workflowId": "workflow_id",
    "userId": "user_id",
    "originalOwnerEmail": "original_owner_email",
    "status": "status",
    "currentNodeId": "current_node_id",
    "nodeResults": "node_results",
    "variables": "variables",
    "input": "input",
    "output": "output",
    "error": "error",
    "threadId": "thread_id",
}


async def upsert_execution(session: AsyncSession, data: dict[str, Any]) -> bool:
    """Insert unless a row with this id exists. The caller commits.

    ``input``/``output`` of ``None`` are left out so the columns stay SQL NULL.
    """
    unknown = sorted(set(data) - set(_EXECUTION_COLUMNS))
    if unknown:
        raise ValueError(f"unknown execution keys: {', '.join(unknown)}")
    values: dict[str, Any] = {
        column: data[key] for key, column in _EXECUTION_COLUMNS.items() if key in data
    }
    for column in ("input", "output"):
        if values.get(column) is None:
            values.pop(column, None)
    return await executions_repo.insert_if_absent(session, cast("ExecutionInsert", values))


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
