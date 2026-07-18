"""Google Drive OAuth primitives — standard three-legged OAuth2 (no RFC
8707 `resource` param; that was an MCP/Highspot-specific requirement, not
applicable to Google's OAuth server). Tokens live server-side only,
encrypted with src/security/encryption.py — same primitives src/mcp/oauth.py
uses for MCP OAuth tokens.

State is a self-contained encrypted payload (user_id + expiry), not a DB
row like McpOAuthState — AES-GCM already gives tamper-evidence and expiry
without a separate table/cleanup sweep. Tradeoff: unlike McpOAuthState
(deleted on first use), this state is TTL-bounded but not single-use — a
captured state value could be replayed within the 10-minute window. This
is accepted: the state is tamper-evident and identity-embedded, so an
attacker still can't forge a state for someone else's user_id; only a
literal MITM/log-leak of a specific state value would let it be replayed,
and only within the TTL.

See docs/archive/phase-history/specs/2026-07-15-google-drive-oauth-file-trigger-design.md §B.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from src.config import get_settings
from src.security.encryption import EncryptionError, EncryptionKeyMissingError, decrypt, encrypt

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# drive.file alone grants no access to the userinfo endpoint this module
# calls to look up the connected account's email (CloudStorageConnection.
# accountEmail) -- without it Google's userinfo endpoint returns 401
# ("missing required authentication credential") even though the token
# exchange itself succeeded, since the token simply isn't scoped for it.
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
OAUTH_SCOPES = f"{DRIVE_FILE_SCOPE} {USERINFO_EMAIL_SCOPE}"

_STATE_TTL = timedelta(minutes=10)
_EXPIRY_BUFFER_SECONDS = 60


class GoogleDriveOAuthError(RuntimeError):
    """Base class for all Google Drive OAuth errors."""


class InvalidStateError(GoogleDriveOAuthError):
    """State param missing, expired, or tampered with."""


class TokenExchangeError(GoogleDriveOAuthError):
    """Google rejected the authorization code exchange."""


class TokenRefreshError(GoogleDriveOAuthError):
    """Google rejected the token refresh request."""


class DriveTokenExpiredError(GoogleDriveOAuthError):
    """Token expired with no refresh token available — user must reconnect."""


class DriveConnectionMissingError(GoogleDriveOAuthError):
    """No CloudStorageConnection row found for the given id."""


def build_state(user_id: str) -> str:
    payload = {"user_id": user_id, "exp": (datetime.now(UTC) + _STATE_TTL).isoformat()}
    return encrypt(json.dumps(payload))


def consume_state(state: str) -> str:
    """Decrypt state, verify not expired, return user_id.

    Note: this state is TTL-bounded but not single-use (see module
    docstring) — a captured state value could be replayed within the TTL.
    """
    try:
        payload = json.loads(decrypt(state))
    except EncryptionKeyMissingError:
        # Server misconfiguration, not a tampered/invalid state — let it
        # propagate unchanged so it isn't mistaken for a client-side attack.
        raise
    except EncryptionError as exc:
        raise InvalidStateError(f"OAuth state is invalid or tampered: {exc}") from exc
    expires_at = datetime.fromisoformat(payload["exp"])
    if datetime.now(UTC) >= expires_at:
        raise InvalidStateError("OAuth state has expired.")
    return payload["user_id"]  # type: ignore[no-any-return]


def build_authorize_url(user_id: str, redirect_uri: str) -> str:
    settings = get_settings()
    params = {
        "client_id": settings.google_oauth_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": build_state(user_id),
    }
    return f"{GOOGLE_AUTHORIZE_URL}?{urlencode(params)}"


def expires_at_from_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    settings = get_settings()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL, data=form, headers={"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise TokenExchangeError(f"Google token exchange request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenExchangeError(
            f"Google token exchange failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    payload: dict[str, Any] = resp.json()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            userinfo_resp = await client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {payload['access_token']}"},
            )
    except httpx.HTTPError as exc:
        raise TokenExchangeError(f"Google userinfo request failed: {exc}") from exc
    if userinfo_resp.status_code >= 400:
        raise TokenExchangeError(
            f"Google userinfo fetch failed (HTTP {userinfo_resp.status_code}): "
            f"{userinfo_resp.text[:200]}"
        )
    payload["email"] = userinfo_resp.json().get("email", "")
    return payload


async def refresh_access_token(refresh_token_plaintext: str) -> dict[str, Any]:
    settings = get_settings()
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token_plaintext,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL, data=form, headers={"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise TokenRefreshError(f"Google token refresh request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TokenRefreshError(
            f"Google token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    return resp.json()  # type: ignore[no-any-return]


async def get_valid_drive_access_token(
    connection_id: str, db: Any, *, expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS
) -> str:
    """Return a decrypted plaintext access token, refreshing if near-expiry."""
    connection = await db.cloudstorageconnection.find_unique(where={"id": connection_id})
    if connection is None:
        raise DriveConnectionMissingError(
            f"No CloudStorageConnection found for id {connection_id!r}."
        )

    if connection.expiresAt is not None:
        now = datetime.now(UTC)
        if now >= connection.expiresAt - timedelta(seconds=expiry_buffer_seconds):
            if not connection.encryptedRefreshToken:
                raise DriveTokenExpiredError(
                    f"Drive connection {connection_id!r} expired with no refresh token; "
                    f"user must reconnect."
                )
            refreshed = await refresh_access_token(decrypt(connection.encryptedRefreshToken))
            update_data: dict[str, Any] = {
                "encryptedAccessToken": encrypt(refreshed["access_token"]),
                "expiresAt": expires_at_from_in(refreshed.get("expires_in")),
            }
            new_refresh_token = refreshed.get("refresh_token")
            if new_refresh_token:
                # Google doesn't always rotate the refresh token; only
                # persist a new one when the response actually includes
                # one (a partial Prisma update leaves the existing
                # encryptedRefreshToken alone otherwise).
                update_data["encryptedRefreshToken"] = encrypt(new_refresh_token)
            connection = await db.cloudstorageconnection.update(
                where={"id": connection_id},
                data=update_data,
            )

    return decrypt(connection.encryptedAccessToken)  # type: ignore[no-any-return]


__all__ = [
    "DRIVE_FILE_SCOPE",
    "DriveConnectionMissingError",
    "DriveTokenExpiredError",
    "GoogleDriveOAuthError",
    "InvalidStateError",
    "TokenExchangeError",
    "TokenRefreshError",
    "build_authorize_url",
    "build_state",
    "consume_state",
    "exchange_code_for_tokens",
    "expires_at_from_in",
    "get_valid_drive_access_token",
    "refresh_access_token",
]
