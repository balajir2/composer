"""Tests for TavilyProvider."""

import pytest
from pytest_httpx import HTTPXMock  # pyright: ignore[reportMissingImports]

from src.config import get_settings
from src.engine.state import initial_state
from src.engine.workflow import AgentNode
from src.tools.base import ApiKeyAuth, BuildContext
from src.tools.providers.tavily import MissingApiKeyError, TavilyProvider


def _agent_node() -> AgentNode:
    return AgentNode.model_validate(
        {
            "id": "a",
            "type": "agent",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A"},
        }
    )


def test_provider_metadata() -> None:
    p = TavilyProvider()
    assert p.name == "tavily"
    assert p.category == "standard"
    assert isinstance(p.auth, ApiKeyAuth)
    assert p.auth.env_var == "TAVILY_API_KEY"


async def test_tools_lists_search() -> None:
    tools = await TavilyProvider().tools()
    assert len(tools) == 1
    assert tools[0].name == "tavily_search"


async def test_build_tool_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    node = _agent_node()
    with pytest.raises(MissingApiKeyError):
        await TavilyProvider().build_tool(
            "tavily_search",
            BuildContext(node=node, state=initial_state()),
        )


async def test_build_tool_unknown_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    node = _agent_node()
    with pytest.raises(ValueError, match="no tool named"):
        await TavilyProvider().build_tool(
            "ghost",
            BuildContext(node=node, state=initial_state()),
        )


async def test_search_tool_arun_returns_markdown(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search",
        method="POST",
        json={
            "results": [
                {
                    "title": "Title 1",
                    "url": "https://example.com/1",
                    "content": "Body 1",
                    "score": 0.9,
                },
                {
                    "title": "Title 2",
                    "url": "https://example.com/2",
                    "content": "Body 2",
                    "score": 0.8,
                },
            ],
        },
    )
    node = _agent_node()
    tool = await TavilyProvider().build_tool(
        "tavily_search",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(query="what is the capital of France?")  # pyright: ignore[reportPrivateUsage]
    assert "Title 1" in result
    assert "https://example.com/1" in result
    assert "Body 1" in result


async def test_search_tool_http_error_returns_error_string(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search",
        method="POST",
        status_code=500,
        text="internal error",
    )
    node = _agent_node()
    tool = await TavilyProvider().build_tool(
        "tavily_search",
        BuildContext(node=node, state=initial_state()),
    )
    result = await tool._arun(query="foo")  # pyright: ignore[reportPrivateUsage]
    assert result.startswith("Error: Tavily search failed (HTTP 500)")


async def test_health_check_no_key_returns_not_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "")
    get_settings.cache_clear()
    status = await TavilyProvider().health_check()
    assert status.ok is False
    assert "TAVILY_API_KEY" in status.message


async def test_health_check_with_key_pings_tavily(
    monkeypatch: pytest.MonkeyPatch,
    httpx_mock: HTTPXMock,  # pyright: ignore[reportUnknownParameterType]
) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    get_settings.cache_clear()
    httpx_mock.add_response(
        url="https://api.tavily.com/search",
        method="POST",
        json={"results": []},
    )
    status = await TavilyProvider().health_check()
    assert status.ok is True
    assert "reachable" in status.message.lower() or "valid" in status.message.lower()
