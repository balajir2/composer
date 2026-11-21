"""Unit tests for reconciliation logic (mocked Prisma)."""

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos import users as users_repo
from src.migration.reconcile import reconcile_by_email
from tests.unit.db_fakes import FakeSession, make_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def test_reconcile_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    db = MagicMock()
    get_by_email = AsyncMock(return_value=make_user(id="u1", email="alice@x.com"))
    monkeypatch.setattr(users_repo, "get_by_email", get_by_email)
    db.workflow.update_many = AsyncMock(return_value=3)
    db.workflowexecution.update_many = AsyncMock(return_value=5)
    claim = AsyncMock(return_value=1)
    monkeypatch.setattr(mcp_servers_repo, "claim_orphans_by_email", claim)
    session = FakeSession()

    result = await reconcile_by_email(
        db, cast("AsyncSession", session), "Alice@X.com"
    )  # mixed case lowercased
    assert result == {"workflows": 3, "executions": 5, "mcpServers": 1}
    assert get_by_email.await_args is not None
    assert get_by_email.await_args.args[1] == "alice@x.com"

    claim.assert_awaited_once_with(session, "alice@x.com", "u1")

    # Confirm update_many filters include user_id=None (idempotence)
    db.workflow.update_many.assert_awaited()
    wf_call = db.workflow.update_many.await_args
    assert wf_call is not None
    assert wf_call.kwargs["where"] == {"originalOwnerEmail": "alice@x.com", "userId": None}
    assert wf_call.kwargs["data"] == {"userId": "u1"}


async def test_reconcile_unknown_email_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    db = MagicMock()
    monkeypatch.setattr(users_repo, "get_by_email", AsyncMock(return_value=None))
    with pytest.raises(ValueError, match="no Composer user found"):
        await reconcile_by_email(db, cast("AsyncSession", FakeSession()), "ghost@x.com")
