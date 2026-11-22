"""Tests for the Agent executor — non-tool path, single provider mocked."""

import asyncio
from collections.abc import Iterator
from typing import Any, cast

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import BaseModel
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.executors.agent import AgentExecutor, unwrap_message_content
from src.telemetry.providers import configure_provider_metrics


@pytest.fixture(autouse=True)
def _disable_environment_langsmith_tracing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")


@pytest.fixture
def provider_metric_reader() -> Iterator[InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    configure_provider_metrics(provider)
    yield reader
    configure_provider_metrics(None)
    provider.shutdown()


def _provider_request_series(reader: InMemoryMetricReader) -> dict[tuple[str, str, str], int]:
    data = reader.get_metrics_data()
    assert data is not None
    request_metric = next(
        metric
        for resource_metrics in data.resource_metrics
        for scope in resource_metrics.scope_metrics
        for metric in scope.metrics
        if metric.name == "composer.provider.request"
    )
    series: dict[tuple[str, str, str], int] = {}
    for point in request_metric.data.data_points:
        assert point.attributes is not None
        attributes = cast("dict[str, str]", point.attributes)
        series[
            (
                attributes["provider"],
                attributes["operation"],
                attributes["result"],
            )
        ] = cast("Any", point).value
    return series


def _install_model(monkeypatch: pytest.MonkeyPatch, model: Any) -> None:
    from src.llm import providers

    def _build(_model_string: str, **_kwargs: Any) -> Any:
        return model

    monkeypatch.setattr(providers, "build_chat_model", _build)


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

    def _fake_build_chat_model(model_string: str, **kw: Any) -> FakeListChatModel:
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

    node = _agent_node()
    state = initial_state("Who are you?")
    delta = await AgentExecutor(node).arun(state)

    assert delta["variables"]["lastOutput"] == "Hello!"
    assert delta["current_node_id"] == "a"
    assert delta["node_results"]["a"]["status"] == "completed"
    assert delta["node_results"]["a"]["output"] == "Hello!"


async def test_agent_records_actual_llm_invocation(
    monkeypatch: pytest.MonkeyPatch,
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    fake = FakeListChatModel(responses=["private answer"])
    _install_model(monkeypatch, fake)

    result = await AgentExecutor(_agent_node(model="openai/private-model-name")).arun(
        initial_state("private prompt")
    )

    assert result["variables"]["lastOutput"] == "private answer"
    assert _provider_request_series(provider_metric_reader) == {
        ("openai", "invoke", "success"): 1,
    }


async def test_agent_records_main_and_structured_output_llm_invocations(
    monkeypatch: pytest.MonkeyPatch,
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")

    class _JsonFake:
        def __init__(self) -> None:
            self.calls: list[list[Any]] = []

        async def ainvoke(self, messages: list[Any]) -> AIMessage:
            self.calls.append(messages)
            return AIMessage(content='{"answer": 42}')

        def with_structured_output(self, schema: Any = None, **_kwargs: Any) -> "_JsonFake":
            return self

        def bind_tools(self, _tools: Any) -> "_JsonFake":
            return self

    fake = _JsonFake()
    _install_model(monkeypatch, fake)

    result = await AgentExecutor(
        _agent_node(model="openai/private-model-name", output_format="JSON")
    ).arun(initial_state("private prompt"))

    assert result["variables"]["lastOutput"] == {"answer": 42}
    assert len(fake.calls) == 2
    assert _provider_request_series(provider_metric_reader) == {
        ("openai", "invoke", "success"): 2,
    }


async def test_agent_cancellation_keeps_identity_and_records_bounded_result(
    monkeypatch: pytest.MonkeyPatch,
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    cancellation = asyncio.CancelledError("private prompt and provider message")

    class _CancelledFake:
        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            raise cancellation

    _install_model(monkeypatch, _CancelledFake())

    with pytest.raises(asyncio.CancelledError) as raised:
        await AgentExecutor(_agent_node(model="anthropic/private-model-name")).arun(
            initial_state("private prompt")
        )

    assert raised.value is cancellation
    assert _provider_request_series(provider_metric_reader) == {
        ("anthropic", "invoke", "cancellation"): 1,
    }


async def test_agent_provider_error_keeps_exception_identity_and_redacts_message(
    monkeypatch: pytest.MonkeyPatch,
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    request = httpx.Request("POST", "https://provider.example/private-path")
    response = httpx.Response(503, request=request)
    failure = httpx.HTTPStatusError("private response body", request=request, response=response)

    class _FailedFake:
        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            raise failure

    _install_model(monkeypatch, _FailedFake())

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await AgentExecutor(_agent_node(model="openai/private-model")).arun(
            initial_state("private prompt")
        )

    assert raised.value is failure
    series = _provider_request_series(provider_metric_reader)
    assert series == {("openai", "invoke", "provider_error"): 1}
    assert "private response body" not in repr(series)


async def test_agent_measures_standard_tool_once_without_recording_tool_data(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    from src.config import get_settings

    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search",
        method="POST",
        json={"results": []},
    )

    class _ToolCallingFake:
        def __init__(self) -> None:
            self.calls = 0

        def bind_tools(self, _tools: list[Any]) -> "_ToolCallingFake":
            return self

        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "tavily_search",
                            "args": {"query": "private query"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="private final answer")

    fake = _ToolCallingFake()
    from src.tools import registry
    from src.tools.providers.tavily import _TavilySearchTool  # pyright: ignore[reportPrivateUsage]

    _install_model(monkeypatch, fake)

    async def _resolve_standard_tool(_node: Any, _context: Any) -> list[Any]:
        return [_TavilySearchTool()]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_standard_tool)

    result = await AgentExecutor(
        _agent_node(model="openai/private-model-name", selected_tools=["tavily.search"])
    ).arun(initial_state("private prompt"))

    assert result["variables"]["lastOutput"] == "private final answer"
    assert _provider_request_series(provider_metric_reader) == {
        ("openai", "invoke", "success"): 2,
        ("tavily", "tool", "success"): 1,
    }
    assert "private query" not in repr(_provider_request_series(provider_metric_reader))


async def test_agent_skips_generic_metric_for_mcp_bound_tool(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
    provider_metric_reader: InMemoryMetricReader,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    httpx_mock.add_response(
        url="https://mcp.example.com/rpc",
        method="POST",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "MCP result"}]},
        },
    )

    class _McpInput(BaseModel):
        query: str

    class _ToolCallingFake:
        def __init__(self) -> None:
            self.calls = 0

        def bind_tools(self, _tools: list[Any]) -> "_ToolCallingFake":
            return self

        async def ainvoke(self, _messages: list[Any]) -> AIMessage:
            self.calls += 1
            if self.calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "private-mcp-tool",
                            "args": {"query": "private"},
                            "id": "call-1",
                            "type": "tool_call",
                        }
                    ],
                )
            return AIMessage(content="done")

    fake = _ToolCallingFake()
    from src.mcp.base import _McpBoundTool  # pyright: ignore[reportPrivateUsage]
    from src.mcp.client import MCPClient
    from src.tools import registry

    _install_model(monkeypatch, fake)

    async def _resolve_mcp_tool(_node: Any, _context: Any) -> list[Any]:
        return [
            _McpBoundTool(
                client=MCPClient("https://mcp.example.com/rpc"),
                tool_name="private-mcp-tool",
                description="private description",
                args_schema=_McpInput,
            )
        ]

    monkeypatch.setattr(registry, "resolve_tools_for_node", _resolve_mcp_tool)

    result = await AgentExecutor(
        _agent_node(model="openai/private-model-name", selected_tools=["mcp.private"])
    ).arun(initial_state("private prompt"))

    assert result["variables"]["lastOutput"] == "done"
    assert _provider_request_series(provider_metric_reader) == {
        ("openai", "invoke", "success"): 2,
        ("other", "tool", "success"): 1,
    }


