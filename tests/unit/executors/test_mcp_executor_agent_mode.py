"""Behavioral tests for McpExecutor agent mode, the agentic loop and tool-error
containment (src/executors/mcp.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool

import src.executors.mcp as mcp_mod
from src.engine.context import _current_db, set_current_db  # pyright: ignore[reportPrivateUsage]
from src.engine.state import initial_state
from src.engine.workflow import McpNode
from src.executors.mcp import McpExecutor, McpMaxIterationsError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from langchain_core.language_models import BaseChatModel


@pytest.fixture(autouse=True)
def _reset_db_ctx() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    token = _current_db.set(None)
    yield
    _current_db.reset(token)


class _ScriptedChatModel:
    """Chat model fake that replays a scripted list of responses."""

    def __init__(self, responses: list[AIMessage]) -> None:
        self._responses = list(responses)
        self.bound_tools: list[BaseTool] | None = None
        self.seen: list[list[BaseMessage]] = []

    def bind_tools(self, tools: list[BaseTool]) -> _ScriptedChatModel:
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        self.seen.append(list(messages))
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


def _tool(name: str, fn: Any) -> BaseTool:
    return StructuredTool.from_function(  # pyright: ignore[reportUnknownMemberType]
        coroutine=fn, name=name, description=f"{name} tool"
    )


def _build_scripted_model(*_args: Any, **_kwargs: Any) -> _ScriptedChatModel:
    return _ScriptedChatModel([AIMessage(content="x")])


def _text(msg: BaseMessage) -> str:
    content = cast("Any", msg.content)  # pyright: ignore[reportUnknownMemberType]
    return str(content)


def _call(name: str, call_id: str = "c1", **args: Any) -> dict[str, Any]:
    return {"name": name, "id": call_id, "args": args}


def _node(**data: Any) -> McpNode:
    payload: dict[str, Any] = {"label": "MCP", "mcpServerId": "srv1", **data}
    return McpNode.model_validate(
        {"id": "m", "type": "mcp", "position": {"x": 0, "y": 0}, "data": payload}
    )


async def _run_loop(model: _ScriptedChatModel, tools: list[BaseTool], cap: int = 3) -> str:
    return await mcp_mod._agentic_loop(  # pyright: ignore[reportPrivateUsage]
        "m", cast("BaseChatModel", model), [HumanMessage(content="go")], tools, cap
    )


# ─── agentic loop ───────────────────────────────────────────────────────────


async def test_loop_returns_text_when_model_makes_no_tool_calls() -> None:
    model = _ScriptedChatModel([AIMessage(content="final answer")])
    assert await _run_loop(model, []) == "final answer"
    assert model.bound_tools is None  # no tools -> model is not bound


async def test_loop_executes_tool_calls_then_returns_final_text() -> None:
    async def _echo(text: str) -> str:
        return f"echo:{text}"

    model = _ScriptedChatModel(
        [AIMessage(content="", tool_calls=[_call("echo", text="hi")]), AIMessage(content="done")]  # pyright: ignore[reportArgumentType]
    )
    out = await _run_loop(model, [_tool("echo", _echo)])
    assert out == "done"
    second_turn = model.seen[1]
    tool_msgs = [m for m in second_turn if isinstance(m, ToolMessage)]
    assert [_text(m) for m in tool_msgs] == ["echo:hi"]
    assert tool_msgs[0].tool_call_id == "c1"


async def test_loop_runs_parallel_tool_calls_in_one_turn() -> None:
    async def _a() -> str:
        return "A"

    async def _b() -> str:
        return "B"

    model = _ScriptedChatModel(
        [
            AIMessage(content="", tool_calls=[_call("a", "1"), _call("b", "2")]),  # pyright: ignore[reportArgumentType]
            AIMessage(content="ok"),
        ]
    )
    await _run_loop(model, [_tool("a", _a), _tool("b", _b)])
    results = {m.tool_call_id: _text(m) for m in model.seen[1] if isinstance(m, ToolMessage)}
    assert results == {"1": "A", "2": "B"}


async def test_loop_raises_when_model_never_converges() -> None:
    forever = AIMessage(content="", tool_calls=[_call("nope")])  # pyright: ignore[reportArgumentType]
    model = _ScriptedChatModel([forever])
    with pytest.raises(McpMaxIterationsError, match="max_iterations=2"):
        await _run_loop(model, [], cap=2)
    assert len(model.seen) == 3  # cap + 1 model turns


async def test_loop_stringifies_non_string_content() -> None:
    model = _ScriptedChatModel([AIMessage(content=[{"type": "text", "text": "x"}])])
    out = await _run_loop(model, [])
    assert "x" in out


# ─── tool-error containment ─────────────────────────────────────────────────


async def test_unknown_tool_call_becomes_error_message_not_exception() -> None:
    msg = await mcp_mod._run_tool({}, _call("ghost"))  # pyright: ignore[reportPrivateUsage]
    assert isinstance(msg, ToolMessage)
    assert "tool 'ghost' is not registered" in _text(msg)
    assert msg.tool_call_id == "c1"


async def test_tool_exception_is_reported_back_to_model_with_type_and_message() -> None:
    async def _boom() -> str:
        raise TimeoutError("upstream timed out")

    msg = await mcp_mod._run_tool(  # pyright: ignore[reportPrivateUsage]
        {"boom": _tool("boom", _boom)}, _call("boom")
    )
    assert "raised" in _text(msg)
    assert "upstream timed out" in _text(msg)


# ─── executor agent mode ────────────────────────────────────────────────────


async def test_agent_mode_resolves_selected_tools_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def _echo(text: str) -> str:
        return text

    async def _fake_resolve(
        server_id: str, names: list[str] | None, *, user_id: str, db: Any
    ) -> list[BaseTool]:
        captured.update(server_id=server_id, names=names, user_id=user_id, db=db)
        return [_tool("echo", _echo)]

    model = _ScriptedChatModel(
        [AIMessage(content="", tool_calls=[_call("echo", text="v")]), AIMessage(content="fin")]  # pyright: ignore[reportArgumentType]
    )
    built: dict[str, Any] = {}

    def _fake_build(model_id: str, **kwargs: Any) -> _ScriptedChatModel:
        built["model_id"] = model_id
        return model

    monkeypatch.setattr(mcp_mod, "resolve_mcp_tools_by_names", _fake_resolve)
    monkeypatch.setattr(mcp_mod._providers, "build_chat_model", _fake_build)  # pyright: ignore[reportPrivateUsage]
    sentinel_db = object()
    set_current_db(sentinel_db)

    state = initial_state()
    state["user_id"] = "user-7"
    state["variables"]["topic"] = "cats"
    node = _node(instructions="Research {{topic}}", selectedToolNames=["echo"])
    delta = await McpExecutor(node).arun(state)

    assert captured == {
        "server_id": "srv1",
        "names": ["echo"],
        "user_id": "user-7",
        "db": sentinel_db,
    }
    assert built["model_id"] == mcp_mod.DEFAULT_MODEL
    assert delta["variables"]["lastOutput"] == "fin"
    assert delta["node_results"]["m"]["input"] == "Research cats"
    assert delta["node_results"]["m"]["status"] == "completed"


async def test_agent_mode_without_db_context_raises_runtime_error() -> None:
    node = _node(instructions="do it")
    with pytest.raises(RuntimeError, match="set_current_db"):
        await McpExecutor(node).arun(initial_state())


async def test_agent_mode_clamps_max_iterations_to_absolute_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_caps: list[int] = []

    async def _fake_resolve(*_a: Any, **_k: Any) -> list[BaseTool]:
        return []

    async def _fake_loop(node_id: str, model: Any, messages: Any, tools: Any, cap: int) -> str:
        seen_caps.append(cap)
        return "x"

    monkeypatch.setattr(mcp_mod, "resolve_mcp_tools_by_names", _fake_resolve)
    monkeypatch.setattr(mcp_mod, "_agentic_loop", _fake_loop)
    monkeypatch.setattr(
        mcp_mod._providers,  # pyright: ignore[reportPrivateUsage]
        "build_chat_model",
        _build_scripted_model,
    )
    set_current_db(object())
    await McpExecutor(_node(instructions="go", maxIterations=10_000)).arun(initial_state())
    assert seen_caps == [mcp_mod.ABSOLUTE_MAX_ITERATIONS]


async def test_deterministic_mode_propagates_tool_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(**_kw: Any) -> str:
        raise ConnectionError("mcp server unreachable")

    async def _fake_resolve(
        server_id: str, tool_name: str, *, user_id: str, db: Any
    ) -> tuple[Any, BaseTool]:
        return object(), _tool("t", _boom)

    monkeypatch.setattr(mcp_mod, "resolve_single_mcp_tool", _fake_resolve)
    set_current_db(object())
    with pytest.raises(ConnectionError, match="unreachable"):
        await McpExecutor(_node(toolName="t")).arun(initial_state())
