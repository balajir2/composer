"""Tests for McpExecutor (the 'mcp' node type)."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.state import initial_state
from src.engine.workflow import McpNode
from src.executors.mcp import McpExecutor


def _mcp_node(
    *,
    mcp_server_id: str | None = "srv1",
    tool_name: str | None = "echo",
    arguments: dict[str, Any] | None = None,
) -> McpNode:
    data: dict[str, Any] = {"label": "MCP"}
    if mcp_server_id:
        data["mcpServerId"] = mcp_server_id
    if tool_name:
        data["toolName"] = tool_name
    if arguments is not None:
        data["arguments"] = arguments
    return McpNode.model_validate(
        {"id": "m", "type": "mcp", "position": {"x": 0, "y": 0}, "data": data}
    )


@pytest.fixture(autouse=True)
def _reset_db_ctx() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    from src.engine.context import _current_db  # pyright: ignore[reportPrivateUsage]

    token = _current_db.set(None)
    yield
    _current_db.reset(token)


async def test_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tool = MagicMock()
    fake_tool.ainvoke = AsyncMock(return_value="echoed: hello")

    import src.engine.context as ctx_mod
    import src.executors.mcp as mcp_exec_mod

    fake_db = MagicMock()
    ctx_mod.set_current_db(fake_db)

    async def _fake_resolve(
        mcp_server_id: str, tool_name: str, user_id: Any, db: Any
    ) -> tuple[Any, Any]:
        return MagicMock(), fake_tool

    monkeypatch.setattr(mcp_exec_mod, "resolve_single_mcp_tool", _fake_resolve)

    node = _mcp_node(arguments={"q": "hello"})
    state = initial_state()
    delta = await McpExecutor(node).arun(state)

    assert delta["variables"]["lastOutput"] == "echoed: hello"
    assert delta["current_node_id"] == "m"
    assert delta["node_results"]["m"]["status"] == "completed"


async def test_missing_server_id_raises() -> None:
    import src.engine.context as ctx_mod

    ctx_mod.set_current_db(MagicMock())
    node = _mcp_node(mcp_server_id=None)
    with pytest.raises(ValueError, match="mcpServerId"):
        await McpExecutor(node).arun(initial_state())


async def test_missing_tool_name_raises() -> None:
    import src.engine.context as ctx_mod

    ctx_mod.set_current_db(MagicMock())
    node = _mcp_node(tool_name=None)
    with pytest.raises(ValueError, match="toolName"):
        await McpExecutor(node).arun(initial_state())


async def test_variable_substitution_in_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Arguments containing {{...}} placeholders should be substituted."""
    fake_tool = MagicMock()
    captured: dict[str, Any] = {}

    async def _capture_and_return(args: dict[str, Any]) -> str:
        captured.update(args)
        return "ok"

    fake_tool.ainvoke = AsyncMock(side_effect=_capture_and_return)

    import src.engine.context as ctx_mod
    import src.executors.mcp as mcp_exec_mod

    ctx_mod.set_current_db(MagicMock())

    async def _fake_resolve(
        mcp_server_id: str, tool_name: str, user_id: Any, db: Any
    ) -> tuple[Any, Any]:
        return MagicMock(), fake_tool

    monkeypatch.setattr(mcp_exec_mod, "resolve_single_mcp_tool", _fake_resolve)

    node = _mcp_node(arguments={"q": "{{user_message}}"})
    state = initial_state()
    state["variables"]["user_message"] = "Hello world"
    await McpExecutor(node).arun(state)
    assert captured["q"] == "Hello world"


async def test_mcp_executor_is_registered() -> None:
    import src.executors.mcp  # noqa: F401  # pyright: ignore[reportUnusedImport]  # side-effect registration
    from src.executors.base import build_executor

    node = _mcp_node()
    executor = build_executor(node)
    assert isinstance(executor, McpExecutor)
