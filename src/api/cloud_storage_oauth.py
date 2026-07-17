"""Google Drive OAuth connect routes. Structurally mirrors
src/api/mcp_servers.py's oauth_router, adapted for a single fixed-provider
OAuth app (client id/secret from settings, not a per-record oauthConfig).

See docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §B, §E.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.integrations.google_drive.oauth import (
    DriveConnectionMissingError,
    DriveTokenExpiredError,
    GoogleDriveOAuthError,
    TokenRefreshError,
    build_authorize_url,
    consume_state,
    exchange_code_for_tokens,
    expires_at_from_in,
    get_valid_drive_access_token,
)
from src.security.auth import get_current_user_id
from src.security.encryption import encrypt
from src.security.rate_limit import (
    RateLimiterProtocol,
    enforce,
    get_rate_limiter,
    per_minute_config,
)
from src.storage.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cloud-storage"])


class AuthorizeResponse(BaseModel):
    authorize_url: str = Field(alias="authorizeUrl")

    model_config = ConfigDict(populate_by_name=True)


class CloudStorageConnectionRead(BaseModel):
    id: str
    provider: str
    account_email: str = Field(alias="accountEmail")

    model_config = ConfigDict(populate_by_name=True)


def _callback_redirect_uri() -> str:
    return f"{get_settings().backend_public_url}/cloud-storage/google-drive/callback"


@router.get("/cloud-storage/google-drive/authorize", response_model=AuthorizeResponse)
async def google_drive_authorize(
    user_id: str = Depends(get_current_user_id),
) -> AuthorizeResponse:
    url = build_authorize_url(user_id, _callback_redirect_uri())
    return AuthorizeResponse.model_validate({"authorizeUrl": url})


def _popup_close_html(oauth_status: str, detail: str = "") -> HTMLResponse:
    message = json.dumps(
        {"type": "composer:google-drive-oauth", "status": oauth_status, "detail": detail[:200]}
    )
    return HTMLResponse(
        "<html><body><script>"
        f"window.opener && window.opener.postMessage({message}, '*');"
        "window.close();"
        "</script></body></html>"
    )


@router.get("/cloud-storage/google-drive/callback")
async def google_drive_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Prisma = Depends(get_db),
) -> HTMLResponse:
    # The whole exchange-and-persist sequence is wrapped in one try block —
    # by the time we reach the upsert, the user has already successfully
    # authorized with Google, so a failure here (DB drop, constraint race,
    # malformed payload) must still resolve to a friendly popup-close page,
    # never an unhandled 500 rendered inside the OAuth popup.
    try:
        user_id = consume_state(state)
        payload = await exchange_code_for_tokens(code, _callback_redirect_uri())
        token_data = {
            "encryptedAccessToken": encrypt(payload["access_token"]),
            "encryptedRefreshToken": (
                encrypt(payload["refresh_token"]) if payload.get("refresh_token") else None
            ),
            "expiresAt": expires_at_from_in(payload.get("expires_in")),
            "scope": payload.get("scope"),
        }
        await db.cloudstorageconnection.upsert(  # pyright: ignore[reportAttributeAccessIssue,reportArgumentType]
            where={
                "userId_provider_accountEmail": {
                    "userId": user_id,
                    "provider": "google-drive",
                    "accountEmail": payload["email"],
                }
            },
            data={
                "create": {
                    "userId": user_id,
                    "provider": "google-drive",
                    "accountEmail": payload["email"],
                    **token_data,
                },
                "update": token_data,  # pyright: ignore[reportArgumentType]
            },
        )
    except GoogleDriveOAuthError as exc:
        return _popup_close_html("error", str(exc))
    except Exception:
        # Unvetted exception type (DB error, malformed Google payload, etc.)
        # — don't leak its message into a page rendered in the user's
        # browser; log server-side instead.
        logger.exception("google_drive_callback: failed to persist connection after OAuth grant")
        return _popup_close_html("error", "Failed to save the connection. Please try again.")
    return _popup_close_html("success")


@router.get("/cloud-storage/connections", response_model=list[CloudStorageConnectionRead])
async def list_cloud_storage_connections(
    provider: str | None = None,
    db: Prisma = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
) -> list[CloudStorageConnectionRead]:
    where: dict[str, Any] = {"userId": user_id}
    if provider:
        where["provider"] = provider
    rows = await db.cloudstorageconnection.find_many(  # pyright: ignore[reportAttributeAccessIssue]
        where=where  # pyright: ignore[reportArgumentType]
    )
    return [
        CloudStorageConnectionRead.model_validate(
            {"id": r.id, "provider": r.provider, "accountEmail": r.accountEmail}
        )
        for r in rows
    ]


class PickerTokenResponse(BaseModel):
    access_token: str = Field(alias="accessToken")

    model_config = ConfigDict(populate_by_name=True)


@router.post(
    "/cloud-storage/connections/{connection_id}/picker-token",
    response_model=PickerTokenResponse,
)
async def get_picker_token(
    connection_id: str,
    db: Prisma = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    limiter: RateLimiterProtocol = Depends(get_rate_limiter),
) -> PickerTokenResponse:
    """Hand back the connection's current OAuth access token for one-time
    client-side use by the Google Picker embed (Task 9's frontend
    component) — not a new or narrower scope, just the same drive.file
    token get_valid_drive_access_token() already produces server-side,
    exposed for the one call the Picker widget itself requires
    (setOAuthToken). 404s (not 403) for a connection owned by another
    user, matching this codebase's private-resource convention (CLAUDE.md
    Phase 8: private = 404 for non-owner).

    Rate-limited like mcp_servers.py's test_mcp_connection: this route can
    trigger a live Google OAuth token-refresh call on every invocation via
    get_valid_drive_access_token(), so an unbounded caller could hammer
    Google's token endpoint."""
    await enforce(
        limiter,
        route_key="picker_token",
        client_key=user_id,
        config=per_minute_config(get_settings().rate_limit_picker_token_per_minute),
    )
    connection = await db.cloudstorageconnection.find_unique(  # pyright: ignore[reportAttributeAccessIssue]
        where={"id": connection_id}
    )
    if connection is None or connection.userId != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found.")
    try:
        token = await get_valid_drive_access_token(connection_id, db)
    except DriveConnectionMissingError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (DriveTokenExpiredError, TokenRefreshError) as exc:
        # Distinguishable status (409) so the Task 9 frontend can show a
        # "reconnect your Google Drive" prompt instead of a generic error.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PickerTokenResponse.model_validate({"accessToken": token})


__all__ = ["router"]
