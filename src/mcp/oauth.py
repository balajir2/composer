"""MCP OAuth primitives.

All outbound OAuth calls (authorize URL, token exchange, token refresh,
client_credentials) include the RFC 8707 `resource` parameter (fix #1 from
CLAUDE.md §1). Tokens live server-side only, encrypted with
src/security/encryption.py (fix #4). get_valid_access_token falls back to
the server owner's token when a user accesses a shared server but has no
personal token (fix #5).

See Phase 3b spec §7, ADR-0011.
"""

import asyncio
import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import locks
from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos.mcp_oauth import McpOAuthStateRecord, McpOAuthTokenRecord
from src.db.repos.mcp_servers import McpServerRecord
from src.security.encryption import decrypt, decrypt_marked, encrypt


class OAuthError(RuntimeError):
    """Base class for all OAuth-layer errors."""


class InvalidStateError(OAuthError):
    """Callback state not found or expired."""


class TokenExchangeError(OAuthError):
    """IdP rejected the authorization code exchange."""


class TokenRefreshError(OAuthError):
    """IdP rejected the token refresh request."""


class McpTokenMissingError(OAuthError):
    """No token found for (server, user) and no fallback available."""


class McpTokenExpiredError(OAuthError):
    """Token expired with no refresh token available — user must re-authorize."""


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge_s256).

    Verifier: 64 random URL-safe bytes → 86-char string (within the 43-128
    range RFC 7636 permits). Challenge: SHA-256(verifier), base64url, no padding.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def generate_state(nbytes: int = 32) -> str:
    """URL-safe random state for CSRF protection."""
    return secrets.token_urlsafe(nbytes)


def derive_resource(server_url: str) -> str:
    """RFC 8707: resource identifier is the target server's origin (scheme://host[:port]).

    Highspot rejects token exchange when `resource` differs from the MCP URL's
    origin. Unit tests assert this on every outbound OAuth call.
    """
    parts = urlsplit(server_url)
    return f"{parts.scheme}://{parts.netloc}"


_STATE_TTL = timedelta(minutes=5)

# Shared by the callback route ("OAuth state not found (CSRF or replay).") and the
# consume-time error ("OAuth state '<state>' not found (CSRF or replay).").
STATE_NOT_FOUND_TAIL = "not found (CSRF or replay)."

_TOKEN_TIMEOUT = httpx.Timeout(30.0, connect=5.0)


async def _post_token_form(
    token_url: str,
    form: dict[str, Any],
    error_cls: type[OAuthError],
    failure_label: str,
) -> dict[str, Any]:
    """POST an OAuth token-endpoint form and return the JSON body.

    No session may be open, with one exception: ``refresh_token`` posts while it holds its
    advisory lock (and so one pooled connection).
    """
    async with httpx.AsyncClient(timeout=_TOKEN_TIMEOUT) as client:
        resp = await client.post(
            token_url,
            data=form,
            headers={"Accept": "application/json"},
        )
    if resp.status_code >= 400:
        raise error_cls(f"{failure_label} (HTTP {resp.status_code}): {resp.text[:200]}")
    payload: dict[str, Any] = resp.json()
    return payload


# Short-session rule: every database step below opens its own session and closes it before
# the next network call, so a pooled connection is never held across an IdP request. The one
# exception is ``refresh_token``, which holds a session across its refresh POST so the advisory
# lock that serializes refreshes lives as long as the POST.

Sessionmaker = async_sessionmaker[AsyncSession]


