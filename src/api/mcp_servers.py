"""POST /mcp-servers — create MCP server.
GET /mcp-servers — list own + shared.
POST /mcp-servers/{id}/test-connection — ping via McpToolProvider.health_check.
DELETE /mcp-servers/{id} — owner only.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.db.repos.mcp_servers import McpServerChanges, McpServerRecord
from src.db.runtime import request_sessionmaker
from src.db.session import get_session
from src.mcp.base import McpToolProvider
from src.mcp.oauth import (
    InvalidStateError,
    OAuthError,
    build_authorize_url,
    exchange_code_for_tokens,
)
from src.mcp.resolver import user_can_use_server
from src.mcp.schema_adapter import (
    UnresolvedUrlTemplateError,
    substitute_url_placeholders,
)
from src.security.auth import ensure_admin, get_current_user_id
from src.security.encryption import (
    encrypt,
    encrypt_marked,
    encrypt_sensitive_headers,
    redact_sensitive_headers,
)
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-servers"])


def _require_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """The app's sessionmaker for routes that open their own short sessions around network I/O."""
    sessionmaker = request_sessionmaker(request)
    if sessionmaker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
        )
    return sessionmaker


class OauthConfig(BaseModel):
    """OAuth 2.1 config for authType='oauth' MCP servers."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    authorize_url: str = Field(alias="authorizeUrl")
    token_url: str = Field(alias="tokenUrl")
    client_id: str = Field(alias="clientId")
    client_secret: str | None = Field(default=None, alias="clientSecret")
    scopes: list[str] = Field(default_factory=list)


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
    oauth_config: OauthConfig | None = Field(default=None, alias="oauthConfig")


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
    has_oauth_config: bool = Field(default=False, alias="hasOauthConfig")
    has_oauth_token: bool = Field(default=False, alias="hasOauthToken")
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


class OAuthConfigRequest(BaseModel):
    """Wrapper so the admin can PATCH oauthConfig after creation."""

    model_config = ConfigDict(populate_by_name=True)

    oauth_config: OauthConfig = Field(alias="oauthConfig")


class OAuthAuthorizeRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    redirect_uri: str = Field(alias="redirectUri")


class OAuthAuthorizeResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    authorize_url: str = Field(alias="authorizeUrl")


class OAuthCallbackResponse(BaseModel):
    ok: bool
    server_id: str = Field(alias="serverId")
    model_config = ConfigDict(populate_by_name=True)


def _encrypted_oauth_config_dict(config: OauthConfig) -> dict[str, Any]:
    """Dump an OauthConfig for storage with clientSecret encrypted at rest
    (P0-5) — it was previously persisted as plaintext even though
    McpOAuthToken access/refresh tokens already were."""
    dumped = config.model_dump(by_alias=True)
    if dumped.get("clientSecret"):
        dumped["clientSecret"] = encrypt_marked(dumped["clientSecret"])
    return dumped


def _to_read(row: McpServerRecord, *, has_oauth_token: bool = False) -> McpServerRead:
    """Redact encrypted fields before returning to client."""
    return McpServerRead.model_validate(
        {
            "id": row.id,
            "userId": row.user_id,
            "name": row.name,
            "url": row.url,
            "description": row.description,
            "category": row.category,
            "authType": row.auth_type,
            "hasAccessToken": bool(row.encrypted_access_token),
            "hasOauthConfig": bool(row.oauth_config),
            "hasOauthToken": has_oauth_token,
            "headerName": row.header_name,
            "tools": row.tools,
            "connectionStatus": row.connection_status,
            "lastTested": row.last_tested,
            "lastError": row.last_error,
            "enabled": row.enabled,
            "isOfficial": row.is_official,
            "isShared": row.is_shared,
            # P0-5: never let a secret-looking header value (Authorization,
            # X-Api-Key, ...) leave the server; other headers pass through.
            "headers": redact_sensitive_headers(row.headers) if row.headers else row.headers,
            "createdAt": row.created_at,
            "updatedAt": row.updated_at,
        }
    )


@router.post("/mcp-servers", response_model=McpServerRead, status_code=status.HTTP_201_CREATED)
async def create_mcp_server(
    payload: McpServerCreate,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> McpServerRead:
    # Validate URL template resolves
    try:
        _ = substitute_url_placeholders(payload.url)
    except UnresolvedUrlTemplateError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    if payload.auth_type not in {"none", "api-key", "bearer", "oauth"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"authType must be one of none/api-key/bearer/oauth, got {payload.auth_type!r}",
        )
    if payload.auth_type in {"api-key", "bearer"} and not payload.access_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"authType={payload.auth_type!r} requires accessToken",
        )

    encrypted_token = (
        encrypt(payload.access_token)
        if payload.access_token and payload.auth_type in {"api-key", "bearer"}
        else None
    )

    values = mcp_servers_repo.McpServerCreate(
        user_id=user_id,
        name=payload.name,
        url=payload.url,
        description=payload.description,
        category=payload.category,
        auth_type=payload.auth_type,
        encrypted_access_token=encrypted_token,
        header_name=payload.header_name,
        is_shared=payload.is_shared,
    )
    if payload.headers is not None:
        # P0-5: encrypt secret-looking header values at rest — no prior
        # server row exists yet at create time, so no preserve-on-marker
        # concern (there's currently no PATCH endpoint for headers either).
        values["headers"] = encrypt_sensitive_headers(payload.headers)
    if payload.oauth_config is not None:
        values["oauth_config"] = _encrypted_oauth_config_dict(payload.oauth_config)

    row = await mcp_servers_repo.create(session, values)
    await session.commit()
    return _to_read(row)


@router.get("/mcp-servers", response_model=list[McpServerRead])
async def list_mcp_servers(
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> list[McpServerRead]:
    rows = await mcp_servers_repo.list_visible(session, user_id)
    return [_to_read(r) for r in rows]


@router.post("/mcp-servers/{server_id}/test-connection", response_model=TestConnectionResponse)
async def test_mcp_connection(
    server_id: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]  # TODO(phase 2 task 3): OAuth token reads
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> TestConnectionResponse:
    await enforce(
        limiter,
        route_key="mcp_test",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_mcp_test_per_minute),
    )
    sessionmaker = _require_sessionmaker(request)
    # No session is open across the MCP network calls below: read in one short
    # session, close it, call out, then write the result in a second one.
    async with sessionmaker() as session:
        server = await mcp_servers_repo.get(session, server_id)
    # Same visibility rule as the resolver: owner or shared.  A private server
    # of another user answers exactly like an unknown id (no existence leak).
    # This check stays before any network call.
    if server is None or not user_can_use_server(user_id, server):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )

    # OAuth servers require db+user_id so the provider can fetch the caller's
    # access token from McpOAuthToken on each outbound request.  Static-auth
    # servers accept the extras harmlessly.
    provider = McpToolProvider(server, db=db, user_id=user_id)
    health = await provider.health_check()

    changes = McpServerChanges(
        connection_status="connected" if health.ok else "error",
        last_tested=datetime.now(UTC),
        last_error=None if health.ok else health.message,
    )
    if health.ok:
        try:
            tools = await provider.tools()
            changes["tools"] = [{"name": t.name, "description": t.description} for t in tools]
        except Exception as exc:
            # Health said ok but tools/list failed; surface + don't overwrite lastError
            changes["connection_status"] = "error"
            changes["last_error"] = f"tools/list failed after initialize ok: {exc}"

    message = health.message
    try:
        async with sessionmaker() as session:
            # A None result means the server was deleted during the network call: nothing to record.
            await mcp_servers_repo.update(session, server_id, changes)
            await session.commit()
    except Exception as exc:
        # The connection result is still valid; only recording it failed. Log the class
        # name only (exception text can carry connection details).
        logger.warning("mcp test-connection: could not save result (%s)", type(exc).__name__)
        message = f"{message} (result not saved)"

    return TestConnectionResponse(ok=health.ok, message=message)


class McpOwnerAssignRequest(BaseModel):
    user_id: str | None = Field(default=None, alias="userId")
    email: str | None = None

    model_config = ConfigDict(populate_by_name=True)


@router.patch("/mcp-servers/{server_id}/owner", response_model=McpServerRead)
async def assign_mcp_server_owner(
    server_id: str,
    payload: McpOwnerAssignRequest,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> McpServerRead:  # pyright: ignore[reportUnusedFunction]
    target_user_id = payload.user_id
    if target_user_id is None:
        if not payload.email:
            raise HTTPException(422, "must provide user_id or email")
        target = await users_repo.get_by_email(session, payload.email.lower())
        if target is None:
            raise HTTPException(404, f"user with email {payload.email!r} not found")
        target_user_id = target.id
    else:
        target = await users_repo.get_by_id(session, target_user_id)
        if target is None:
            raise HTTPException(404, f"user {target_user_id!r} not found")
    existing = await mcp_servers_repo.get(session, server_id)
    if existing is None:
        raise HTTPException(404, f"MCP server {server_id!r} not found.")
    updated = await mcp_servers_repo.update(session, server_id, {"user_id": target_user_id})
    await session.commit()
    if updated is None:  # deleted between the read and the update
        raise HTTPException(404, f"MCP server {server_id!r} not found.")
    return _to_read(updated)


@router.delete("/mcp-servers/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: str,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> None:
    server = await mcp_servers_repo.get(session, server_id)
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    # Owner-only delete, even for shared servers
    if server.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only the owner ({server.user_id!r}) can delete this server.",
        )
    await mcp_servers_repo.delete(session, server_id)
    await session.commit()


@router.patch(
    "/mcp-servers/{server_id}/oauth-config",
    response_model=McpServerRead,
)
async def update_oauth_config(
    server_id: str,
    payload: OAuthConfigRequest,
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> McpServerRead:  # pyright: ignore[reportUnusedFunction]
    existing = await mcp_servers_repo.get(session, server_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    # Only the owner can edit OAuth config (client_id / client_secret are secrets).
    if existing.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner may edit OAuth config.",
        )
    if existing.auth_type != "oauth":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"MCP server authType must be 'oauth' (got {existing.auth_type!r}).",
        )
    updated = await mcp_servers_repo.update(
        session, server_id, {"oauth_config": _encrypted_oauth_config_dict(payload.oauth_config)}
    )
    await session.commit()
    if updated is None:  # deleted between the read and the update
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    return _to_read(updated)


@router.post(
    "/mcp-servers/{server_id}/oauth/authorize",
    response_model=OAuthAuthorizeResponse,
)
async def oauth_authorize(
    server_id: str,
    payload: OAuthAuthorizeRequest,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]  # TODO(phase 2 task 3): state rows
    session: AsyncSession = Depends(get_session),
    user_id: str = Depends(get_current_user_id),
) -> OAuthAuthorizeResponse:
    server = await mcp_servers_repo.get(session, server_id)
    await session.rollback()  # release the pooled connection; the rest uses Prisma
    # Same visibility rule as the resolver: owner or shared.  A private server
    # of another user answers exactly like an unknown id (no existence leak).
    if server is None or not user_can_use_server(user_id, server):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server {server_id!r} not found.",
        )
    if server.auth_type != "oauth":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"MCP server authType must be 'oauth' (got {server.auth_type!r}).",
        )
    url = await build_authorize_url(
        server, user_id=user_id, redirect_uri=payload.redirect_uri, db=db
    )
    return OAuthAuthorizeResponse.model_validate({"authorizeUrl": url})


@router.post(
    "/mcp-servers/{server_id}/oauth/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def oauth_disconnect(
    server_id: str,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> None:
    token = await db.mcpoauthtoken.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={
            "mcpServerId_userId": {
                "mcpServerId": server_id,
                "userId": user_id,
            }
        }
    )
    if token is None:
        # Idempotent: no token means nothing to disconnect.
        return
    await db.mcpoauthtoken.delete(where={"id": token.id})  # pyright: ignore[reportAttributeAccessIssue]


oauth_router = APIRouter(tags=["oauth"])


_FRONTEND_ORIGIN_DEFAULT = "http://localhost:3000"


def _admin_mcp_url(server_id: str, status_param: str, detail: str = "") -> str:
    """Build the redirect URL for the admin MCP page after OAuth callback."""
    settings = get_settings()
    origin = (
        settings.iep_ui_origin
        if settings.deployment_mode == "embedded" and settings.iep_ui_origin
        else _FRONTEND_ORIGIN_DEFAULT
    )
    from urllib.parse import urlencode

    params: dict[str, str] = {"serverId": server_id, "oauth": status_param}
    if detail:
        params["detail"] = detail[:200]
    return f"{origin}/admin/mcp-servers?{urlencode(params)}"


@oauth_router.get("/oauth/callback")
async def oauth_callback(
    code: str,
    state: str,
    request: Request,
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]  # TODO(phase 2 task 3): state read and token exchange use Prisma
) -> RedirectResponse:
    """Exchange the authorization code for tokens, then redirect the browser
    back to the admin MCP servers page with a status query param so the UI
    can surface a toast instead of leaving the user on a raw-JSON page."""
    state_row = await db.mcpoauthstate.find_unique(where={"state": state})  # pyright: ignore[reportAttributeAccessIssue]
    if state_row is None:
        return RedirectResponse(
            url=_admin_mcp_url("", "error", "OAuth state not found (CSRF or replay)."),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    # Short session: closed before the token-endpoint call in exchange_code_for_tokens.
    async with _require_sessionmaker(request)() as session:
        server = await mcp_servers_repo.get(session, state_row.mcpServerId)
    if server is None:
        return RedirectResponse(
            url=_admin_mcp_url(
                state_row.mcpServerId, "error", "Server not found for this OAuth state."
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    try:
        await exchange_code_for_tokens(server, code=code, state=state, db=db)
    except (InvalidStateError, OAuthError) as exc:
        return RedirectResponse(
            url=_admin_mcp_url(server.id, "error", str(exc)),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(
        url=_admin_mcp_url(server.id, "success"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


class McpSharedRequest(BaseModel):
    is_shared: bool = Field(..., alias="isShared")

    model_config = ConfigDict(populate_by_name=True)


@router.patch("/mcp-servers/{server_id}/shared", response_model=McpServerRead)
async def set_mcp_shared(
    server_id: str,
    payload: McpSharedRequest,
    session: AsyncSession = Depends(get_session),
    _admin: str = Depends(ensure_admin),
) -> McpServerRead:  # pyright: ignore[reportUnusedFunction]
    existing = await mcp_servers_repo.get(session, server_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found.")
    updated = await mcp_servers_repo.update(session, server_id, {"is_shared": payload.is_shared})
    await session.commit()
    if updated is None:  # deleted between the read and the update
        raise HTTPException(status_code=404, detail=f"MCP server {server_id!r} not found.")
    return _to_read(updated)


__all__ = [
    "McpOwnerAssignRequest",
    "McpServerCreate",
    "McpServerRead",
    "McpSharedRequest",
    "OAuthAuthorizeRequest",
    "OAuthAuthorizeResponse",
    "OAuthCallbackResponse",
    "TestConnectionResponse",
    "oauth_router",
    "router",
]
