"""Workflow definitions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from sqlalchemy import ColumnElement, exists, func, or_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.errors import AlreadyExists, add_or_already_exists, unique_violation_as_already_exists
from src.db.models import Workflow, WorkflowAssignment
from src.db.types import utcnow


@dataclass(frozen=True)
class WorkflowRecord:
    id: str
    user_id: str | None
    name: str
    description: str | None
    category: str | None
    tags: list[str]
    difficulty: str | None
    estimated_time: str | None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    version: str | None
    is_template: bool
    is_public: bool
    is_production: bool
    external_slug: str | None
    original_owner_email: str | None
    created_at: datetime
    updated_at: datetime


class Visibility(TypedDict):
    user_id: str
    is_admin: bool


class WorkflowValues(TypedDict):
    """The insertable columns; ``id`` is generated unless given (migration keeps OAB ids)."""

    name: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    id: NotRequired[str]
    user_id: NotRequired[str | None]
    description: NotRequired[str | None]
    category: NotRequired[str | None]
    tags: NotRequired[list[str] | None]
    difficulty: NotRequired[str | None]
    estimated_time: NotRequired[str | None]
    version: NotRequired[str | None]
    is_template: NotRequired[bool]
    is_public: NotRequired[bool]
    is_production: NotRequired[bool]
    external_slug: NotRequired[str | None]
    original_owner_email: NotRequired[str | None]
    created_at: NotRequired[datetime]
    updated_at: NotRequired[datetime]


class WorkflowChanges(TypedDict, total=False):
    """Columns ``update`` may change; keys that are absent are left alone."""

    user_id: str | None
    name: str
    description: str | None
    category: str | None
    tags: list[str]
    difficulty: str | None
    estimated_time: str | None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    version: str | None
    is_template: bool
    is_public: bool
    is_production: bool
    external_slug: str | None


def _record(r: Workflow) -> WorkflowRecord:
    return WorkflowRecord(
        id=r.id,
        user_id=r.user_id,
        name=r.name,
        description=r.description,
        category=r.category,
        tags=list(r.tags) if r.tags is not None else [],
        difficulty=r.difficulty,
        estimated_time=r.estimated_time,
        nodes=r.nodes,
        edges=r.edges,
        version=r.version,
        is_template=r.is_template,
        is_public=r.is_public,
        is_production=r.is_production,
        external_slug=r.external_slug,
        original_owner_email=r.original_owner_email,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )


def _contains_pattern(q: str) -> str:
    """Wildcards in ``q`` are deliberately NOT escaped: matches Prisma, pinned by the oracle."""
    return f"%{q}%"


def _assigned(user_id: str) -> ColumnElement[bool]:
    # EXISTS, never a join: a join would repeat a workflow once per assignment.
    return exists().where(
        WorkflowAssignment.workflow_id == Workflow.id, WorkflowAssignment.user_id == user_id
    )


def _visible(user_id: str) -> ColumnElement[bool]:
    return or_(Workflow.is_public.is_(True), Workflow.user_id == user_id, _assigned(user_id))


async def get(
    session: AsyncSession, workflow_id: str, *, for_update: bool = False
) -> WorkflowRecord | None:
    """One workflow by id.

    ``for_update`` takes a FOR NO KEY UPDATE row lock until the transaction ends: it
    serializes against an owner reassignment and a delete but not against inserts of
    rows that reference the workflow (executions, assignments). It also refreshes an
    instance already in the session, so the checks that follow see the locked row.
    """
    if for_update:
        row = await session.get(
            Workflow, workflow_id, with_for_update={"key_share": True}, populate_existing=True
        )
    else:
        row = await session.get(Workflow, workflow_id)
    return _record(row) if row else None


async def get_by_slug(session: AsyncSession, slug: str) -> WorkflowRecord | None:
    row = (await session.scalars(select(Workflow).where(Workflow.external_slug == slug))).first()
    return _record(row) if row else None


async def _page(
    session: AsyncSession, where: list[ColumnElement[bool]], limit: int, offset: int
) -> tuple[list[WorkflowRecord], int]:
    total = await session.scalar(select(func.count()).select_from(Workflow).where(*where))
    stmt = (
        select(Workflow)
        .where(*where)
        .order_by(Workflow.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = [_record(r) for r in await session.scalars(stmt)]
    return rows, total or 0


async def list_page(
    session: AsyncSession,
    *,
    viewer: Visibility,
    mine: bool,
    is_template: bool | None,
    is_public: bool | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[list[WorkflowRecord], int]:
    where: list[ColumnElement[bool]] = []
    user_id = viewer["user_id"]
    if mine:
        where.append(or_(Workflow.user_id == user_id, _assigned(user_id)))
    elif not viewer["is_admin"]:
        where.append(_visible(user_id))
    if is_template is not None:
        where.append(Workflow.is_template.is_(is_template))
    if is_public is not None:
        where.append(Workflow.is_public.is_(is_public))
    if category is not None:
        where.append(Workflow.category == category)
    return await _page(session, where, limit, offset)


async def search(
    session: AsyncSession, *, viewer: Visibility, q: str, limit: int
) -> tuple[list[WorkflowRecord], int]:
    pattern = _contains_pattern(q)
    where: list[ColumnElement[bool]] = [
        or_(Workflow.name.ilike(pattern), Workflow.description.ilike(pattern))
    ]
    if not viewer["is_admin"]:
        where.insert(0, _visible(viewer["user_id"]))
    return await _page(session, where, limit, 0)


async def create(session: AsyncSession, values: WorkflowValues) -> WorkflowRecord:
    row = Workflow(**values)
    await add_or_already_exists(session, row)
    return _record(row)


async def insert_if_absent(session: AsyncSession, values: WorkflowValues) -> bool:
    """Insert unless a row with this id exists. True when inserted (migration writer)."""
    if "id" in values and await session.get(Workflow, values["id"]) is not None:
        return False
    try:
        await create(session, values)
    except AlreadyExists:
        return False
    return True


async def update(
    session: AsyncSession, workflow_id: str, changes: WorkflowChanges
) -> WorkflowRecord | None:
    """Apply ``changes`` and bump ``updated_at``; ``AlreadyExists`` on a slug clash.

    Runs in a savepoint, so a clash leaves the caller's transaction usable.
    """
    stmt = (
        sa_update(Workflow)
        .where(Workflow.id == workflow_id)
        .values(**changes, updated_at=utcnow())
        .returning(Workflow)
        .execution_options(populate_existing=True)
    )
    async with unique_violation_as_already_exists(session):
        row = (await session.scalars(stmt)).one_or_none()
    return _record(row) if row else None


async def delete(session: AsyncSession, workflow_id: str) -> bool:
    """Delete one workflow; the database cascades its assignments and executions."""
    result = await session.execute(
        sa_delete(Workflow).where(Workflow.id == workflow_id).returning(Workflow.id)
    )
    return result.first() is not None


async def list_production(session: AsyncSession) -> list[WorkflowRecord]:
    stmt = select(Workflow).where(Workflow.is_production.is_(True))
    return [_record(r) for r in await session.scalars(stmt)]


async def delete_for_user(session: AsyncSession, user_id: str) -> int:
    result = await session.execute(
        sa_delete(Workflow).where(Workflow.user_id == user_id).returning(Workflow.id)
    )
    return len(result.all())


async def claim_orphans_by_email(session: AsyncSession, email: str, user_id: str) -> int:
    """Attach workflows imported with ``original_owner_email == email`` and no owner to ``user_id``."""
    result = await session.execute(
        sa_update(Workflow)
        .where(Workflow.original_owner_email == email, Workflow.user_id.is_(None))
        .values(user_id=user_id, updated_at=utcnow())
        .returning(Workflow.id)
    )
    return len(result.all())