async def build_authorize_url(
    server: McpServerRecord,
    user_id: str,
    redirect_uri: str,
    sessionmaker: Sessionmaker,
) -> str:
    """Return the OAuth authorize URL; insert McpOAuthState for the callback.

    The returned URL includes the RFC 8707 `resource` parameter (fix #1).
    """
    config = server.oauth_config
    if not config:
        raise ValueError(
            f"MCP server {server.id!r} has no oauthConfig; cannot build authorize URL."
        )
    authorize_url = config.get("authorizeUrl")
    client_id = config.get("clientId")
    scopes: list[Any] = config.get("scopes") or []
    scope_str = " ".join(scopes)
    if not authorize_url or not client_id:
        raise ValueError(f"MCP server {server.id!r} oauthConfig missing authorizeUrl or clientId.")

    state = generate_state()
    verifier, challenge = generate_pkce_pair()
    now = datetime.now(UTC)

    # Reap expired states of any server or user, then record this one: one short session.
    async with sessionmaker() as session:
        await mcp_oauth_repo.reap_expired_states(session, now)
        await mcp_oauth_repo.create_state(
            session,
            mcp_oauth_repo.StateValues(
                mcp_server_id=server.id,
                user_id=user_id,
                state=state,
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                scope=scope_str or None,
                expires_at=now + _STATE_TTL,
            ),
        )
        await session.commit()

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": scope_str,
        "resource": derive_resource(server.url),  # fix #1
    }
    return f"{authorize_url}?{urlencode(params)}"


async def _consume_state_or_raise(state: str, sessionmaker: Sessionmaker) -> McpOAuthStateRecord:
    """Atomically take the one-shot state row; its own short session, closed on return.

    The delete is the single point of consumption, so of two callbacks carrying the same
    state exactly one gets the row. The other (and an expired state, which is left in
    place) learns why from a follow-up read.
    """
    async with sessionmaker() as session:
        row = await mcp_oauth_repo.consume_state(session, state, datetime.now(UTC))
        await session.commit()
        if row is not None:
            return row
        # Reading after the commit is intentional: it diagnoses expired (row still there)
        # versus consumed or never issued (row gone).
        still_there = await mcp_oauth_repo.get_state(session, state)
    if still_there is not None:
        raise InvalidStateError(f"OAuth state {state!r} has expired.")
    raise InvalidStateError(f"OAuth state {state!r} {STATE_NOT_FOUND_TAIL}")


def _expires_at_from_in(expires_in: int | None) -> datetime | None:
    if expires_in is None:
        return None
    return datetime.now(UTC) + timedelta(seconds=int(expires_in))


async def exchange_code_for_tokens(
    server: McpServerRecord,
    code: str,
    state: str,
    sessionmaker: Sessionmaker,
) -> McpOAuthTokenRecord:
    """Exchange authorization code for tokens; store encrypted in McpOAuthToken.

    Token-exchange POST includes RFC 8707 `resource` (fix #1). Order: consume the state
    (short session) -> token POST (no session) -> store the token (short session).
    """
    state_row = await _consume_state_or_raise(state, sessionmaker)

    config: dict[str, Any] = server.oauth_config or {}
    token_url = config["tokenUrl"]
    client_id = config["clientId"]
    # P0-5: clientSecret is encrypted at rest by the mcp-servers API;
    # decrypt_marked passes through unmarked (pre-existing plaintext)
    # values unchanged, so this is safe for servers saved before
    # encryption existed too.
    client_secret = decrypt_marked(config.get("clientSecret", ""))

    form: dict[str, Any] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_row.redirect_uri,
        "code_verifier": state_row.code_verifier,
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": derive_resource(server.url),  # fix #1
    }

    payload = await _post_token_form(
        token_url, form, TokenExchangeError, "OAuth token exchange failed"
    )
    access_token = payload.get("access_token", "")
    refresh_token: str | None = payload.get("refresh_token")
    expires_at = _expires_at_from_in(payload.get("expires_in"))

    values = mcp_oauth_repo.TokenValues(
        encrypted_access_token=encrypt(access_token),
        encrypted_refresh_token=encrypt(refresh_token) if refresh_token else None,
        expires_at=expires_at,
        scope=payload.get("scope") or state_row.scope,
        token_type=payload.get("token_type", "Bearer"),
    )

    async def _store(session: AsyncSession) -> McpOAuthTokenRecord:
        stored = await mcp_oauth_repo.upsert_token(session, server.id, state_row.user_id, values)
        await session.commit()
        return stored

    # Same natural-key lock as refresh_token, so an in-flight refresh cannot overwrite this
    # re-authorization. A refused caller waits holding no session (see locks.run_under_lock).
    try:
        stored = await locks.run_under_lock(
            sessionmaker,
            mcp_oauth_repo.token_lock_key(server.id, state_row.user_id),
            _store,
            wait_s=_REFRESH_WAIT_DEADLINE_S,
        )
    except locks.LockTimeoutError as exc:
        raise TokenExchangeError(
            f"Timed out waiting for a token refresh of server {server.id!r} to finish; try again."
        ) from exc
    return stored


