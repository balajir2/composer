"""POST /mcp-servers — create MCP server.
GET /mcp-servers — list own + shared.
POST /mcp-servers/{id}/test-connection — ping via McpToolProvider.health_check.
DELETE /mcp-servers/{id} — owner only.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.mcp.base import McpToolProvider
from src.mcp.schema_adapter import (
    UnresolvedUrlTemplateError,
    substitute_url_placeholders,
)
from src.security.encryption import encrypt
from src.storage.db import get_db

router = APIRouter(tags=["mcp-servers"])


class McpServerCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    url: str
    description: str | None = None
    category: str | None = None
    auth_type: str = Field(alias="authType")  # "none" | "api-key" | "bearer" | "oauth"
    access_token: str | None = Field(default=None, alias="accessToken")
    header_name: str | None = Field(default=None, alias="headerName")
    is_shared: bool = Field(default=False, alias="isShared")
    headers: dict[str, str] | None = None


class McpServerRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    id: str
    user_id: str = Field(alias="userId")
    name: str
    url: str
    description: str | None = None
    category: str | None = None
    auth_type: str = Field(alias="authType")
    has_access_token: bool = Field(default=False, alias="hasAccessToken")
    header_name: str | None = Field(default=None, alias="headerName")
    tools: Any = None
    connection_status: str = Field(alias="connectionStatus")
    last_tested: Any = Field(default=None, alias="lastTested")
    last_error: str | None = Field(default=None, alias="lastError")
    enabled: bool = True
    is_official: bool = Field(default=False, alias="isOfficial")
    is_shared: bool = Field(default=False, alias="isShared")
    headers: Any = None
    created_at: Any = Field(alias="createdAt")
    updated_at: Any = Field(alias="updatedAt")


class TestConnectionResponse(BaseModel):
    ok: bool
    message: str


def _to_read(row: Any) -> McpServerRead:
    """Redact encrypted fields before returning to client."""
    return McpServerRead.model_validate(
        {
            "id": row.id,
            "userId": row.userId,
            "name": row.name,
            "url": row.url,
            "description": row.description,
            "category": row.category,
            "authType": row.authType,
            "hasAccessToken": bool(row.encryptedAccessToken),
            "headerName": row.headerName,
            "tools": row.tools,
            "connectionStatus": row.connectionStatus,
            "lastTested": row.lastTested,
            "lastError": row.lastError,
            "enabled": row.enabled,
            "isOfficial": row.isOfficial,
            "isShared": row.isShared,
            "headers": row.headers,
            "createdAt": row.createdAt,
            "updatedAt": row.updatedAt,
        }
    )


@router.post("/mcp-servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    payload: McpServerCreate,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> McpServerRead:
    # Validate URL template resolves
    try:
        _ = substitute_url_placeholders(payload.url)
    except UnresolvedUrlTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if payload.auth_type not in {"none", "api-key", "bearer", "oauth"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"authType must be one of none/api-key/bearer/oauth, got {payload.auth_type!r}",
        )
    if payload.auth_type in {"api-key", "bearer"} and not payload.access_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"authType={payload.auth_type!r} requires accessToken",
        )

    encrypted_token = (
        encrypt(payload.access_token)
        if payload.access_token and payload.auth_type in {"api-key", "bearer"}
        else None
    )

    row = await db.mcpserver.create(  # pyright: ignore[reportAttributeAccessIssue]
        data={
            "userId": "dev",  # anonymous per ADR-0005
            "name": payload.name,
            "url": payload.url,
            "description": payload.description,
            "category": payload.category,
            "authType": payload.auth_type,
            "encryptedAccessToken": encrypted_token,
            "headerName": payload.header_name,
            "isShared": payload.is_shared,
            "headers": payload.headers,  # pyright: ignore[reportArgumentType]
        }
    )
    return _to_read(row)


@router.get("/mcp-servers", response_model=list[McpServerRead])
async def list_mcp_servers(
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> list[McpServerRead]:
    rows = await db.mcpserver.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"OR": [{"userId": "dev"}, {"isShared": True}]}
    )
    return [_to_read(r) for r in rows]


@router.post("/mcp-servers/{server_id}/test-connection", response_model=TestConnectionResponse)
async def test_mcp_connection(
    server_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> TestConnectionResponse:
    server = await db.mcpserver.find_unique(where={"id": server_id})  # pyright: ignore[reportAttributeAccessIssue]
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )

    provider = McpToolProvider(server)
    health = await provider.health_check()

    now = datetime.now(UTC)
    update_data: dict[str, Any] = {
        "connectionStatus": "connected" if health.ok else "error",
        "lastTested": now,
        "lastError": None if health.ok else health.message,
    }
    if health.ok:
        try:
            tools = await provider.tools()
            update_data["tools"] = [  # pyright: ignore[reportArgumentType]
                {"name": t.name, "description": t.description} for t in tools
            ]
        except Exception as exc:
            # Health said ok but tools/list failed; surface + don't overwrite lastError
            update_data["connectionStatus"] = "error"
            update_data["lastError"] = f"tools/list failed after initialize ok: {exc}"

    await db.mcpserver.update(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": server_id}, data=update_data
    )

    return TestConnectionResponse(ok=health.ok, message=health.message)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
) -> None:
    server = await db.mcpserver.find_unique(where={"id": server_id})  # pyright: ignore[reportAttributeAccessIssue]
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    # Owner-only delete, even for shared servers
    if server.userId != "dev":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the owner ({server.userId!r}) can delete this server.",
        )
    await db.mcpserver.delete(where={"id": server_id})  # pyright: ignore[reportAttributeAccessIssue]


__all__ = ["McpServerCreate", "McpServerRead", "TestConnectionResponse", "router"]
