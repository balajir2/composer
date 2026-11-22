"""MCP OAuth tokens (one per server and user) and short-lived authorization states."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, add_or_already_exists
from src.db.ids import new_id
from src.db.models import McpOAuthState, McpOAuthToken
from src.db.types import utcnow


@dataclass(frozen=True)
class McpOAuthTokenRecord:
    id: str
    mcp_server_id: str
    user_id: str
    encrypted_access_token: str
    encrypted_refresh_token: str | None
    expires_at: datetime | None
    scope: str | None
    token_type: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class McpOAuthStateRecord:
    id: str
    mcp_server_id: str
    user_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    scope: str | None
    expires_at: datetime
    created_at: datetime


class TokenValues(TypedDict):
    """What a code exchange stores; an upsert keeps the stored refresh token and scope when NULL."""

    encrypted_access_token: str
    encrypted_refresh_token: str | None
    expires_at: datetime | None
    scope: str | None
    token_type: str


class TokenChanges(TypedDict, total=False):
    """Columns a refresh may change; scope, token type and created_at are never touched."""

    encrypted_access_token: str
    encrypted_refresh_token: str | None
    expires_at: datetime | None


class TokenInsert(TokenValues):
    """A complete row for the migration writer, which keeps the source id."""

    id: str
    mcp_server_id: str
    user_id: str


class StateValues(TypedDict):
    mcp_server_id: str
    user_id: str
    state: str
    code_verifier: str
    redirect_uri: str
    scope: str | None
    expires_at: datetime


def _token(r: McpOAuthToken) -> McpOAuthTokenRecord:
    return McpOAuthTokenRecord(
        id=r.id,
        mcp_server_id=r.mcp_server_id,
        user_id=r.user_id,
        encrypted_access_token=r.encrypted_access_token,
        encrypted_refresh_token=r.encrypted_refresh_token,
        expires_at=r.expires_at,
        scope=r.scope,
        token_type=r.token_type,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _state(r: McpOAuthState) -> McpOAuthStateRecord:
    return McpOAuthStateRecord(
        id=r.id,
        mcp_server_id=r.mcp_server_id,
        user_id=r.user_id,
        state=r.state,
        code_verifier=r.code_verifier,
        redirect_uri=r.redirect_uri,
        scope=r.scope,
        expires_at=r.expires_at,
        created_at=r.created_at,
    )


# ─── tokens ────────────────────────────────────────────────────────────────


async def get_token(
    session: AsyncSession, server_id: str, user_id: str
) -> McpOAuthTokenRecord | None:
    stmt = select(McpOAuthToken).where(
        McpOAuthToken.mcp_server_id == server_id, McpOAuthToken.user_id == user_id
    )
    row = await session.scalar(stmt)
    return _token(row) if row else None


async def get_token_by_id(session: AsyncSession, token_id: str) -> McpOAuthTokenRecord | None:
    """One token row by id, read fresh (never from the session's identity map)."""
    row = await session.scalar(
        select(McpOAuthToken)
        .where(McpOAuthToken.id == token_id)
        .execution_options(populate_existing=True)
    )
    return _token(row) if row else None


async def upsert_token(
    session: AsyncSession, server_id: str, user_id: str, values: TokenValues
) -> McpOAuthTokenRecord:
    """One atomic statement; a re-authorization updates the stored token in place, keeping the
    refresh token and scope when the new grant omits them."""
    now = utcnow()  # ON CONFLICT bypasses the ORM's onupdate, so set it here
    ins = insert(McpOAuthToken).values(
        id=new_id(),
        mcp_server_id=server_id,
        user_id=user_id,
        created_at=now,
        updated_at=now,
        **values,
    )
    stmt = (
        ins.on_conflict_do_update(
            index_elements=[McpOAuthToken.mcp_server_id, McpOAuthToken.user_id],
            set_={
                **values,
                # A re-grant that omits these keeps the stored value (Google and some IdPs
                # omit refresh_token and scope on re-consent); the first grant is unaffected.
                "encrypted_refresh_token": func.coalesce(
                    ins.excluded.encrypted_refresh_token,
                    McpOAuthToken.encrypted_refresh_token,
                ),
                "scope": func.coalesce(ins.excluded.scope, McpOAuthToken.scope),
                "updated_at": now,
            },
        )
        .returning(McpOAuthToken)
        # Refresh a row already loaded in the session instead of returning stale values.
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one()
    return _token(row)


async def update_token(
    session: AsyncSession, token_id: str, changes: TokenChanges
) -> McpOAuthTokenRecord | None:
    stmt = (
        sa_update(McpOAuthToken)
        .where(McpOAuthToken.id == token_id)
        .values(**changes, updated_at=utcnow())
        .returning(McpOAuthToken)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _token(row) if row else None


async def delete_token(session: AsyncSession, token_id: str) -> bool:
    result = await session.execute(
        sa_delete(McpOAuthToken).where(McpOAuthToken.id == token_id).returning(McpOAuthToken.id)
    )
    return result.first() is not None


async def delete_tokens_for_user(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        sa_delete(McpOAuthToken).where(McpOAuthToken.user_id == user_id).returning(McpOAuthToken.id)
    )
    return len(result.all())


async def insert_token_if_absent(session: AsyncSession, values: TokenInsert) -> bool:
    """Insert unless a row with this id exists. True when inserted (migration writer)."""
    if await session.get(McpOAuthToken, values["id"]) is not None:
        return False
    try:
        await add_or_already_exists(session, McpOAuthToken(**values))
    except AlreadyExists:
        return False
    return True


# ─── states ────────────────────────────────────────────────────────────────


async def create_state(session: AsyncSession, values: StateValues) -> McpOAuthStateRecord:
    row = McpOAuthState(**values)
    await add_or_already_exists(session, row)
    return _state(row)


async def get_state(session: AsyncSession, state: str) -> McpOAuthStateRecord | None:
    row = await session.scalar(select(McpOAuthState).where(McpOAuthState.state == state))
    return _state(row) if row else None


async def consume_state(
    session: AsyncSession, state: str, now: datetime
) -> McpOAuthStateRecord | None:
    """Delete and return an unexpired state: single-use, and exactly one concurrent caller wins.

    None means the state is unknown, already consumed or expired (an expired row is left alone);
    ``get_state`` tells the cases apart.
    """
    stmt = (
        sa_delete(McpOAuthState)
        .where(McpOAuthState.state == state, McpOAuthState.expires_at > now)
        .returning(McpOAuthState)
        .execution_options(synchronize_session=False)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _state(row) if row else None


async def reap_expired_states(session: AsyncSession, now: datetime) -> int:
    result = await session.execute(
        sa_delete(McpOAuthState).where(McpOAuthState.expires_at < now).returning(McpOAuthState.id)
    )
    return len(result.all())


async def delete_states_for_user(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        sa_delete(McpOAuthState).where(McpOAuthState.user_id == user_id).returning(McpOAuthState.id)
    )
    return len(result.all())