_EXPIRY_BUFFER_SECONDS = 60
# The refresh POST is bounded; a waiter's deadline must exceed it so a waiter outlives one
# in-flight refresh.
_REFRESH_POST_TIMEOUT_S = 30.0
_REFRESH_WAIT_DEADLINE_S = 35.0


def _needs_refresh(token: McpOAuthTokenRecord, buffer_seconds: int) -> bool:
    """True when the access token is expired or within ``buffer_seconds`` of expiring."""
    if token.expires_at is None:
        return False
    return datetime.now(UTC) >= token.expires_at - timedelta(seconds=buffer_seconds)


async def refresh_token(
    server: McpServerRecord,
    token_row: McpOAuthTokenRecord,
    sessionmaker: Sessionmaker,
    *,
    expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS,
) -> McpOAuthTokenRecord:
    """Refresh an expired access token, once even when several callers race. POST includes
    RFC 8707 resource (fix #1).

    Serialized per token with a transaction-scoped Postgres advisory lock keyed on the
    (server, user) pair. A caller refused the lock holds no connection while it waits: it
    sleeps, re-reads the row in a short session and returns it once the winner has refreshed
    it (IdPs that detect refresh-token reuse may revoke a token posted twice). The row refreshed
    is the one passed in, by id: a shared server's owner row on fallback, and no row is ever
    created for the caller. Keeps the old refresh_token if the IdP doesn't return a new one.

    Raises ``TokenRefreshError`` if the IdP call fails, if it takes longer than
    ``_REFRESH_POST_TIMEOUT_S``, or if the lock is not granted within ``_REFRESH_WAIT_DEADLINE_S``.

    SHORT-SESSION RULE EXCEPTION: the winner holds ONE session (one pooled connection) open
    across the token POST, because the lock lives exactly as long as the transaction. The POST
    is the only network call allowed inside an open session.
    """
    if not token_row.encrypted_refresh_token:
        raise McpTokenExpiredError(
            f"Token for server {server.id!r} has no refresh token; user must re-authorize."
        )
    removed = f"Token for server {server.id!r} was removed while it was being refreshed."

    async def _check() -> McpOAuthTokenRecord | None:
        """Between attempts, in its own short session: has the winner finished?"""
        async with sessionmaker() as session:
            current = await mcp_oauth_repo.get_token_by_id(session, token_row.id)
        if current is None:
            raise McpTokenMissingError(removed)
        return None if _needs_refresh(current, expiry_buffer_seconds) else current

    async def _refresh(session: AsyncSession) -> McpOAuthTokenRecord:
        current = await mcp_oauth_repo.get_token_by_id(session, token_row.id)
        if current is None:  # disconnected before we got the lock
            raise McpTokenMissingError(removed)
        if not _needs_refresh(current, expiry_buffer_seconds):
            await session.commit()  # refreshed by a caller that held the lock before us
            return current
        if not current.encrypted_refresh_token:
            raise McpTokenExpiredError(
                f"Token for server {server.id!r} has no refresh token; user must re-authorize."
            )

        config: dict[str, Any] = server.oauth_config or {}
        token_url = config["tokenUrl"]
        client_id = config["clientId"]
        # P0-5: see exchange_code_for_tokens.
        client_secret = decrypt_marked(config.get("clientSecret", ""))
        form: dict[str, Any] = {
            "grant_type": "refresh_token",
            "refresh_token": decrypt(current.encrypted_refresh_token),
            "client_id": client_id,
            "client_secret": client_secret,
            "resource": derive_resource(server.url),  # fix #1
        }
        # A failure leaves the session without committing: it rolls back, the lock is
        # released and the row is unchanged.
        try:
            async with asyncio.timeout(_REFRESH_POST_TIMEOUT_S):
                payload = await _post_token_form(
                    token_url, form, TokenRefreshError, "OAuth refresh failed"
                )
        except TimeoutError as exc:
            raise TokenRefreshError(
                f"OAuth refresh for server {server.id!r} timed out after "
                f"{_REFRESH_POST_TIMEOUT_S:g}s"
            ) from exc
        access_token = payload.get("access_token", "")
        new_refresh: str | None = payload.get("refresh_token")

        changes = mcp_oauth_repo.TokenChanges(
            encrypted_access_token=encrypt(access_token),
            expires_at=_expires_at_from_in(payload.get("expires_in")),
        )
        if new_refresh:
            changes["encrypted_refresh_token"] = encrypt(new_refresh)
        # else: no rotation, the stored refresh token is left as it is.

        updated = await mcp_oauth_repo.update_token(session, current.id, changes)
        if updated is None:  # deleted while the IdP call was in flight
            raise McpTokenMissingError(removed)
        await session.commit()  # releases the advisory lock
        return updated

    try:
        return await locks.run_under_lock(
            sessionmaker,
            mcp_oauth_repo.token_lock_key(token_row.mcp_server_id, token_row.user_id),
            _refresh,
            wait_s=_REFRESH_WAIT_DEADLINE_S,
            check=_check,
        )
    except locks.LockTimeoutError as exc:
        raise TokenRefreshError(
            f"Timed out waiting for a concurrent token refresh of server {server.id!r}."
        ) from exc


