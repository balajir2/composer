"""MCP server registrations."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from sqlalchemy import delete as sa_delete
from sqlalchemy import or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, add_or_already_exists
from src.db.models import McpServer
from src.db.types import utcnow


@dataclass(frozen=True)
class McpServerRecord:
    id: str
    user_id: str | None
    name: str
    url: str
    description: str | None
    category: str | None
    auth_type: str
    encrypted_access_token: str | None
    header_name: str | None
    oauth_config: dict[str, Any] | None
    tools: list[dict[str, Any]] | None
    connection_status: str
    last_tested: datetime | None
    last_error: str | None
    enabled: bool
    is_official: bool
    is_shared: bool
    headers: dict[str, str] | None
    created_at: datetime
    updated_at: datetime
    original_owner_email: str | None


class McpServerCreate(TypedDict):
    """The insertable columns; ``id`` is generated unless given (migration keeps OAB ids)."""

    name: str
    url: str
    auth_type: str
    id: NotRequired[str]
    user_id: NotRequired[str | None]
    description: NotRequired[str | None]
    category: NotRequired[str | None]
    encrypted_access_token: NotRequired[str | None]
    header_name: NotRequired[str | None]
    oauth_config: NotRequired[dict[str, Any] | None]
    tools: NotRequired[list[dict[str, Any]] | None]
    connection_status: NotRequired[str]
    last_tested: NotRequired[datetime | None]
    last_error: NotRequired[str | None]
    enabled: NotRequired[bool]
    is_official: NotRequired[bool]
    is_shared: NotRequired[bool]
    headers: NotRequired[dict[str, str] | None]
    original_owner_email: NotRequired[str | None]


class McpServerChanges(TypedDict, total=False):
    """Columns ``update`` may change; keys that are absent are left alone."""

    user_id: str | None
    oauth_config: dict[str, Any] | None
    is_shared: bool
    connection_status: str
    last_tested: datetime | None
    last_error: str | None
    tools: list[dict[str, Any]] | None


def _record(r: McpServer) -> McpServerRecord:
    return McpServerRecord(
        id=r.id,
        user_id=r.user_id,
        name=r.name,
        url=r.url,
        description=r.description,
        category=r.category,
        auth_type=r.auth_type,
        encrypted_access_token=r.encrypted_access_token,
        header_name=r.header_name,
        oauth_config=r.oauth_config,
        tools=r.tools,
        connection_status=r.connection_status,
        last_tested=r.last_tested,
        last_error=r.last_error,
        enabled=r.enabled,
        is_official=r.is_official,
        is_shared=r.is_shared,
        headers=r.headers,
        created_at=r.created_at,
        updated_at=r.updated_at,
        original_owner_email=r.original_owner_email,
    )


# get may answer from the session's identity map. The ORM UPDATE ... RETURNING statements
# refresh already-loaded rows (populate_existing), so a held ORM object never goes stale.
async def get(session: AsyncSession, server_id: str) -> McpServerRecord | None:
    row = await session.get(McpServer, server_id)
    return _record(row) if row else None


async def list_visible(session: AsyncSession, user_id: str) -> list[McpServerRecord]:
    """The user's own servers plus every shared one (no defined order, as before)."""
    stmt = select(McpServer).where(or_(McpServer.user_id == user_id, McpServer.is_shared.is_(True)))
    return [_record(r) for r in await session.scalars(stmt)]


async def create(session: AsyncSession, values: McpServerCreate) -> McpServerRecord:
    row = McpServer(**values)
    await add_or_already_exists(session, row)
    return _record(row)


async def insert_if_absent(session: AsyncSession, values: McpServerCreate) -> bool:
    """Insert unless a row with this id exists. True when inserted (migration writer)."""
    if "id" in values and await session.get(McpServer, values["id"]) is not None:
        return False
    try:
        await create(session, values)
    except AlreadyExists:
        return False
    return True


async def update(
    session: AsyncSession, server_id: str, changes: McpServerChanges
) -> McpServerRecord | None:
    stmt = (
        sa_update(McpServer)
        .where(McpServer.id == server_id)
        .values(**changes, updated_at=utcnow())
        .returning(McpServer)
        .execution_options(populate_existing=True)
    )
    row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def delete(session: AsyncSession, server_id: str) -> bool:
    result = await session.execute(
        sa_delete(McpServer).where(McpServer.id == server_id).returning(McpServer.id)
    )
    return result.first() is not None


async def delete_for_user(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        sa_delete(McpServer).where(McpServer.user_id == user_id).returning(McpServer.id)
    )
    return len(result.all())


async def claim_orphans_by_email(session: AsyncSession, email: str, user_id: str) -> int:
    """Attach servers imported with ``original_owner_email == email`` and no owner to ``user_id``."""
    result = await session.execute(
        sa_update(McpServer)
        .where(McpServer.original_owner_email == email, McpServer.user_id.is_(None))
        .values(user_id=user_id, updated_at=utcnow())
        .returning(McpServer.id)
    )
    return len(result.all())
