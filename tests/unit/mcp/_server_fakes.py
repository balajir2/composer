"""Shared fakes for resolver tests: a sessionmaker whose sessions carry one scripted server row."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos.mcp_servers import McpServerRecord
from tests.unit.db_fakes import FakeSession, FakeSessionmaker

_WHEN = datetime(2026, 4, 20, tzinfo=UTC)

_DEFAULT_SERVER = McpServerRecord(
    id="srv1",
    user_id="dev",
    name="T",
    url="https://m.example.com/rpc",
    description=None,
    category=None,
    auth_type="none",
    encrypted_access_token=None,
    header_name=None,
    oauth_config=None,
    tools=None,
    connection_status="untested",
    last_tested=None,
    last_error=None,
    enabled=True,
    is_official=False,
    is_shared=False,
    headers=None,
    created_at=_WHEN,
    updated_at=_WHEN,
    original_owner_email=None,
)


def make_server(**overrides: Any) -> McpServerRecord:
    """The default server record with ``overrides`` applied (unknown field names raise)."""
    return replace(_DEFAULT_SERVER, **overrides)


class _ServerSession(FakeSession):
    server: McpServerRecord | None = None


def sessionmaker_with(server: McpServerRecord | None) -> FakeSessionmaker:
    session = _ServerSession()
    session.server = server
    return FakeSessionmaker(session)


def install_server_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch mcp_servers_repo.get to answer from the server carried by the fake session.

    It returns that server only when the requested id matches, else None, like the real repo.
    """

    async def _get(session: Any, server_id: str) -> McpServerRecord | None:
        server: McpServerRecord | None = session.server
        return server if server is not None and server.id == server_id else None

    monkeypatch.setattr(mcp_servers_repo, "get", _get)
