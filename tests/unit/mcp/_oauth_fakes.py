"""Fakes for OAuth unit tests: token/state records and scripted ``mcp_oauth`` repo functions."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from src.db.repos import mcp_oauth as mcp_oauth_repo
from src.db.repos.mcp_oauth import McpOAuthStateRecord, McpOAuthTokenRecord

_WHEN = datetime(2026, 4, 20, tzinfo=UTC)

_DEFAULT_TOKEN = McpOAuthTokenRecord(
    id="tok1",
    mcp_server_id="srv1",
    user_id="user1",
    encrypted_access_token="enc-access",
    encrypted_refresh_token=None,
    expires_at=None,
    scope=None,
    token_type="Bearer",
    created_at=_WHEN,
    updated_at=_WHEN,
)

_DEFAULT_STATE = McpOAuthStateRecord(
    id="st1",
    mcp_server_id="srv1",
    user_id="user1",
    state="abc",
    code_verifier="ver123",
    redirect_uri="http://localhost:3000/cb",
    scope="read",
    expires_at=_WHEN,
    created_at=_WHEN,
)


def make_token(**overrides: Any) -> McpOAuthTokenRecord:
    return replace(_DEFAULT_TOKEN, **overrides)


def make_state(**overrides: Any) -> McpOAuthStateRecord:
    return replace(_DEFAULT_STATE, **overrides)


class RepoCalls:
    """Records the arguments of every scripted repo call, in order, with event logging."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events: list[str] = events if events is not None else []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def of(self, name: str) -> list[tuple[Any, ...]]:
        return [args for n, args in self.calls if n == name]


def script_repo(
    monkeypatch: pytest.MonkeyPatch,
    calls: RepoCalls,
    **results: Any,
) -> None:
    """Replace ``mcp_oauth`` repo functions. ``results`` maps a function name to its return value
    (a list is consumed one item per call; an Exception instance is raised). Unscripted names
    are left alone. Each call is logged as ``db:<name>`` in ``calls.events``."""

    def _make(name: str, scripted: Any) -> Any:
        queue = list(scripted) if isinstance(scripted, list) else None

        async def _fn(session: Any, *args: Any) -> Any:
            calls.events.append(f"db:{name}")
            calls.calls.append((name, args))
            value = queue.pop(0) if queue is not None else scripted
            if isinstance(value, Exception):
                raise value
            return value

        return _fn

    for name, scripted in results.items():
        monkeypatch.setattr(mcp_oauth_repo, name, _make(name, scripted))
