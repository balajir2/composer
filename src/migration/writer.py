"""Idempotent writer: inserts Composer rows, skips if already present by id.

Wraps JSON-typed fields with `prisma.Json(...)` before `create`.  This keeps
transformer functions pure (plain dicts for easy unit testing), while the
writer handles the Prisma-Python-specific coercion for `Json` / `Json?`
columns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from prisma import Json  # pyright: ignore[reportAttributeAccessIssue]

if TYPE_CHECKING:
    from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]


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


async def upsert_mcp_server(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.mcpserver.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    wrapped = _wrap_optional(data, ("oauthConfig", "tools", "headers"))
    await db.mcpserver.create(data=wrapped)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    return True


async def upsert_mcp_oauth_token(db: Prisma, data: dict[str, Any]) -> bool:  # pyright: ignore[reportUnknownParameterType]
    existing = await db.mcpoauthtoken.find_unique(where={"id": data["id"]})  # pyright: ignore[reportAttributeAccessIssue]
    if existing is not None:
        return False
    await db.mcpoauthtoken.create(data=data)  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
    return True


__all__ = [
    "upsert_execution",
    "upsert_mcp_oauth_token",
    "upsert_mcp_server",
    "upsert_workflow",
]
