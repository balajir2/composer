"""Tests for the Agent executor — non-tool path, single provider mocked."""

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.executors.agent import AgentExecutor


def _agent_node(
    *,
    model: str = "anthropic/claude-3-5-haiku-latest",
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
