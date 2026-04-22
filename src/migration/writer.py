"""Idempotent writer: inserts Composer rows, skips if already present by id."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


async def upsert_workflow(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    """Return True if inserted, False if already present."""
    existing = await db.workflow.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    await db.workflow.create(data=data)  # pyright: ignore[reportAttributeAccessIssue]
    return True


async def upsert_execution(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.workflowexecution.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    await db.workflowexecution.create(data=data)  # pyright: ignore[reportAttributeAccessIssue]
    return True


async def upsert_mcp_server(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.mcpserver.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    await db.mcpserver.create(data=data)  # pyright: ignore[reportAttributeAccessIssue]
    return True


async def upsert_mcp_oauth_token(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.mcpoauthtoken.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    await db.mcpoauthtoken.create(data=data)  # pyright: ignore[reportAttributeAccessIssue]
    return True


__all__ = [
    "upsert_execution",
    "upsert_mcp_oauth_token",
    "upsert_mcp_server",
    "upsert_workflow",
]