@pytest.mark.parametrize("telemetry_enabled", [True, False])
async def test_agent_preserves_langsmith_config_with_telemetry_on_or_off(
    monkeypatch: pytest.MonkeyPatch,
    provider_metric_reader: InMemoryMetricReader,
    telemetry_enabled: bool,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    from src.engine.context import LangSmithConfig
    from src.executors import agent as agent_module
    from src.llm import providers

    config = LangSmithConfig(
        tracing_v2=True,
        project="private-project",
        endpoint="https://langsmith.example",
        api_key="private-key",
    )
    monkeypatch.setattr(agent_module, "get_current_langsmith", lambda: config)
    observed: list[LangSmithConfig | None] = []
    fake = FakeListChatModel(responses=["answer"])

    def _build(
        _model_string: str, *, langsmith_config: LangSmithConfig | None, **_kwargs: Any
    ) -> Any:
        observed.append(langsmith_config)
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _build)
    if not telemetry_enabled:
        configure_provider_metrics(None)

    result = await AgentExecutor(_agent_node(model="openai/private-model")).arun(
        initial_state("private prompt")
    )

    assert result["variables"]["lastOutput"] == "answer"
    assert observed == [config]
    if telemetry_enabled:
        assert _provider_request_series(provider_metric_reader) == {
            ("openai", "invoke", "success"): 1,
        }
    else:
        assert provider_metric_reader.get_metrics_data() is None


async def test_agent_renders_instructions_via_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Agent executor must call variable_substitution.substitute on instructions."""
    fake = FakeListChatModel(responses=["ok"])
    from src.llm import providers

    def _fake_build_chat_model(model_string: str, **kw: Any) -> FakeListChatModel:
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

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

    def _fake_build_chat_model(model_string: str, **kw: Any) -> _JsonFake:
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

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
    from langchain_core.tools import tool  # pyright: ignore[reportUnknownVariableType]

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

    def _fake_build_chat_model(model_string: str, **kw: Any) -> _ToolCallingFake:
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

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
    from langchain_core.tools import tool  # pyright: ignore[reportUnknownVariableType]

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

    def _fake_build_chat_model(model_string: str, **kw: Any) -> _InfiniteLoopFake:
        return fake

    monkeypatch.setattr(providers, "build_chat_model", _fake_build_chat_model)

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
    blocks: list[dict[str, Any]] = [
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
