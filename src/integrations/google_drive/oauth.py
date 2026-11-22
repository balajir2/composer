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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import get_settings
from src.db import locks
from src.db.repos import cloud_storage as cloud_storage_repo
from src.db.repos.cloud_storage import CloudStorageConnectionRecord
from src.security.encryption import EncryptionError, EncryptionKeyMissingError, decrypt, encrypt

GOOGLE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
# drive.file (originally used here) only grants durable access to files the
# app created or the user explicitly opened through a drive.file-scoped
# Picker session -- confirmed in production (2026-07-18) that a folder
# picked via Picker becomes unreachable (404) to the token minutes later
# for server-side polling, even though the Picker UI itself could browse
# it live. Replaced with two narrower non-drive.file scopes instead of the
# unrestricted `drive` scope:
#   - drive.readonly: list_new_files' metadata query + read_file's
#     alt=media content download. Durable, not tied to a Picker session.
#   - drive.metadata: move_file's PATCH to set the composerStatus
#     appProperty (the claim marker) -- drive.readonly alone is read-only
#     and can't write it; drive.metadata covers metadata read/write across
#     Drive without granting file *content* write access, which this
#     provider never needs (write_file raises NotImplementedError).
# Both are Google "restricted" scopes requiring a CASA security assessment
# to verify for public production use beyond 100 test users -- same tier
# drive.file was meant to avoid needing that for. Since this app stays in
# OAuth consent-screen Testing status (small number of known test users,
# not seeking public verification), that requirement doesn't apply either
# way, so there's no added cost to using the correctly-scoped combination
# here over the single drive.file scope that didn't actually work.
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata"
# drive.file alone grants no access to the userinfo endpoint this module
# calls to look up the connected account's email (CloudStorageConnection.
# accountEmail) -- without it Google's userinfo endpoint returns 401
# ("missing required authentication credential") even though the token
# exchange itself succeeded, since the token simply isn't scoped for it.
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
OAUTH_SCOPES = f"{DRIVE_READONLY_SCOPE} {DRIVE_METADATA_SCOPE} {USERINFO_EMAIL_SCOPE}"

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


async def _post_token_form(
    form: dict[str, str], error_cls: type[GoogleDriveOAuthError], label: str
) -> dict[str, Any]:
    """POST a grant to Google's token endpoint; failures raise ``error_cls`` naming ``label``."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = await client.post(
                GOOGLE_TOKEN_URL, data=form, headers={"Accept": "application/json"}
            )
    except httpx.HTTPError as exc:
        raise error_cls(f"Google {label} request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise error_cls(f"Google {label} failed (HTTP {resp.status_code}): {resp.text[:200]}")
    return resp.json()  # type: ignore[no-any-return]


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict[str, Any]:
    settings = get_settings()
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": settings.google_oauth_client_id,
        "client_secret": settings.google_oauth_client_secret,
    }
    payload = await _post_token_form(form, TokenExchangeError, "token exchange")

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
    return await _post_token_form(form, TokenRefreshError, "token refresh")


def _needs_refresh(connection: CloudStorageConnectionRecord, buffer_seconds: int) -> bool:
    if connection.expires_at is None:
        return False
    return datetime.now(UTC) >= connection.expires_at - timedelta(seconds=buffer_seconds)


async def _refresh_connection(
    connection_id: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    expiry_buffer_seconds: int,
) -> CloudStorageConnectionRecord:
    """Refresh one connection's token once, even when several callers race.

    Serialized per connection with a transaction-scoped Postgres advisory lock: a caller that
    waited re-reads the row, finds it already refreshed and returns it without posting (a
    refresh token posted twice can get revoked by an IdP that detects reuse).

    SHORT-SESSION RULE EXCEPTION: this holds ONE session (one pooled connection) open across
    the Google refresh POST, because the lock lives exactly as long as the transaction. That
    POST is the only network call allowed inside an open session.
    """
    missing = f"No CloudStorageConnection found for id {connection_id!r}."
    async with sessionmaker() as session:
        try:
            await locks.acquire_xact_lock(session, f"cloud_storage_connection:{connection_id}")
        except locks.LockTimeoutError as exc:
            raise TokenRefreshError(
                f"Timed out waiting for another refresh of Drive connection {connection_id!r}; "
                f"try again."
            ) from exc
        current = await cloud_storage_repo.get(session, connection_id)
        if current is None:
            raise DriveConnectionMissingError(missing)
        if not _needs_refresh(current, expiry_buffer_seconds):
            await session.commit()  # another caller refreshed it while we waited
            return current
        if not current.encrypted_refresh_token:
            raise DriveTokenExpiredError(
                f"Drive connection {connection_id!r} expired with no refresh token; "
                f"user must reconnect."
            )
        # Raises on failure: leaving the block rolls back, releasing the lock, row unchanged.
        refreshed = await refresh_access_token(decrypt(current.encrypted_refresh_token))
        changes = cloud_storage_repo.TokenChanges(
            encrypted_access_token=encrypt(refreshed["access_token"]),
            expires_at=expires_at_from_in(refreshed.get("expires_in")),
        )
        new_refresh_token = refreshed.get("refresh_token")
        if new_refresh_token:
            # Google doesn't always rotate the refresh token; only persist a new one
            # when the response actually includes one.
            changes["encrypted_refresh_token"] = encrypt(new_refresh_token)
        updated = await cloud_storage_repo.update_tokens(session, connection_id, changes)
        if updated is None:
            # Deleted after the first read: don't hand back the stale, expired token.
            raise DriveConnectionMissingError(missing)
        await session.commit()  # releases the advisory lock
        return updated


async def get_valid_drive_access_token(
    connection_id: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS,
) -> str:
    """Return a decrypted plaintext access token, refreshing if near-expiry.

    The fast path is one short read session, closed before anything else. A refresh runs in its
    own session under an advisory lock (see ``_refresh_connection``, a documented exception to
    the short-session rule).
    """
    async with sessionmaker() as session:
        connection = await cloud_storage_repo.get(session, connection_id)
    if connection is None:
        raise DriveConnectionMissingError(
            f"No CloudStorageConnection found for id {connection_id!r}."
        )

    if _needs_refresh(connection, expiry_buffer_seconds):
        if not connection.encrypted_refresh_token:
            raise DriveTokenExpiredError(
                f"Drive connection {connection_id!r} expired with no refresh token; "
                f"user must reconnect."
            )
        connection = await _refresh_connection(connection_id, sessionmaker, expiry_buffer_seconds)

    return decrypt(connection.encrypted_access_token)


__all__ = [
    "DRIVE_METADATA_SCOPE",
    "DRIVE_READONLY_SCOPE",
    "OAUTH_SCOPES",
    "USERINFO_EMAIL_SCOPE",
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
