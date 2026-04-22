"""Post-migration reconciliation: claim orphan rows for a Composer user by email."""

from __future__ import annotations

import logging

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]

logger = logging.getLogger(__name__)


async def reconcile_by_email(db: Prisma, email: str) -> dict[str, int]:  # pyright: ignore[reportUnknownParameterType]
    """Attach all rows with original_owner_email=<email> to the User with that email.

    Returns a per-table count of updated rows.  Idempotent — re-runs UPDATE
    only the rows that still have user_id=NULL.
    """
    email_lc = email.lower()
    user = await db.user.find_unique(where={"email": email_lc})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise ValueError(f"no Composer user found with email {email_lc!r}")

    result: dict[str, int] = {}

    wf_updated = await db.workflow.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"originalOwnerEmail": email_lc, "userId": None},
        data={"userId": user.id},
    )
    result["workflows"] = wf_updated

    exec_updated = await db.workflowexecution.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"originalOwnerEmail": email_lc, "userId": None},
        data={"userId": user.id},
    )
    result["executions"] = exec_updated

    mcp_updated = await db.mcpserver.update_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"originalOwnerEmail": email_lc, "userId": None},
        data={"userId": user.id},
    )
    result["mcpServers"] = mcp_updated

    return result


async def run_reconcile(*, email: str) -> int:
    """CLI entry point.  Returns 0 on success, 1 on missing user."""
    db = Prisma()
    await db.connect()
    try:
        try:
            result = await reconcile_by_email(db, email)
        except ValueError as exc:
            logger.error("%s", exc)
            return 1
        logger.info("reconciled: %s", result)
        return 0
    finally:
        await db.disconnect()


__all__ = ["reconcile_by_email", "run_reconcile"]
