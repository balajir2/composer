"""Shared fakes for resolver tests: a sessionmaker whose sessions carry one scripted server row."""

from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.repos import mcp_servers as mcp_servers_repo
from src.db.repos.mcp_servers import McpServerRecord
from tests.unit.db_fakes import FakeSession, FakeSessionmaker


def make_server(**overrides: Any) -> McpServerRecord:
    when = datetime(2026, 4, 20, tzinfo=UTC)
    base: dict[str, Any] = {
        "id": "srv1",
        "user_id": "dev",
        "name": "T",
        "url": "https://m.example.com/rpc",
        "description": None,
        "category": None,
        "auth_type": "none",
        "encrypted_access_token": None,
        "header_name": None,
        "oauth_config": None,
        "tools": None,
        "connection_status": "untested",
        "last_tested": None,
        "last_error": None,
        "enabled": True,
        "is_official": False,
        "is_shared": False,
        "headers": None,
        "created_at": when,
        "updated_at": when,
        "original_owner_email": None,
    }
    base.update(overrides)
    return McpServerRecord(**base)


class _ServerSession(FakeSession):
    server: McpServerRecord | None = None


def sessionmaker_with(server: McpServerRecord | None) -> FakeSessionmaker:
    session = _ServerSession()
    session.server = server
    return FakeSessionmaker(session)


def install_server_get(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make mcp_servers_repo.get answer with the server carried by the fake session."""

    async def _get(session: Any, server_id: str) -> McpServerRecord | None:
        del server_id
        return session.server

    monkeypatch.setattr(mcp_servers_repo, "get", _get)
