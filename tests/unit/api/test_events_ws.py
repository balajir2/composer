"""Unit tests for WebSocket event streaming (Phase 9a)."""

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.engine.events import ExecutionEventBus
from src.main import create_app
from src.security.jwt import create_access_token, create_refresh_token
from src.security.rate_limit import RateLimiter


def _execution_row(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": "exec-1",
        "workflowId": "wf-1",
        "userId": "user-1",
        "status": "running",
        "currentNodeId": None,
        "nodeResults": {},
        "variables": {},
        "input": None,
        "output": None,
        "error": None,
        "threadId": "thread-1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _build_app(execution: SimpleNamespace, user_role: str = "member") -> tuple[TestClient, Any]:
    app = create_app()
    db = MagicMock()
    db.workflowexecution.find_unique = AsyncMock(return_value=execution)
    db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id=execution.userId, role=SimpleNamespace(value=user_role))
    )
    app.state.db = db
    app.state.checkpointer = MagicMock()
    app.state.event_bus = ExecutionEventBus()
    app.state.rate_limiter = RateLimiter()
    return TestClient(app), app


def _token_for(user_id: str) -> str:
    return create_access_token(user_id=user_id)


def test_ws_accepts_owner_with_bearer_subprotocol() -> None:
    execution = _execution_row(status="running", userId="u1")
    client, _ = _build_app(execution)
    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        msg = ws.receive_text()
        data = json.loads(msg)
        assert data["type"] == "workflow_started"
        assert data["executionId"] == "exec-1"


def test_ws_closes_4401_on_missing_bearer() -> None:
    execution = _execution_row()
    client, _ = _build_app(execution)

    # Server closes with code 4401 before accept; Starlette's TestClient raises
    # WebSocketDisconnect when the server rejects without accepting.
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/executions/exec-1/ws"):
        pass


def test_ws_closes_4401_on_refresh_token() -> None:
    execution = _execution_row(userId="u1")
    client, _ = _build_app(execution)
    token = create_refresh_token("u1")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]),
    ):
        pass


def test_ws_closes_4403_for_non_owner() -> None:
    execution = _execution_row(status="running", userId="owner-u")
    client, _ = _build_app(execution, user_role="member")
    token = _token_for("different-u")
    with (
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]),
    ):
        pass


def test_ws_admin_bypass_on_non_owned_execution() -> None:
    execution = _execution_row(status="running", userId="owner-u")
    client, app = _build_app(execution, user_role="admin")
    # admin is not the owner, but role check bypasses
    token = _token_for("admin-u")
    # Seed admin user id on the role mock
    app.state.db.user.find_unique = AsyncMock(
        return_value=SimpleNamespace(id="admin-u", role=SimpleNamespace(value="admin"))
    )
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        data = json.loads(ws.receive_text())
        assert data["type"] == "workflow_started"


def test_ws_terminal_snapshot_closes() -> None:
    execution = _execution_row(status="completed", userId="u1")
    client, _ = _build_app(execution)
    token = _token_for("u1")
    with client.websocket_connect("/executions/exec-1/ws", subprotocols=["bearer", token]) as ws:
        data = json.loads(ws.receive_text())
        assert data["type"] == "workflow_completed"
        # server closes after terminal snapshot
