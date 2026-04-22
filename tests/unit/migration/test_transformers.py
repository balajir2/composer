"""Unit tests for migration transformers (no DB)."""

from __future__ import annotations

from src.migration.transformers import (
    execution_row,
    mcp_server_row,
    workflow_row,
)


def _users_fixture() -> list[dict]:  # type: ignore[type-arg]
    return [
        {"_id": "u1", "clerkId": "user_2alice", "email": "alice@example.com", "name": "Alice"},
        {"_id": "u2", "clerkId": "user_2bob", "email": "bob@example.com", "name": "Bob"},
    ]


def test_workflow_row_populates_owner_email_and_nulls_user_id() -> None:
    src = {
        "_id": "wf1",
        "userId": "user_2alice",
        "name": "W1",
        "nodes": [],
        "edges": [],
        "isPublic": False,
        "isTemplate": False,
        "tags": ["a"],
    }
    out = workflow_row(src, _users_fixture())
    assert out["id"] == "wf1"
    assert out["userId"] is None
    assert out["originalOwnerEmail"] == "alice@example.com"
    assert out["isPublic"] is False
    assert out["tags"] == ["a"]


def test_workflow_row_lowercases_email() -> None:
    users = [{"_id": "u", "clerkId": "user_X", "email": "MIXED@case.com"}]
    out = workflow_row(
        {"_id": "w", "userId": "user_X", "name": "x", "nodes": [], "edges": []}, users
    )
    assert out["originalOwnerEmail"] == "mixed@case.com"


def test_workflow_row_handles_missing_email() -> None:
    users = [{"_id": "u", "clerkId": "user_X"}]  # no email on OAB user
    out = workflow_row(
        {"_id": "w", "userId": "user_X", "name": "x", "nodes": [], "edges": []}, users
    )
    assert out["originalOwnerEmail"] is None


def test_workflow_row_handles_unknown_clerk_id() -> None:
    out = workflow_row(
        {"_id": "w", "userId": "user_UNKNOWN", "name": "x", "nodes": [], "edges": []},
        _users_fixture(),
    )
    assert out["originalOwnerEmail"] is None


def test_execution_row_rewrites_running_to_failed() -> None:
    out = execution_row(
        {
            "_id": "e1",
            "workflowId": "w1",
            "userId": "user_2alice",
            "status": "running",
            "nodeResults": {},
            "variables": {},
        },
        _users_fixture(),
    )
    assert out["status"] == "failed"
    assert "not recoverable" in (out["error"] or "")


def test_execution_row_preserves_completed() -> None:
    out = execution_row(
        {
            "_id": "e1",
            "workflowId": "w1",
            "userId": "user_2alice",
            "status": "completed",
            "nodeResults": {},
            "variables": {},
            "output": "ok",
        },
        _users_fixture(),
    )
    assert out["status"] == "completed"
    assert out["output"] == "ok"


def test_execution_row_generates_fresh_thread_id() -> None:
    out = execution_row(
        {
            "_id": "e1",
            "workflowId": "w1",
            "userId": "user_2alice",
            "status": "completed",
            "threadId": "oab-thread-1",
            "nodeResults": {},
            "variables": {},
        },
        _users_fixture(),
    )
    assert out["threadId"].startswith("migrated-")
    assert out["threadId"] != "oab-thread-1"


def test_mcp_server_row_reuses_oab_encrypted_token() -> None:
    out = mcp_server_row(
        {
            "_id": "m1",
            "userId": "user_2alice",
            "name": "X",
            "url": "https://x",
            "authType": "api-key",
            "accessToken": "encrypted-ct",
        },
        _users_fixture(),
    )
    assert out["encryptedAccessToken"] == "encrypted-ct"
    assert out["originalOwnerEmail"] == "alice@example.com"
    assert out["userId"] is None
