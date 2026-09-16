"""Tests for the Agent executor — non-tool path, single provider mocked."""

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.executors.agent import AgentExecutor, unwrap_message_content


def _agent_node(
    *,
    model: str = "anthropic/claude-haiku-4-5-20251001",
    instructions: str = "Say hi.",
    output_format: str = "Text",
    selected_tools: list[str] | None = None,
) -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "Agent",
                "model": model,
                "instructions": instructions,
                "outputFormat": output_format,
                "selectedTools": selected_tools or [],
            },
        }
    )


async def test_agent_text_output_no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeListChatModel(responses=["Hello!"])
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    node = _agent_node()
    state = initial_state("Who are you?")
    delta = await AgentExecutor(node).arun(state)

    assert delta["variables"]["lastOutput"] == "Hello!"
    assert delta["current_node_id"] == "a"
    assert delta["node_results"]["a"]["status"] == "completed"
    assert delta["node_results"]["a"]["output"] == "Hello!"


async def test_agent_renders_instructions_via_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent executor must call variable_substitution.substitute on instructions."""
    fake = FakeListChatModel(responses=["ok"])
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    node = _agent_node(instructions="Hello, {{user.name}}")
    state = initial_state()
    state["variables"]["user"] = {"name": "Ada"}
    delta = await AgentExecutor(node).arun(state)
    assert "Ada" in str(delta["node_results"]["a"].get("input", ""))


async def test_agent_json_output_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """When outputFormat=JSON, the return value is a parsed dict."""
    # FakeListChatModel.with_structured_output + json_mode doesn't fully simulate
    # real providers — patch in a simple fake that returns the parsed dict directly.
    from langchain_core.messages import AIMessage

    class _JsonFake:
        async def ainvoke(self, messages: Any) -> AIMessage:
            return AIMessage(content='{"answer": 42}')

        def with_structured_output(self, schema: Any = None, **kw: Any) -> Any:
            # Return self so the structured_invoke path goes through ainvoke.
            return self

        def bind_tools(self, tools: Any) -> "_JsonFake":
            return self

    fake = _JsonFake()
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    node = _agent_node(output_format="JSON")
    state = initial_state("give JSON")
    delta = await AgentExecutor(node).arun(state)
    assert delta["variables"]["lastOutput"] == {"answer": 42}


async def test_agent_is_registered() -> None:
    """build_executor must resolve 'agent' to AgentExecutor."""
    from src.executors.base import build_executor

    node = _agent_node()
    executor = build_executor(node)
    assert isinstance(executor, AgentExecutor)


async def test_agent_calls_tool_and_loops_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First LLM turn returns a tool_call; tool returns text; second turn returns final text."""
    from langchain_core.tools import tool

    @tool
    async def echo_tool(message: str) -> str:
        """Echo the given message."""
        return f"echoed: {message}"

    class _ToolCallingFake:
        """Fake chat model: first invoke returns a tool_call, second returns text."""

        def __init__(self) -> None:
            self.call_count = 0

        def bind_tools(self, tools: list[Any]) -> "_ToolCallingFake":
            return self

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            self.call_count += 1
            if self.call_count == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "echo_tool",
                            "args": {"message": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="Tool said: echoed: hello")

    fake = _ToolCallingFake()
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    # Register a test-scoped provider with echo_tool
    from src.tools import registry

    async def _resolve_stub(node: Any, context: Any) -> list[Any]:
        return [echo_tool]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_stub)

    node = _agent_node(selected_tools=["fake.echo"])
    state = initial_state("say hello")
    delta = await AgentExecutor(node).arun(state)
    assert "echoed: hello" in delta["variables"]["lastOutput"]
    assert fake.call_count == 2


async def test_agent_respects_max_iterations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM keeps emitting tool calls, we cap at MAX_ITERATIONS."""
    from langchain_core.tools import tool

    @tool
    async def always_tool() -> str:
        """Echo."""
        return "."

    class _InfiniteLoopFake:
        def bind_tools(self, tools: list[Any]) -> "_InfiniteLoopFake":
            return self

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "always_tool",
                        "args": {},
                        "id": f"call_{len(messages)}",
                        "type": "tool_call",
                    }
                ],
            )

    fake = _InfiniteLoopFake()
    from src.llm import providers

    monkeypatch.setattr(providers, "build_chat_model", lambda *a, **kw: fake)  # pyright: ignore[reportUnknownLambdaType]

    from src.tools import registry

    async def _resolve_stub(node: Any, context: Any) -> list[Any]:
        return [always_tool]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_stub)

    node = _agent_node(selected_tools=["fake.always"])
    state = initial_state("loop")
    from src.executors.agent import MaxIterationsExceededError

    with pytest.raises(MaxIterationsExceededError, match="max_iterations=10"):
        await AgentExecutor(node).arun(state)


@pytest.mark.parametrize(
    "provider,model",
    [
        ("anthropic", "anthropic/claude-3-5-haiku-latest"),
        ("openai", "openai/gpt-5-nano"),
        ("google", "google/gemini-2.0-flash"),
        ("groq", "groq/openai/gpt-oss-120b"),
    ],
)
async def test_agent_runs_for_each_provider(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    model: str,
) -> None:
    """Provider dispatch is verified; the actual LLM call is mocked."""
    expected = f"hello from {provider}"
    fake = FakeListChatModel(responses=[expected])

    captured: dict[str, str] = {}

    def _fake_build(model_string: str, **kwargs: Any) -> Any:
        captured["model"] = model_string
        return fake

    import src.llm.providers as providers

    monkeypatch.setattr(providers, "build_chat_model", _fake_build)

    node = _agent_node(model=model)
    delta = await AgentExecutor(node).arun(initial_state("test"))
    assert delta["variables"]["lastOutput"] == expected
    assert captured["model"] == model


def test_unwrap_message_content_plain_string() -> None:
    assert unwrap_message_content("hello") == "hello"


def test_unwrap_message_content_text_block_array() -> None:
    """Anthropic-style content blocks unwrap to clean text."""
    blocks = [
        {"type": "text", "text": "First sentence.", "extras": {"signature": "abc"}},
        {"type": "text", "text": "Second sentence."},
    ]
    assert unwrap_message_content(blocks) == "First sentence.\nSecond sentence."


def test_unwrap_message_content_skips_non_text_blocks() -> None:
    """Thinking/tool_use blocks are dropped — only text survives."""
    blocks = [
        {"type": "thinking", "thinking": "internal monologue"},
        {"type": "text", "text": "answer"},
        {"type": "tool_use", "name": "search", "input": {}},
    ]
    assert unwrap_message_content(blocks) == "answer"


def test_unwrap_message_content_empty_list_yields_empty_string() -> None:
    assert unwrap_message_content([]) == ""


def test_unwrap_message_content_handles_string_items() -> None:
    """Some providers return plain strings interleaved with dicts."""
    blocks = ["hello", {"type": "text", "text": "world"}]
    assert unwrap_message_content(blocks) == "hello\nworld"


def test_unwrap_message_content_none_returns_empty() -> None:
    assert unwrap_message_content(None) == ""
