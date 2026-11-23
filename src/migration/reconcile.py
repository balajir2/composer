"""Post-migration reconciliation: claim orphan rows for a Composer user by email."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.db.repos import workflows as workflows_repo
from src.db.runtime import one_shot_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def reconcile_by_email(db: Prisma, session: AsyncSession, email: str) -> dict[str, int]:  # pyright: ignore[reportUnknownParameterType]
    """Attach all rows with original_owner_email=<email> to the User with that email.

    Returns a per-table count of updated rows.  Idempotent — re-runs UPDATE
    only the rows that still have user_id=NULL.
    """
    email_lc = email.lower()
    user = await users_repo.get_by_email(session, email_lc)
    if user is None:
        raise ValueError(f"no Composer user found with email {email_lc!r}")

    result: dict[str, int] = {}

    result["workflows"] = await workflows_repo.claim_orphans_by_email(session, email_lc, user.id)

    exec_updated = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"originalOwnerEmail": email_lc, "userId": None},
        data={"userId": user.id},
    )
    result["executions"] = exec_updated

    result["mcpServers"] = await mcp_servers_repo.claim_orphans_by_email(session, email_lc, user.id)

    return result


async def run_reconcile(*, email: str) -> int:
    """CLI entry point.  Returns 0 on success, 1 on missing user."""
    db = Prisma()
    await db.connect()
    try:
        try:
            async with one_shot_session(get_settings()) as session:
                result = await reconcile_by_email(db, session, email)
                await session.commit()
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("reconciled: %s", result)
        return 0
    finally:
        await db.disconnect()


__all__ = ["reconcile_by_email", "run_reconcile"]
