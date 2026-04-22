"""Unit tests for reconciliation logic (mocked Prisma)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.migration.reconcile import reconcile_by_email


async def test_reconcile_happy_path() -> None:
    db = MagicMock()
    db.user.find_unique = AsyncMock(return_value=SimpleNamespace(id="u1", email="alice@x.com"))
    db.workflow.update_many = AsyncMock(return_value=3)
    db.workflowexecution.update_many = AsyncMock(return_value=5)
    db.mcpserver.update_many = AsyncMock(return_value=1)

    result = await reconcile_by_email(db, "Alice@X.com")  # mixed case lowercased
    assert result == {"workflows": 3, "executions": 5, "mcpServers": 1}

    # Confirm update_many filters include user_id=None (idempotence)
    db.workflow.update_many.assert_awaited()
    wf_call = db.workflow.update_many.await_args
    assert wf_call is not None
    assert wf_call.kwargs["where"] == {"originalOwnerEmail": "alice@x.com", "userId": None}
    assert wf_call.kwargs["data"] == {"userId": "u1"}


async def test_reconcile_unknown_email_raises() -> None:
    db = MagicMock()
    db.user.find_unique = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="no Composer user found"):
        await reconcile_by_email(db, "ghost@x.com")
