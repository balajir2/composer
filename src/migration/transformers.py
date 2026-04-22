"""Pure transforms: OAB Convex row -> Composer Prisma dict.

These functions are easy to unit-test because they have no side effects.
All DB writes live in writer.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _email_for_clerk_id(users: list[dict[str, Any]], clerk_user_id: str | None) -> str | None:
    """Resolve OAB's Clerk user_id -> email via the users export.

    OAB's users table has `clerkId` + optional `email`.  Returns None if
    no match OR if the matching row lacks an email.  Emails are lower-
    cased for consistent reconciliation later.
    """
    if not clerk_user_id:
        return None
    for u in users:
        if u.get("clerkId") == clerk_user_id:
            email = u.get("email")
            return email.lower() if isinstance(email, str) else None
    return None


def workflow_row(row: dict[str, Any], users: list[dict[str, Any]]) -> dict[str, Any]:
    """Transform an OAB `workflows` row into a Composer `workflows` insert dict.

    Sets user_id=None and original_owner_email=<email>.  Preserves isPublic.
    """
    return {
        "id": row["_id"],  # reuse OAB's opaque id; Prisma @id accepts any unique string
        "userId": None,
        "originalOwnerEmail": _email_for_clerk_id(users, row.get("userId")),
        "name": row["name"],
        "description": row.get("description"),
        "category": row.get("category"),
        "tags": row.get("tags") or [],
        "difficulty": row.get("difficulty"),
        "estimatedTime": row.get("estimatedTime"),
        "nodes": row.get("nodes") or [],
        "edges": row.get("edges") or [],
        "version": row.get("version"),
        "isTemplate": bool(row.get("isTemplate", False)),
        "isPublic": bool(row.get("isPublic", False)),
    }


def execution_row(row: dict[str, Any], users: list[dict[str, Any]]) -> dict[str, Any]:
    """Transform an OAB `executions` row.

    In-flight statuses ('running', 'waiting_approval') are rewritten to
    'failed' with an explanatory error — Phase 9 spec §4.4.  Generates a
    fresh thread_id since we don't migrate checkpoints.
    """
    import secrets

    source_status = row.get("status", "failed")
    if source_status in ("running", "waiting_approval"):
        final_status = "failed"
        final_error = row.get("error") or "migrated from OAB; in-flight state not recoverable"
    else:
        final_status = source_status
        final_error = row.get("error")

    return {
        "id": row["_id"],
        "workflowId": row["workflowId"],
        "userId": None,
        "originalOwnerEmail": _email_for_clerk_id(users, row.get("userId")),
        "status": final_status,
        "currentNodeId": row.get("currentNodeId"),
        "nodeResults": row.get("nodeResults") or {},
        "variables": row.get("variables") or {},
        "input": row.get("input"),
        "output": row.get("output"),
        "error": final_error,
        "threadId": f"migrated-{secrets.token_hex(8)}",
    }


def mcp_server_row(
    row: dict[str, Any],
    users: list[dict[str, Any]],
) -> dict[str, Any]:
    """Transform an OAB `mcpServers` row."""
    owner_email = _email_for_clerk_id(users, row.get("userId"))
    return {
        "id": row["_id"],
        "userId": None,  # set later via reconciliation
        "originalOwnerEmail": owner_email,
        "name": row["name"],
        "url": row["url"],
        "description": row.get("description"),
        "category": row.get("category"),
        "authType": row["authType"],
        "encryptedAccessToken": row.get("accessToken"),  # already encrypted by OAB
        "headerName": None,
        "oauthConfig": row.get("oauthConfig"),
        "tools": row.get("tools"),
        "connectionStatus": row.get("connectionStatus", "untested"),
        "lastTested": row.get("lastTested"),
        "lastError": row.get("lastError"),
        "enabled": bool(row.get("enabled", True)),
        "isOfficial": bool(row.get("isOfficial", False)),
        "isShared": bool(row.get("isShared", False)),
        "headers": row.get("headers"),
    }


def mcp_oauth_token_row(
    row: dict[str, Any],
    reencrypt: Callable[[str], str],
) -> dict[str, Any]:
    """Transform an OAB `mcpOAuthTokens` row, re-encrypting tokens.

    The `reencrypt` callable wraps: decrypt with OAB key, re-encrypt with
    Composer key.  Configured in runner.py.
    """
    return {
        "id": row["_id"],
        "mcpServerId": row["mcpServerId"],
        "userId": row["userId"],  # will be rewritten by reconciliation later
        "encryptedAccessToken": reencrypt(row["encryptedAccessToken"]),
        "encryptedRefreshToken": reencrypt(row["encryptedRefreshToken"])
        if row.get("encryptedRefreshToken")
        else None,
        "expiresAt": row.get("expiresAt"),
        "scope": row.get("scope"),
        "tokenType": row.get("tokenType") or "Bearer",
    }


__all__ = [
    "execution_row",
    "mcp_oauth_token_row",
    "mcp_server_row",
    "workflow_row",
]
