"""Test-only self-service hard-delete for Playwright e2e accounts.

Not part of the public API surface -- exists purely to close the gap that
kept leaving Playwright test users (`pw-*@example.com`, created by
frontend/e2e/fixtures/test-user.ts and the ad-hoc registration in
frontend/e2e/external-invoke.spec.ts) and their workflows in the shared
dev Neon database after every e2e run -- 21 accumulated between
2026-07-10 and 2026-07-20 before this endpoint existed. The regular
account-deletion path (`DELETE /admin/users/{id}` in admin_users.py) is a
deliberate SOFT delete that preserves workflows for audit trail -- the
right default for real users, but it means test accounts had no way to
actually be removed and just piled up forever.

This endpoint is narrowly scoped so it can never touch a real account:
  1. Registered only when settings.environment != "production" (see
     src/main.py's conditional app.include_router call) -- unreachable in
     production regardless of what a caller sends.
  2. Also re-checked inside the handler (belt-and-suspenders in case this
     router is ever included some other way, e.g. a future refactor of
     src/main.py or a test that wires it up directly).
  3. Authenticated via the normal get_current_user_id dependency, so a
     caller can only ever delete the account whose bearer token they
     hold -- there is no target-user-id parameter, and no way to name
     someone else's account.
  4. The calling account's own email must match the exact pattern the
     e2e fixtures generate (`pw-<token>@example.com`) -- even a genuine
     dev-mode session for a real developer's own account can't be
     deleted through this route, since real accounts don't have that
     email shape.
"""

import logging
import re

from fastapi import APIRouter, Depends, HTTPException, status

from prisma import Prisma  # pyright: ignore[reportAttributeAccessIssue]
from src.config import get_settings
from src.security.auth import get_current_user_id
from src.storage.db import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["test-cleanup"])

# Matches every email frontend/e2e/fixtures/test-user.ts and the ad-hoc
# specs that don't use it (external-invoke.spec.ts's `pw-ext-...`)
# generate: a `pw-` prefix followed by anything, always @example.com.
_TEST_EMAIL_PATTERN = re.compile(r"^pw-[^@]+@example\.com$")


@router.delete("/internal/test-users/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_own_test_account(  # pyright: ignore[reportUnusedFunction]
    db: Prisma = Depends(get_db),  # pyright: ignore[reportUnknownParameterType]
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Hard-delete the calling account and everything it owns.

    Deletion order matters: workflows first, since their `onDelete:
    Cascade` FK (schema.prisma's WorkflowExecution/Approval relations)
    takes their executions and approvals with them at the DB level; then
    the `userId`-scoped tables that have no FK relation to User at all
    (workflows.userId itself included -- see schema.prisma's comment
    that it is a loose string, not a `@relation`) and so need an
    explicit delete rather than relying on cascade; then the user row
    itself, whose own `onDelete: Cascade` FK from ApiKey takes any
    remaining API keys with it.
    """
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    user = await db.user.find_unique(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not _TEST_EMAIL_PATTERN.match(user.email):  # pyright: ignore[reportUnknownMemberType]
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this endpoint only deletes accounts matching the e2e test-user pattern",
        )

    await db.workflow.delete_many(where={"userId": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.workflowassignment.delete_many(  # pyright: ignore[reportAttributeAccessIssue]
        where={"OR": [{"userId": user_id}, {"assignedById": user_id}]}
    )
    await db.mcpserver.delete_many(where={"userId": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.mcpoauthtoken.delete_many(where={"userId": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.mcpoauthstate.delete_many(where={"userId": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.cloudstorageconnection.delete_many(where={"userId": user_id})  # pyright: ignore[reportAttributeAccessIssue]
    await db.user.delete(where={"id": user_id})  # pyright: ignore[reportAttributeAccessIssue]

    logger.info("test_cleanup: hard-deleted e2e test account %s (%s)", user_id, user.email)


__all__ = ["router"]