async def get_valid_access_token(
    server: McpServerRecord,
    user_id: str | None,
    sessionmaker: Sessionmaker,
    *,
    expiry_buffer_seconds: int = _EXPIRY_BUFFER_SECONDS,
) -> str:
    """Return a decrypted plaintext access token, refreshing if near-expiry.

    For shared servers, falls back to the owner's token when the user has no
    personal one (fix #5). Never leaks a token to a caller that can't access
    the server (resolver handles that check before we get here).

    The fast path reads in one short session, closed before anything else. A refresh runs in
    its own session under an advisory lock (see ``refresh_token``, a documented exception to
    the short-session rule) and writes to whichever row the token came from (the owner's, on
    fallback).
    """
    effective_user = user_id or ""
    async with sessionmaker() as session:
        token = await mcp_oauth_repo.get_token(session, server.id, effective_user)

        # Service-account fallback for shared servers (fix #5)
        if (
            token is None
            and server.is_shared
            and server.user_id is not None
            and effective_user != server.user_id
        ):
            token = await mcp_oauth_repo.get_token(session, server.id, server.user_id)
            if token is None:
                raise McpTokenMissingError(
                    f"No token for user {user_id!r} on MCP server {server.id!r}, "
                    f"and server owner {server.user_id!r} has no token either "
                    f"(service-account fallback failed). "
                    f"The server owner must complete the OAuth flow to act as a service account."
                )
        elif token is None:
            raise McpTokenMissingError(
                f"No OAuth token found for user {user_id!r} on MCP server {server.id!r}. "
                f"User must complete the OAuth flow first."
            )

    # Refresh-on-use with expiry buffer
    if _needs_refresh(token, expiry_buffer_seconds):
        if not token.encrypted_refresh_token:
            raise McpTokenExpiredError(
                f"Token for server {server.id!r} expired and has no refresh token; "
                f"user must re-authorize."
            )
        token = await refresh_token(
            server, token, sessionmaker, expiry_buffer_seconds=expiry_buffer_seconds
        )

    return decrypt(token.encrypted_access_token)


__all__ = [
    "InvalidStateError",
    "McpTokenExpiredError",
    "McpTokenMissingError",
    "OAuthError",
    "TokenExchangeError",
    "TokenRefreshError",
    "build_authorize_url",
    "derive_resource",
    "exchange_code_for_tokens",
    "generate_pkce_pair",
    "generate_state",
    "get_valid_access_token",
    "refresh_token",
]
